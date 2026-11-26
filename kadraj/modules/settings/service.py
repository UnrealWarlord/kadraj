"""Ayarlar servisi: API kasası, sağlayıcı testleri, yerel çalışma zamanları, araçlar, yollar, profiller, görünüm.

Durum burada tutulur; sayfalar yalnızca görünümdür. Ağ ve pip çağrıları arka plan iş
parçacığında çalışır; sonuçlar sinyalle ana iş parçacığına döner (arayüz donmaz).

Güvenlik: düz API anahtarı hiçbir yerde gösterilmez. Önizleme yalnızca ilk 6 karakter,
``…`` ve son 4 karakterdir (``mask_preview``). Anahtarlar kasada şifreli tutulur.

Saf yardımcılar (``mask_preview``, ``initials``, ``led_state``, ``parse_*``) modül
düzeyindedir ve doğrudan test edilir. Sınırlar: ağ (``http_get``), alt süreç (``run_cmd``,
``popen``) ve sağlayıcı pingi (``ping_fn``) testlerde enjekte edilir.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, Signal

from ... import device
from ...core import components, i18n
from ...core.contracts import Job, PingResult, SmartError, Topics, UiMode
from ...storage import cache, disk, folders, profiles
from .strings import K

UPDATE_KIND = "tools.ytdlp"
LOCAL_TIMEOUT = 2.0
PING_TIMEOUT = 5.0
UPDATE_TIMEOUT = 600.0
WHISPER_MODELS: Tuple[str, ...] = ("tiny", "base", "small", "medium", "large-v3")
A11Y_KEYS: Tuple[str, ...] = ("large_text", "high_contrast", "reduced_motion")
_KIND_KEYS = {
    "llm": K.KIND_LLM, "image": K.KIND_IMAGE, "video": K.KIND_VIDEO, "tts": K.KIND_TTS,
    "stt": K.KIND_STT, "stock": K.KIND_STOCK, "local": K.KIND_LOCAL,
}
_FFMPEG_RE = re.compile(r"ffmpeg version (\S+)")


# ---------------------------------------------------------------------------
# Saf yardımcılar
# ---------------------------------------------------------------------------
def mask_preview(secret: Any) -> str:
    """Düz anahtarın kısa önizlemesi: ilk 6 + ``…`` + son 4. Kısa anahtarda tamamen maskelenir."""
    value = str(secret or "")
    if len(value) < 20:
        return "••••••••"
    return f"{value[:6]}…{value[-4:]}"


def initials(name: Any) -> str:
    """Avatar harfleri: ilk iki sözcüğün baş harfi (``Anthropic (Claude)`` → ``AC``)."""
    parts = [p for p in re.split(r"[\s\-_()/]+", str(name or "").strip()) if p]
    letters = "".join(p[0] for p in parts[:2])
    return letters.upper() or "?"


def led_state(ok: Optional[bool], checking: bool = False) -> str:
    """Durum ışığı: ``checking`` | ``ok`` | ``fail`` | ``idle`` (henüz denenmedi)."""
    if checking:
        return "checking"
    if ok is None:
        return "idle"
    return "ok" if ok else "fail"


def parse_ollama_models(payload: Any) -> Tuple[str, ...]:
    """Ollama ``/api/tags`` yanıtından model adları. Beklenmeyen biçimde boş demet."""
    models = payload.get("models") if isinstance(payload, Mapping) else None
    if not isinstance(models, list):
        return ()
    names = [str(m.get("name") or m.get("model") or "") for m in models if isinstance(m, Mapping)]
    return tuple(n for n in names if n)


def parse_ffmpeg_version(text: Any) -> str:
    """``ffmpeg -version`` çıktısından sürüm (``7.1``). Bulunamazsa boş dize."""
    match = _FFMPEG_RE.search(str(text or ""))
    return match.group(1) if match else ""


def js_tool_key() -> str:
    """YouTube JS çözücüsü satırının etiketi: Android'de QuickJS (APK'da gelir), başka yerde Deno."""
    return K.TOOL_QUICKJS if device.is_android() else K.TOOL_DENO


def update_note_key() -> str:
    """Güncelleme notunun anahtarı: Android'de açılışta yalnız yt-dlp denetlenir (FFmpeg uygulamayla gelir)."""
    return K.ANDROID_TOOLS_NOTE if device.is_android() else K.UPDATE_STARTUP_NOTE


def repair_hint_key() -> str:
    """"Onar" açıklamasının anahtarı: Android'de yalnız yt-dlp yeniden indirilebilir."""
    return K.ANDROID_REPAIR_HINT if device.is_android() else K.REPAIR_HINT


def source_label(source: Any) -> str:
    """Bileşenin nereden geldiği (``core/components.py`` kaynağı) sade etiketi: Pakette / Güncellendi / Sistemde / Yok."""
    key = {"bundled": K.SOURCE_BUNDLED, "updated": K.SOURCE_UPDATED, "system": K.SOURCE_SYSTEM,
           "env": K.SOURCE_SYSTEM, "module": K.SOURCE_SYSTEM,
           "android": K.SOURCE_ANDROID}.get(str(source or ""), K.SOURCE_MISSING)
    return i18n.t(key)


def _ytdlp_version() -> str:
    """Kullanılacak yt-dlp sürümü: güncellenmiş/paketle gelen zipapp ya da kurulu paket (``core/components.py``)."""
    return components.ytdlp_version()


def _default_run(args: Sequence[str], timeout: float) -> Tuple[int, str]:
    try:
        done = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def _default_http_get(url: str, timeout: float) -> Any:
    import httpx                                   # tembel içe aktarma
    response = httpx.get(url, timeout=timeout, follow_redirects=False)
    response.raise_for_status()
    return response.json() if "json" in response.headers.get("content-type", "") else response.text


def _safe(fn: Callable[..., Any], *args: Any, default: Any = None) -> Any:
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 — kasa kilitliyken ya da bozukken arayüz çökmez
        return default


# ---------------------------------------------------------------------------
# Görünüm verisi
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class KeyRow:
    provider_id: str
    name: str
    kind: str
    usage: str
    has_key: bool
    masked: str
    model: str
    state: str                       # led_state
    latency_ms: Optional[float]
    message: str


@dataclass(frozen=True)
class LocalRuntime:
    provider_id: str
    name: str
    endpoint: str
    state: str                       # up | down | missing
    models: Tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class ToolInfo:
    ytdlp: str
    ffmpeg: str
    ffmpeg_path: str
    deno: str = ""
    #: bileşen → kaynak (``env|updated|bundled|system|module``; bulunamadıysa ``""``): "yt-dlp", "ffmpeg", "deno"
    sources: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FolderInfo:
    root: str
    free: int
    total: int
    used_fraction: float


class SettingsService(QObject):
    """``ctx.service("settings")``. ``changed(topic)``: "keys" | "local" | "tools" | "folder" | "fallback" | "profiles" | "view"."""

    name = "settings"
    changed = Signal(str)
    pingDone = Signal(str, object)           # provider_id, PingResult

    # Testler için enjeksiyon noktaları (None → gerçek uygulama).
    ping_fn: Optional[Callable[[str], PingResult]] = None
    http_get: Optional[Callable[[str, float], Any]] = None
    run_cmd: Optional[Callable[[Sequence[str], float], Tuple[int, str]]] = None
    popen: Optional[Callable[..., Any]] = None

    def __init__(self) -> None:
        super().__init__()
        self.ctx: Any = None
        self._status: Dict[str, Tuple[str, Optional[float], str]] = {}
        self._results: Dict[str, PingResult] = {}            # son "Bağlantıyı test et" sonuçları (sade Türkçe kart için)
        self._local: List[LocalRuntime] = []
        self._tools: Optional[ToolInfo] = None
        self._local_loaded = False
        self._tools_loaded = False
        self._unsubs: List[Callable[[], None]] = []

    # -- yaşam döngüsü -----------------------------------------------------
    def start(self, ctx: Any) -> None:
        self.ctx = ctx
        # "Bağlantıyı test et" gerçek sağlayıcıyla (ucuz model/kredi listesi) ve sade Türkçe sonuçla çalışsın: Fotoğraf/Dizi sayfaları
        # henüz açılmamış olsa bile AI sağlayıcıları kayıt defterine kurulur (idempotent; kasa/ağ'a dokunmaz).
        try:
            from ...ai_engine.providers.install import install_all
            install_all(ctx.providers, settings=ctx.settings)
        except Exception:  # noqa: BLE001 — AI paketi eksik/bozuksa Ayarlar yine açılır
            pass
        ctx.jobs.register_handler(UPDATE_KIND, self._handle_ytdlp_update)
        self._unsubs.append(ctx.bus.subscribe("components.update", lambda _p=None: self._safe_update()))
        self._unsubs.append(ctx.bus.subscribe(Topics.JOB_UPDATED, self._on_job_updated))

    def stop(self) -> None:
        for unsubscribe in self._unsubs:
            unsubscribe()
        self._unsubs.clear()
        self.ctx = None

    # -- bağlam erişimi ----------------------------------------------------
    @property
    def settings(self) -> Any:
        return self.ctx.settings

    def _vault(self) -> Any:
        return self.ctx.vault

    def _providers(self) -> Any:
        return self.ctx.providers

    def _run(self, args: Sequence[str], timeout: float) -> Tuple[int, str]:
        return (self.run_cmd or _default_run)(args, timeout)

    # -- API kasası --------------------------------------------------------
    def vault_state(self) -> Tuple[str, str]:
        """Kasa durumu ve kısa mesaj: ``ready`` | ``locked`` | ``empty`` | ``error``."""
        status = getattr(self._vault(), "status", None)
        if not callable(status):
            return "ready", ""
        st = _safe(status, default=None)
        if st is None:
            return "error", i18n.t(K.VAULT_ERROR)
        if st.state == "locked":
            return "locked", i18n.t(K.VAULT_LOCKED)
        if st.state == "error":
            return "error", st.error.summary if st.error else i18n.t(K.VAULT_ERROR)
        return st.state, ""

    def key_providers(self) -> List[Any]:
        # anahtarını başka bir servisle paylaşan servis (ör. OpenAI görsel → OpenAI) ayrı satır olmaz: kasada tek kayıt, tek satır
        return [p for p in self._providers().list()
                if p.needs_key and not p.local and getattr(p, "key_id", p.id) == p.id]

    def model_of(self, provider_id: str) -> str:
        if provider_id == "whisper_local":
            return self.whisper_model()
        return str(self.settings.get(f"providers.{provider_id}.model") or "")

    def key_rows(self) -> List[KeyRow]:
        vault = self._vault()
        rows: List[KeyRow] = []
        for p in self.key_providers():
            state, latency, message = self._status.get(p.id, ("idle", None, ""))
            kind = str(getattr(p.kind, "value", p.kind))
            rows.append(KeyRow(
                provider_id=p.id, name=p.display_name, kind=kind,
                usage=i18n.t(_KIND_KEYS.get(kind, K.KIND_LOCAL)),
                has_key=bool(_safe(vault.has_secret, p.id, default=False)),
                masked=str(_safe(vault.masked, p.id, default="") or ""),
                model=self.model_of(p.id), state=state, latency_ms=latency, message=message,
            ))
        return rows

    def save_key(self, provider_id: str, secret: Any) -> None:
        value = str(secret or "").strip()
        if not value:
            raise ValueError(i18n.t(K.VAULT_EMPTY_KEY))
        self._vault().set_secret(provider_id, value)        # kasa açılamazsa SmartError
        self._status.pop(provider_id, None)
        self._results.pop(provider_id, None)
        self.changed.emit("keys")

    def delete_key(self, provider_id: str) -> bool:
        removed = bool(self._vault().delete_secret(provider_id))
        self._status.pop(provider_id, None)
        self._results.pop(provider_id, None)
        self.changed.emit("keys")
        return removed

    def key_preview(self, provider_id: str) -> str:
        """Göz düğmesi için: yalnızca ``mask_preview`` çıktısı. Tam anahtar döndürülmez."""
        secret = _safe(self._vault().get_secret, provider_id, default=None)
        return mask_preview(secret) if secret else ""

    def test_connection(self, provider_id: str, *, background: bool = True) -> None:
        self._status[provider_id] = ("checking", None, "")
        self.changed.emit("keys")
        if background:
            threading.Thread(target=self._ping_worker, args=(provider_id,), name="kadraj-ping",
                             daemon=True).start()
        else:
            self._ping_worker(provider_id)

    def test_results(self) -> Dict[str, PingResult]:
        """Son bağlantı testi sonuçları (sağlayıcı kimliği → ``PingResult``). Hata kartı ``result.error``'dadır."""
        return dict(self._results)

    def clear_test_result(self, provider_id: str) -> None:
        self._results.pop(provider_id, None)
        self.changed.emit("keys")

    def test_all(self, *, background: bool = True) -> None:
        for row in self.key_rows():
            self.test_connection(row.provider_id, background=background)

    def _ping_worker(self, provider_id: str) -> None:
        try:
            if self.ping_fn is not None:
                result = self.ping_fn(provider_id)
            else:
                result = self._providers().test_connection(provider_id, vault=self._vault(), timeout=PING_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 — ping asla arayüzü düşürmez
            result = PingResult(False, None, str(exc), None)
        self._status[provider_id] = (led_state(result.ok), result.latency_ms, result.message)
        self._results[provider_id] = result
        self.pingDone.emit(provider_id, result)
        self.changed.emit("keys")

    def set_model(self, provider_id: str, model: str) -> None:
        self.settings.set(f"providers.{provider_id}.model", str(model or ""))
        self.changed.emit("keys")

    # -- yerel çalışma zamanları ---------------------------------------------
    def local_runtimes(self) -> List[LocalRuntime]:
        return list(self._local)

    def whisper_model(self) -> str:
        value = str(self.settings.get("whisper.model") or "small")
        return value if value in WHISPER_MODELS else "small"

    def set_whisper_model(self, model: str) -> None:
        if model not in WHISPER_MODELS:
            raise ValueError(model)
        self.settings.set("whisper.model", model)
        self.changed.emit("local")

    def ensure_local(self) -> None:
        """Yerel algılamayı yalnızca ilk kez çalıştırır; sayfa her açıldığında ağ taraması yapılmaz."""
        if not self._local_loaded:
            self.refresh_local(background=True)

    def refresh_local(self, *, background: bool = True) -> None:
        if background:
            threading.Thread(target=self._refresh_local_worker, name="kadraj-local", daemon=True).start()
        else:
            self._refresh_local_worker()

    def _refresh_local_worker(self) -> None:
        rows: List[LocalRuntime] = []
        for p in self._providers().list():
            if not p.local:
                continue
            if p.id == "ollama":
                rows.append(self._detect_ollama(p))
            elif p.id == "comfyui":
                rows.append(self._detect_comfyui(p))
            else:
                rows.append(self._detect_module(p))
        self._local = rows
        self._local_loaded = True
        self.changed.emit("local")

    @staticmethod
    def _endpoint(provider: Any) -> str:
        fn = getattr(provider, "endpoint", None)
        return str(fn() if callable(fn) else getattr(provider, "base_url", "")).rstrip("/")

    def _get(self, url: str) -> Any:
        return (self.http_get or _default_http_get)(url, LOCAL_TIMEOUT)

    def _detect_ollama(self, provider: Any) -> LocalRuntime:
        endpoint = self._endpoint(provider)
        try:
            payload = self._get(f"{endpoint}/api/tags")
        except Exception:  # noqa: BLE001 — kapalı servis hata değildir
            return LocalRuntime(provider.id, provider.display_name, endpoint, "down")
        return LocalRuntime(provider.id, provider.display_name, endpoint, "up", parse_ollama_models(payload))

    def _detect_comfyui(self, provider: Any) -> LocalRuntime:
        endpoint = self._endpoint(provider)
        try:
            self._get(f"{endpoint}/system_stats")
        except Exception:  # noqa: BLE001
            return LocalRuntime(provider.id, provider.display_name, endpoint, "down")
        return LocalRuntime(provider.id, provider.display_name, endpoint, "up")

    def _module_installed(self, module: str = "faster_whisper") -> bool:
        try:
            return importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            return False

    def _detect_module(self, provider: Any) -> LocalRuntime:
        installed = self._module_installed(getattr(provider, "module", "faster_whisper"))
        return LocalRuntime(provider.id, provider.display_name, "", "up" if installed else "missing",
                            detail=self.whisper_model() if installed else "")

    def start_local(self, provider_id: str) -> str:
        """``started`` | ``missing`` | ``need_path`` | ``installed`` | ``missing_module``. Çalıştırır, beklemez."""
        if provider_id == "ollama":
            exe = shutil.which("ollama")
            if not exe:
                return "missing"
            self._spawn([exe, "serve"])
            return "started"
        if provider_id == "comfyui":
            path = str(self.settings.get("comfyui.path") or "")
            main = Path(path).expanduser() / "main.py" if path else None
            if main is None or not main.is_file():
                return "need_path"
            self._spawn([sys.executable, str(main)], cwd=str(main.parent))
            return "started"
        if provider_id == "whisper_local":
            return "installed" if self._module_installed() else "missing_module"
        return "missing"

    def _spawn(self, args: Sequence[str], cwd: Optional[str] = None) -> None:
        opener = self.popen or subprocess.Popen
        opener(list(args), cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL, start_new_session=True)

    def set_comfyui_path(self, path: str) -> None:
        folder = Path(path).expanduser()
        if not folder.is_dir():
            raise ValueError(i18n.t(K.FOLDER_INVALID))
        self.settings.set("comfyui.path", str(folder))
        self.changed.emit("local")

    # -- araçlar -----------------------------------------------------------
    def tool_info(self) -> ToolInfo:
        return self._tools or ToolInfo(_ytdlp_version(), "", "")

    def ensure_tools(self) -> None:
        """Araç sürümlerini yalnızca ilk kez okur (ffmpeg alt süreci her sayfa açılışında çalışmaz)."""
        if not self._tools_loaded:
            self.refresh_tools(background=True)

    def refresh_tools(self, *, background: bool = True) -> None:
        if background:
            threading.Thread(target=self._refresh_tools_worker, name="kadraj-tools", daemon=True).start()
        else:
            self._refresh_tools_worker()

    def _refresh_tools_worker(self) -> None:
        exe = components.ffmpeg_path() or ""
        ffmpeg = ""
        ff_hit = components.find("ffmpeg")
        if exe:
            if ff_hit is not None and ff_hit.version and ff_hit.source in (components.SOURCE_UPDATED, components.SOURCE_BUNDLED):
                ffmpeg = ff_hit.version                                # güncelleyici/paketleme sürümü yazdı
            else:
                code, out = self._run([exe, "-version"], 5.0)
                ffmpeg = parse_ffmpeg_version(out) if code == 0 else ""
        js_name = components.QUICKJS if device.is_android() else "deno"      # "deno" satırı: JS çözücüsü
        deno = components.find(js_name)
        ytdlp = components.ytdlp_choice()
        sources = {"yt-dlp": ytdlp.source if ytdlp else "", "ffmpeg": ff_hit.source if ff_hit else "",
                   "deno": deno.source if deno else ""}
        self._tools = ToolInfo(_ytdlp_version(), ffmpeg, exe,
                               (deno.version or components.probe_version(js_name)) if deno else "", sources)
        self._tools_loaded = True
        self.changed.emit("tools")

    #: Testler için sahte güncelleyici fabrikası (``Updater``'ın ``run`` imzası). ``None`` → gerçek ``core.updater.Updater``.
    updater_factory: Optional[Callable[[], Any]] = None

    def update_ytdlp(self) -> Job:
        """Eski ad; bütün bileşenleri (yt-dlp, FFmpeg, Deno) günceller — ``update_components``."""
        return self.update_components()

    def update_components(self) -> Job:
        """"Şimdi güncelle": yt-dlp, FFmpeg ve Deno'yu iş kuyruğunda resmî kaynaklardan günceller (sağlama doğrulanır)."""
        if self.ctx is None:
            raise RuntimeError("Ayarlar servisi başlatılmadı")
        return self.ctx.jobs.submit(UPDATE_KIND, {}, i18n.t(K.UPDATE_TITLE), notify=True)

    def repair_components(self) -> Job:
        """"Onar": eksik ya da bozuk yt-dlp, FFmpeg, ffprobe ve Deno'yu kurulu sürümleri yok sayarak resmî kaynaklardan
        yeniden indirir (``Updater(seed=True)``; SHA-256 doğrulaması ve atomik değişim aynı kalır)."""
        if self.ctx is None:
            raise RuntimeError("Ayarlar servisi başlatılmadı")
        return self.ctx.jobs.submit(UPDATE_KIND, {"repair": True}, i18n.t(K.REPAIR_TITLE), notify=True)

    def auto_update(self) -> bool:
        """"Açılışta bileşenleri güncelle" anahtarı (varsayılan açık)."""
        return bool(self.settings.get("tools.auto_update", True))

    def set_auto_update(self, on: bool) -> None:
        self.settings.set("tools.auto_update", bool(on))

    def _handle_ytdlp_update(self, job: Job, jc: Any) -> Dict[str, Any]:
        from ...core import updater
        repair = bool((getattr(job, "payload", None) or {}).get("repair"))
        jc.report(0.05, "")
        if self.updater_factory is not None:
            runner = self.updater_factory(seed=True) if repair else self.updater_factory()
        else:
            runner = updater.Updater(seed=repair)
        rep = runner.run(lambda text, frac: jc.report(0.05 + 0.9 * (frac or 0.0), text), force=True)
        if rep.offline and not rep.updated and not rep.pending:
            raise SmartError("tools.offline", i18n.t(K.UPDATE_OFFLINE_TITLE), i18n.t(K.UPDATE_OFFLINE_SUMMARY),
                             cause=i18n.t(K.UPDATE_OFFLINE_CAUSE),
                             steps=tuple(f"{n}. {i18n.t(getattr(K, f'UPDATE_OFFLINE_STEP{n}'))}" for n in (1, 2)),
                             retryable=True, source="settings")
        if rep.errors:
            raise SmartError("tools.update_failed", i18n.t(K.UPDATE_FAILED_TITLE),
                             i18n.t(K.UPDATE_FAILED_SUMMARY), cause=i18n.t(K.UPDATE_FAILED_CAUSE),
                             steps=(f"1. {i18n.t(K.UPDATE_FAILED_STEP)}",
                                    f"2. {i18n.t(K.UPDATE_FAILED_STEP2)}"),
                             technical="; ".join(rep.errors)[-800:], retryable=True, source="settings")
        self._tools = None
        jc.report(1.0, "")
        return {"updated": rep.updated, "pending": rep.pending, "current": rep.current}

    def _safe_update(self) -> None:
        try:
            self.update_components()
        except Exception:  # noqa: BLE001 — kart düğmesi uygulamayı düşürmesin
            pass

    def _on_job_updated(self, job: Any) -> None:
        if getattr(job, "kind", "") == UPDATE_KIND and getattr(job, "finished", False):
            self.refresh_tools(background=True)

    # -- proje klasörü ve önbellek -----------------------------------------
    def project_root(self) -> str:
        return str(self.settings.get("project_root") or "")

    def set_project_root(self, path: str) -> None:
        folder = Path(path).expanduser()
        if not folder.is_dir():
            raise ValueError(i18n.t(K.FOLDER_INVALID))
        self.settings.set("project_root", str(folder))
        self.changed.emit("folder")

    def folder_info(self) -> Optional[FolderInfo]:
        root = self.project_root() or str(Path.home())
        st = disk.status(root)
        if st.level is disk.Level.UNKNOWN:
            return None
        used = (st.total - st.free) / st.total if st.total else 0.0
        return FolderInfo(root=root, free=st.free, total=st.total, used_fraction=min(max(used, 0.0), 1.0))

    def cache_target(self) -> Optional[Path]:
        """Önbellek temizliğinin hedefi: açık proje; yoksa son açılan proje; yoksa ``None``."""
        project = self.ctx.project if self.ctx is not None else None
        if project is not None and project.is_open and project.root is not None:
            return Path(project.root)
        last = str(self.settings.get("last_project") or "")
        return Path(last) if last else None

    def cache_bytes(self) -> int:
        target = self.cache_target()
        if target is None:
            return 0
        return sum(u.bytes for u in cache.usage(target).values())

    def clean_cache(self) -> cache.CleanReport:
        target = self.cache_target()
        if target is None:
            raise ValueError(i18n.t(K.CLEAN_NO_TARGET))
        return cache.clean(target)

    def paths_info(self) -> Dict[str, str]:
        target = self.cache_target()
        render = folders.folder_for("export")
        return {
            "project": self.project_root() or str(Path.home()),
            "cache": str(target / ".kadraj" / "cache") if target else "—",
            "render": str(target / render) if target else "—",
            "temp": str(Path(tempfile.gettempdir()) / "kadraj"),
            "comfyui": str(self.settings.get("comfyui.path") or "—"),
        }

    # -- sağlayıcı sırası (fallback) ---------------------------------------
    def fallback_order(self) -> Dict[str, List[str]]:
        raw = self.settings.get("fallback_order") or {}
        return {k: list(v) for k, v in raw.items() if isinstance(v, list)} if isinstance(raw, Mapping) else {}

    def chain_ids(self, kind: str) -> List[str]:
        """Bir tür için gösterilecek sıra: kayıtlı sıra + kalan sağlayıcılar (kayıt sırasıyla)."""
        from ...core.contracts import ProviderKind
        available = [p.id for p in self._providers().list(ProviderKind(kind))]
        saved = [i for i in self.fallback_order().get(kind, []) if i in available]
        return saved + [i for i in available if i not in saved]

    def set_chain(self, kind: str, ids: Sequence[str]) -> None:
        known = {p.id for p in self._providers().list()}
        clean = [i for i in ids if i in known]
        merged = self.fallback_order()
        merged[kind] = clean
        self.settings.set("fallback_order", merged)
        self.changed.emit("fallback")

    # -- profiller ---------------------------------------------------------
    def profile_list(self) -> List[profiles.Profile]:
        return profiles.list_profiles()

    def create_profile(self, name: str) -> profiles.Profile:
        profile = profiles.create_profile(name, ui_mode=self.ui_mode())
        self.changed.emit("profiles")
        return profile

    def switch_profile(self, profile_id: str) -> profiles.Profile:
        """Profili etkinleştirir ve profilin arayüz modu ile erişilebilirlik tercihlerini uygular."""
        profile = profiles.switch_profile(profile_id)
        self.set_ui_mode(profile.ui_mode)
        for key in A11Y_KEYS:
            self.set_a11y(key, bool(profile.a11y.get(key, False)), notify=False)
        self.changed.emit("profiles")
        self.changed.emit("view")
        return profile

    def delete_profile(self, profile_id: str) -> None:
        profiles.delete_profile(profile_id)
        self.changed.emit("profiles")

    # -- görünüm -----------------------------------------------------------
    def ui_mode(self) -> str:
        return self.ctx.ui_mode.value

    def set_ui_mode(self, mode: str) -> None:
        self.ctx.set_ui_mode(UiMode.coerce(mode))
        self.changed.emit("view")

    def a11y(self, key: str) -> bool:
        return bool(self.settings.get(f"a11y.{key}", False))

    def set_a11y(self, key: str, value: bool, *, notify: bool = True) -> None:
        if key not in A11Y_KEYS:
            raise ValueError(key)
        self.settings.set(f"a11y.{key}", bool(value))
        theme = self.ctx.theme if self.ctx is not None else None
        if theme is not None:
            theme.apply(large_text=self.a11y("large_text"), high_contrast=self.a11y("high_contrast"),
                        reduced_motion=self.a11y("reduced_motion"), animate=False)
        if self.ctx is not None:
            self.ctx.bus.emit(Topics.A11Y_CHANGED, {k: self.a11y(k) for k in A11Y_KEYS})
        if notify:
            self.changed.emit("view")

    def tips_enabled(self) -> bool:
        return bool(self.settings.get("tips.enabled", True))

    def set_tips(self, value: bool) -> None:
        self.settings.set("tips.enabled", bool(value))
        self.changed.emit("view")
