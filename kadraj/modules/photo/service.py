"""``PhotoService`` — Fotoğraf Üret'in **bütün durumu** (sayfalar yalnızca çizer; plan §0.5).

Akış: fikir → (LLM varsa) Türkçe fikri İngilizce alanlara ayrıştır/çevir → eksik **kritik** alanlar için sohbetle soru-cevap (LLM yoksa
8a ``missing_slots`` soruları) → hedef modele uyarlanmış istem önizlemesi → Midjourney'de **kopyala**, API'si olanlarda **doğrudan üret**
→ A/B galeri, geçmiş/favori (``prompts``), büyütme, fırçalı inpainting, stil aktarımı, YouTube kapağı. Çıktılar ``03_AI_Images``.

Kurallar: kullanıcı kendisi istemeden hiçbir üretim başlamaz (maliyet!); LLM/sağlayıcı yoksa ``SmartError`` kartı (çökme yok) ve elle
yolu (adaptör + kopyala) açık kalır; her çağrının maliyeti ``api_usage``'a yazılır (``AIGateway``).
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ...ai_engine import aistrings
from ...ai_engine.access import AIAccess, ServiceStatus
from ...ai_engine.pipeline import quota
from ...ai_engine.pipeline.gateway import MODEL_PROVIDER, AIGateway
from ...ai_engine.prompt_adapter import (ASPECTS, MODELS, STYLE_IDS, AdaptedPrompt, MissingSlot, PromptError, PromptSpec, adapt,
                                         missing_slots, resolve_style, style_library)
from ...ai_engine.providers.aicore import ai_err, extract_json, sniff_mime
from ...ai_engine.providers.install import install_all
from ...core import i18n
from ...core.contracts import PingResult, ProviderKind, SmartError, Topics
from ...core.errors import translate
from ...storage.folders import folder_for
from . import imageops

SLOT_FIELDS = ("subject", "lighting", "angle", "style", "aspect", "lens", "mood", "negatives")
EXTRACT_SYSTEM = (
    "You turn a user's image idea (any language, usually Turkish) into structured fields for a text-to-image prompt. "
    "Write every value in concise English. Leave a field as an empty string when the idea gives no hint for it — never invent details. "
    "'aspect' must be one of: {aspects} or empty. 'style' should be one of {styles} when it fits, otherwise a short English phrase. "
    "'negatives' is a list of things to avoid. Reply with JSON only.")
EXTRACT_SCHEMA = {"type": "object", "additionalProperties": False,
                  "properties": {"subject": {"type": "string"}, "lighting": {"type": "string"}, "angle": {"type": "string"},
                                 "style": {"type": "string"}, "aspect": {"type": "string"}, "lens": {"type": "string"},
                                 "mood": {"type": "string"}, "negatives": {"type": "array", "items": {"type": "string"}}},
                  "required": ["subject", "lighting", "angle", "style", "aspect", "lens", "mood", "negatives"]}
NEGATIVE_PRESETS = {"temiz": ("blurry", "low quality", "watermark", "text", "extra fingers"),
                    "portre": ("deformed hands", "extra limbs", "cross-eyed", "plastic skin"),
                    "mimari": ("distorted perspective", "people", "cars", "clutter")}


@dataclass
class Variation:
    id: int
    path: str
    provider: str
    model: str
    prompt: str
    negative: str = ""
    seed: Optional[int] = None
    label: str = ""                    # "A" | "B" | ""
    favorite: bool = False
    prompt_id: Optional[int] = None    # prompts tablosu satırı (geçmiş/favori)
    cost_usd: float = 0.0


@dataclass
class ChatTurn:
    role: str                          # "assistant" | "user"
    text: str
    slot: str = ""


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], done: Callable[[Any, Optional[BaseException]], None]) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.fn, self.done = fn, done

    def run(self) -> None:
        try:
            res, err = self.fn(), None
        except BaseException as exc:          # noqa: BLE001
            res, err = None, exc
        self.done(res, err)


class PhotoService(QObject):
    specChanged = Signal()
    chatChanged = Signal()
    variationsChanged = Signal()
    historyChanged = Signal()
    errorRaised = Signal(object)
    notice = Signal(str)
    busyChanged = Signal(bool)
    toolDone = Signal(str, str)                 # (araç, çıktı yolu)
    localChanged = Signal()
    aiChanged = Signal()                        # AI servis durumu değişti (durum çubuğu / kurulum kartı yeniler)
    _async_done = Signal(object, object, object)
    _local_done = Signal(object)

    def __init__(self, ctx: Any = None) -> None:
        super().__init__(ctx if isinstance(ctx, QObject) else None)
        self.ctx = ctx
        self.spec = PromptSpec()
        self.idea = ""
        self.idea_en = ""
        self.translated = False
        self.model = "flux"
        self.chat: List[ChatTurn] = []
        self.asked: List[str] = []
        self.variations: List[Variation] = []
        self.last_adapted: Optional[AdaptedPrompt] = None
        self.last_error: Optional[SmartError] = None
        self.gateway: Optional[AIGateway] = None
        self.access: Optional[AIAccess] = None
        self._last_op: Optional[Tuple[Callable[[], Any], Callable[[Any], None], Optional[Callable[[BaseException], None]]]] = None
        self._next_id = 1
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._busy = 0
        self.local_up: Dict[str, bool] = {}
        self._async_done.connect(self._finish_async)
        self._local_done.connect(self._on_local)

    # -- yaşam döngüsü -------------------------------------------------------------------------------
    def start(self, ctx: Any) -> None:
        self.ctx = ctx
        i18n.register_strings(aistrings.STRINGS)
        install_all(ctx.providers, settings=ctx.settings)
        self.gateway = AIGateway(ctx.providers, ctx.vault, ctx.settings, db=lambda: ctx.project.db if ctx.project.is_open else None)
        self.gateway.local_up = self.local_up
        self.access = AIAccess(ctx.providers, ctx.vault, ctx.settings)
        self.model = str(ctx.settings.get("photo.model") or self.model)
        self.refresh_local()

    def stop(self) -> None:
        self._pool.waitForDone(2000)

    def refresh_local(self) -> None:
        """Yerel servisleri (ComfyUI, Ollama) arka planda yoklar; bilinmeyen/kapalıysa Midjourney "kopyala" yolu öne çıkar."""
        if self.ctx is None:
            return
        reg = self.ctx.providers

        access = self.access

        def probe() -> Dict[str, str]:
            return access.probe_local(2.0) if access is not None else {}
        self._pool.start(_Task(probe, lambda res, err: self._local_done.emit(res if err is None else {})))

    def _on_local(self, res: Dict[str, str]) -> None:
        if self.access is not None:
            self.access.set_local_state(res)
            self.local_up.update(self.access.local_up_map())     # kapalı yerel servis False → zincirde denenmez
        self.localChanged.emit()
        self.specChanged.emit()
        self.aiChanged.emit()

    # -- AI servis durumu / kurulum ---------------------------------------------------------------------------
    def ai_status(self, kind: str) -> ServiceStatus:
        return self.access.status(kind) if self.access is not None else ServiceStatus(kind, "no_key")

    def connect_provider(self, provider_id: str, secret: str, on_done: Callable[[PingResult], None]) -> None:
        """Kurulum kartı: anahtarı kasaya yaz + tek ucuz gerçek çağrı yap (arka planda). Anahtar bellekte tutulmaz, loglanmaz."""
        access = self.access
        if access is None:
            on_done(PingResult(False, None, i18n.t("ai.err.no_llm.summary"), ai_err("no_llm")))
            return

        def done(res: PingResult) -> None:
            self.aiChanged.emit()
            self.specChanged.emit()
            on_done(res)
        self.run_async(lambda: access.save_and_test(provider_id, secret), done, busy=False, remember=False)

    def retry_last(self) -> None:
        """Hata kartındaki "Yeniden dene": son arka plan işini aynen yeniden çalıştırır."""
        if self._last_op is not None:
            fn, on_done, on_error = self._last_op
            self.run_async(fn, on_done, on_error=on_error)

    @property
    def project_open(self) -> bool:
        return bool(self.ctx is not None and self.ctx.project.is_open)

    def gw(self) -> AIGateway:
        if self.gateway is None:
            raise ai_err("no_llm")
        return self.gateway

    def out_dir(self) -> Path:
        return Path(self.ctx.project.subfolder(folder_for("ai_image"))) if self.project_open else Path.home() / "Gorseller"

    def _fail(self, exc: BaseException) -> None:
        err = exc if isinstance(exc, SmartError) else translate(exc, {"source": "photo"})
        self.last_error = err
        self.errorRaised.emit(err)

    # -- arka plan yardımcısı -----------------------------------------------------------------------------
    def run_async(self, fn: Callable[[], Any], on_done: Callable[[Any], None], busy: bool = True, *, remember: bool = True,
                  on_error: Optional[Callable[[BaseException], None]] = None) -> None:
        if remember:
            self._last_op = (fn, on_done, on_error)
        if busy:
            self._busy += 1
            self.busyChanged.emit(True)
        self._pool.start(_Task(fn, lambda res, err: self._async_done.emit((on_done, on_error, busy), res, err)))

    def _finish_async(self, handlers: Any, res: Any, err: Any) -> None:
        on_done, on_error, busy = handlers
        if busy:
            self._busy = max(0, self._busy - 1)
            if self._busy == 0:
                self.busyChanged.emit(False)
        if err is not None:
            if on_error is not None:
                try:
                    on_error(err)
                except Exception:                      # noqa: BLE001 — yedek yol hata verirse asıl hata kartı yine gösterilir
                    pass
            self._fail(err)
        else:
            on_done(res)

    @property
    def busy(self) -> bool:
        return self._busy > 0

    # ================================================================================== fikir + sohbet
    def style_choices(self) -> List[Tuple[str, str]]:
        lib = style_library()
        out = [(sid, str(lib[sid].get("tr") or lib[sid].get("name") or sid)) for sid in STYLE_IDS if sid in lib]
        custom = (self.ctx.settings.get("photo.custom_styles") if self.ctx else None) or {}
        return out + [(f"custom:{k}", k) for k in dict(custom)]

    def negative_presets(self) -> Dict[str, Tuple[str, ...]]:
        custom = (self.ctx.settings.get("photo.custom_negatives") if self.ctx else None) or {}
        return {**NEGATIVE_PRESETS, **{k: tuple(v) for k, v in dict(custom).items()}}

    def add_custom_style(self, name: str, fragment: str) -> None:
        if self.ctx is None or not name.strip() or not fragment.strip():
            return
        cur = dict(self.ctx.settings.get("photo.custom_styles") or {})
        cur[name.strip()] = fragment.strip()
        self.ctx.settings.set("photo.custom_styles", cur)

    def add_custom_negative(self, name: str, items: Sequence[str]) -> None:
        if self.ctx is None or not name.strip():
            return
        cur = dict(self.ctx.settings.get("photo.custom_negatives") or {})
        cur[name.strip()] = [i.strip() for i in items if i.strip()]
        self.ctx.settings.set("photo.custom_negatives", cur)

    def apply_negative_preset(self, name: str) -> None:
        items = self.negative_presets().get(name, ())
        self.set_slot("negatives", ", ".join(dict.fromkeys([*self.spec.negatives, *items])))

    def _style_text(self, style_id: str) -> str:
        if style_id.startswith("custom:") and self.ctx is not None:
            return str(dict(self.ctx.settings.get("photo.custom_styles") or {}).get(style_id[7:], style_id[7:]))
        return style_id

    def set_slot(self, slot: str, value: str) -> None:
        if slot not in SLOT_FIELDS:
            raise PromptError(f"Bilinmeyen alan: {slot}")
        if slot == "style":
            value = self._style_text(value)
        data = {f: getattr(self.spec, f) for f in ("subject", "lighting", "angle", "style", "aspect", "lens", "mood", "idea")}
        negs = self.spec.negatives
        if slot == "negatives":
            negs = tuple(x.strip() for x in str(value).split(",") if x.strip())
        else:
            data[slot] = value.strip()
        self.spec = PromptSpec.create(negatives=negs, **data)
        self.specChanged.emit()

    def set_model(self, model: str) -> None:
        if model in MODELS:
            self.model = model
            if self.ctx is not None:
                self.ctx.settings.set("photo.model", model)
            self.specChanged.emit()

    def reset(self) -> None:
        self.spec, self.idea, self.idea_en, self.translated = PromptSpec(), "", "", False
        self.chat, self.asked = [], []
        self.specChanged.emit()
        self.chatChanged.emit()

    def submit_idea(self, text: str, *, use_llm: bool = True) -> None:
        """Fikri alır. LLM varsa **arka planda** alanlara ayrıştırıp İngilizceye çevirir; yoksa metin olduğu gibi konu olur (8a kararı: çeviri yok)."""
        text = " ".join((text or "").split())
        if not text:
            return
        self.reset()
        self.idea = text
        self.chat.append(ChatTurn("user", text))
        if use_llm and self.gateway is not None and self.gateway.has_llm():                 # bilinen kapalı Ollama sayılmaz
            # yapay zekâ yardımcısı isteğe bağlıdır: hata verirse fikir olduğu gibi konu olur, soru-cevap yine ilerler (kart nedenini söyler)
            self.run_async(lambda: self._extract(text), lambda fields: self._apply_extract(text, fields),
                           on_error=lambda _exc: self._plain_idea(text))
        else:
            self.spec = PromptSpec.create(subject=text, idea=text)
            self._ask_next()
            self.specChanged.emit()

    def _plain_idea(self, text: str) -> None:
        self.spec = PromptSpec.create(subject=text, idea=text)
        self._ask_next()
        self.specChanged.emit()

    def _extract(self, text: str) -> Dict[str, Any]:
        system = EXTRACT_SYSTEM.format(aspects=", ".join(ASPECTS), styles=", ".join(STYLE_IDS))
        res = self.gw().llm(f"Idea: {text}", system=system, tier="cheap", json_schema=EXTRACT_SCHEMA, want_json=True, max_tokens=1200)
        data = res.json()
        if not isinstance(data, dict):
            raise ai_err("invalid_json")
        return data

    def _apply_extract(self, text: str, data: Dict[str, Any]) -> None:
        def clean(key: str) -> str:
            return " ".join(str(data.get(key) or "").split())
        aspect = clean("aspect")
        negs = [str(x) for x in (data.get("negatives") or []) if str(x).strip()]
        try:
            self.spec = PromptSpec.create(subject=clean("subject") or text, lighting=clean("lighting"), angle=clean("angle"),
                                          style=clean("style"), aspect=aspect if aspect in ASPECTS else "", lens=clean("lens"),
                                          mood=clean("mood"), negatives=negs, idea=text)
        except PromptError:
            self.spec = PromptSpec.create(subject=clean("subject") or text, idea=text)
        self.translated = clean("subject") != "" and clean("subject") != text
        self._ask_next()
        self.specChanged.emit()

    # -- soru-cevap ---------------------------------------------------------------------------------------
    def missing(self) -> List[MissingSlot]:
        return missing_slots(self.spec)

    def ready(self) -> bool:
        return not any(m.critical for m in self.missing())

    def current_question(self) -> Optional[MissingSlot]:
        for m in self.missing():
            if m.critical and m.name not in self.asked:
                return m
        return None

    def _ask_next(self) -> None:
        q = self.current_question()
        if q is not None:
            self.asked.append(q.name)
            self.chat.append(ChatTurn("assistant", q.question, q.name))
        self.chatChanged.emit()

    def answer(self, text: str) -> None:
        """Son sorulan alana yanıt verir; LLM varsa yanıtı İngilizceye çevirir (yoksa olduğu gibi). Sonra sıradaki soru."""
        text = " ".join((text or "").split())
        slot = next((t.slot for t in reversed(self.chat) if t.role == "assistant" and t.slot), "")
        if not text or not slot:
            return
        self.chat.append(ChatTurn("user", text, slot))

        def apply(value: str) -> None:
            try:
                if slot == "style":
                    self.set_slot("style", resolve_style(value) or value)
                else:
                    self.set_slot(slot, value)
            except PromptError as exc:
                self._fail(exc)
            self._ask_next()
        if slot in ("aspect",) or not (self.gateway and self.gateway.has_llm()):
            apply(text)
        else:
            self.run_async(lambda: self.translate_text(text), apply)

    def skip_question(self) -> None:
        self._ask_next()

    def translate_text(self, text: str) -> str:
        """Türkçe kısa ifadeyi İngilizceye çevirir (LLM; ucuz katman). Hata olursa özgün metin."""
        try:
            res = self.gw().llm(f"Translate to concise English for an image prompt. Reply with the translation only.\n\n{text}",
                                tier="cheap", max_tokens=200)
            return " ".join(res.text.split()) or text
        except SmartError:
            return text

    # ================================================================================== önizleme + üretim
    def preview(self, model: Optional[str] = None) -> AdaptedPrompt:
        self.last_adapted = adapt(model or self.model, self.spec)
        return self.last_adapted

    def action_for(self, model: Optional[str] = None) -> str:
        """``"copy"`` (Midjourney: API yok) ya da ``"generate"`` (API'si olan modeller)."""
        return "copy" if MODEL_PROVIDER.get(model or self.model) is None else "generate"

    def available_models(self) -> List[str]:
        """Şu an üretilebilen modeller (+ her zaman Midjourney: kopyala)."""
        out = ["midjourney"]
        if self.gateway is None:
            return out
        have = {p.id for p in self.gateway.chain(ProviderKind.IMAGE) if not p.local or self.local_up.get(p.id) is True}
        return [m for m in MODELS if MODEL_PROVIDER.get(m) is None or MODEL_PROVIDER[m] in have]

    def estimate_cost(self, n: int = 1) -> quota.PlanShare:
        est = quota.estimate(images=n, settings=self.ctx.settings if self.ctx else None)
        spent = quota.spent_this_month(self.ctx.project.db if self.project_open else None)
        return quota.plan_share(est, spent=spent, settings=self.ctx.settings if self.ctx else None)

    def generate(self, model: Optional[str] = None, n: int = 1, *, label: str = "", seed: Optional[int] = None,
                 on_done: Optional[Callable[[List[Variation]], None]] = None) -> None:
        """Üretir (kullanıcı eylemi): ``model`` Midjourney ise yalnızca istemi hazırlar (``SmartError`` yok, kopyalama arayüzde)."""
        model = model or self.model
        if MODEL_PROVIDER.get(model) is None:
            self.preview(model)
            return
        if not self.ready():
            self._fail(ai_err("bad_response", name="istem", technical="kritik alan eksik"))
            return
        spec = self.spec

        def job() -> List[Variation]:
            res, adapted = self.gw().image(spec, prefer_model=model, n=n, seed=seed)
            self.last_adapted = adapted
            out: List[Variation] = []
            for i, data in enumerate(res.images):
                ext = {"image/jpeg": ".jpg", "image/webp": ".webp"}.get(sniff_mime(data), ".png")       # sağlayıcı JPEG/WebP verebilir
                path = imageops.save_bytes(data, self.out_dir(), f"{imageops.stamp()}_{res.provider}_{i + 1}", ext)
                out.append(self._register(path, res.provider, adapted, res.seed, label, res.usage.cost_usd / max(1, len(res.images))))
            return out

        def done(vs: List[Variation]) -> None:
            self.variations = (vs + self.variations)[:48]
            self.variationsChanged.emit()
            self.historyChanged.emit()
            if on_done:
                on_done(vs)
        self.run_async(job, done)

    def generate_ab(self, models: Optional[Sequence[str]] = None) -> None:
        """A/B: iki farklı model (varsa) ya da aynı modelin iki tohumu; sonuçlar "A"/"B" etiketlenir."""
        cands = [m for m in (models or self.available_models()) if MODEL_PROVIDER.get(m)]
        pair = (list(cands) + [self.model, self.model])[:2] if len(cands) < 2 else cands[:2]
        results: List[Variation] = []

        def one(i: int) -> None:
            self.generate(pair[i], 1, label="AB"[i], seed=None if pair[0] != pair[1] else 1000 + i,
                          on_done=lambda vs: (results.extend(vs), None))
        one(0)
        one(1)

    def _register(self, path: Path, provider: str, adapted: AdaptedPrompt, seed: Optional[int], label: str, cost: float) -> Variation:
        pid = None
        if self.project_open:
            try:
                pid = self.ctx.project.db.add_prompt(model=adapted.model, idea=self.idea or self.spec.idea, prompt=adapted.text,
                                                     negative=adapted.negative,
                                                     params={**adapted.params, "path": str(path), "provider": provider, "seed": seed,
                                                             "aspect": self.spec.aspect})
            except Exception:
                pid = None
            try:
                self.ctx.project.db.add_media(str(path), "image", width=None, height=None, tags="ai")
            except Exception:
                pass
        v = Variation(self._next_id, str(path), provider, adapted.model, adapted.text, adapted.negative, seed, label, False, pid, cost)
        self._next_id += 1
        return v

    # -- galeri / favori / geçmiş --------------------------------------------------------------------------
    def toggle_favorite(self, variation_id: int) -> bool:
        v = next((x for x in self.variations if x.id == variation_id), None)
        if v is None:
            return False
        v.favorite = not v.favorite
        if v.prompt_id is not None and self.project_open:
            self.ctx.project.db.set_prompt_favorite(v.prompt_id, v.favorite)
        self.variationsChanged.emit()
        self.historyChanged.emit()
        return v.favorite

    def choose_ab(self, variation_id: int) -> None:
        """A/B'de beğenileni favoriye alır."""
        for v in self.variations:
            if v.id == variation_id and not v.favorite:
                self.toggle_favorite(v.id)

    def history(self, favorites_only: bool = False) -> List[Dict[str, Any]]:
        return self.ctx.project.db.list_prompts(favorites_only) if self.project_open else []

    def set_history_favorite(self, prompt_id: int, favorite: bool) -> None:
        if self.project_open:
            self.ctx.project.db.set_prompt_favorite(prompt_id, favorite)
            self.historyChanged.emit()

    def reuse(self, prompt_id: int) -> None:
        """Geçmişten bir fikri geri yükler."""
        row = next((r for r in self.history() if r["id"] == prompt_id), None)
        if row:
            self.idea = row["idea"]
            self.spec = PromptSpec.create(subject=row["idea"], idea=row["idea"], aspect=(row["params"].get("aspect") or ""))
            self.specChanged.emit()

    # ================================================================================== görselden istem
    def describe_image(self, path: str) -> None:
        """Beğenilen bir görselden istem çıkarır (vision modeli; yoksa ``ai.no_vision`` kartı)."""
        data = Path(path).read_bytes()
        prompt = ("Describe this image as a text-to-image prompt: subject, lighting, camera angle, style, mood, lens. "
                  "Answer in Turkish in 2-3 sentences so the user can edit it.")
        self.run_async(lambda: self.gw().vision(data, prompt, tier="cheap", max_tokens=600).text,
                       lambda text: self.submit_idea(text, use_llm=True))

    # ================================================================================== araçlar
    def upscale(self, path: str, factor: int = 2) -> None:
        """2×/4×: ComfyUI varsa onunla, yoksa yerel Lanczos."""
        src = Path(path)
        factor = 4 if factor >= 4 else 2

        def job() -> str:
            data = src.read_bytes()
            res = self.gw().upscale(data, factor)
            out = res.images[0] if res is not None and res.images else imageops.lanczos_upscale(data, factor)
            return str(imageops.save_bytes(out, src.parent, f"{src.stem}_x{factor}"))
        self.run_async(job, lambda p: self._tool_done("upscale", p))

    def inpaint(self, path: str, mask_png: bytes, prompt: str) -> None:
        """Fırçalı inpainting: ComfyUI → OpenAI düzenleme; sonuç yalnız maske içinde özgün görsele karıştırılır."""
        src = Path(path)

        def job() -> str:
            original = src.read_bytes()
            res = self.gw().edit_image(original, mask_png, prompt, negative=" ".join(self.spec.negatives))
            merged = imageops.composite_edit(original, res.images[0], mask_png)
            return str(imageops.save_bytes(merged, src.parent, f"{src.stem}_edit"))
        self.run_async(job, lambda p: self._tool_done("inpaint", p))

    def style_transfer(self, content_path: str, style: str, strength: float = 0.7) -> None:
        """Stil aktarımı: ``style`` bir görsel yolu ise renk/ton aktarımı (+ ComfyUI img2img varsa), stil kimliği ise img2img."""
        src = Path(content_path)

        def job() -> str:
            original = src.read_bytes()
            if Path(style).is_file():
                out = imageops.color_transfer(original, style, strength)
            else:
                text = adapt("sdxl", PromptSpec.create(subject=self.spec.subject or "scene", style=style)).text
                res = self.gw().img2img(original, text, strength=strength)
                if res is None or not res.images:
                    raise ai_err("edit_unavailable")
                out = res.images[0]
            return str(imageops.save_bytes(out, src.parent, f"{src.stem}_stil"))
        self.run_async(job, lambda p: self._tool_done("style", p))

    def make_cover(self, image_path: str, title: str, subtitle: str = "") -> str:
        """1280×720 YouTube kapağı (yerel, anında)."""
        data, ext = imageops.make_cover(image_path, title, subtitle)
        path = str(imageops.save_bytes(data, self.out_dir(), f"kapak_{imageops.stamp()}", ext))
        self._tool_done("cover", path)
        return path

    def auto_cover(self, title: str, subtitle: str = "") -> None:
        """Otomatik kapak: istemi 16:9'a çevirip bir görsel üretir, sonra kapağı kurar."""
        spec16 = replace(self.spec, aspect="16:9")
        old, self.spec = self.spec, spec16

        def after(vs: List[Variation]) -> None:
            self.spec = old
            if vs:
                self.make_cover(vs[0].path, title, subtitle)
        self.generate(self.model if MODEL_PROVIDER.get(self.model) else "flux", 1, on_done=after)

    def _tool_done(self, tool: str, path: str) -> None:
        if self.project_open:
            try:
                self.ctx.project.db.add_media(path, "image", tags=f"ai,{tool}")
            except Exception:
                pass
        self.toolDone.emit(tool, path)
        self.variationsChanged.emit()
