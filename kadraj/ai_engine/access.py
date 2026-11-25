"""AI servisi erişimi: **hangi servis kullanılıyor ve durumu ne** + "Kaydet ve dene" (Qt'siz, test edilebilir).

Durum çubuğu ve kurulum kartı bunu kullanır: ``Bağlı`` / ``Anahtar yok`` / ``Yerel servis kapalı`` (+ ``Model kurulu değil``: yerel servis
açık ama içinde model yok). Anahtar yalnızca kasaya yazılır ve **maskeli** gösterilir; hiçbir ileti anahtarı içermez.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from ..core import i18n
from ..core.contracts import PingResult, ProviderKind, SmartAction, SmartError
from ..core.errors import translate
from . import aierrors

KIND_PROVIDERS: Dict[str, Sequence[str]] = {
    "text": ("anthropic", "openai_compat", "ollama"),
    "image": ("bfl_flux", "google_imagen", "openai_image", "comfyui"),
}
#: kurulum kartında listelenen **anahtarlı** servisler (anahtar sayfası + yapıştırma kutusu)
KEY_CHOICES: Dict[str, Sequence[str]] = {"text": ("anthropic", "openai_compat"), "image": ("bfl_flux", "google_imagen", "openai_image")}
LOCAL_PROVIDER = {"text": "ollama", "image": "comfyui"}
PROVIDER_KIND = {"text": ProviderKind.LLM, "image": ProviderKind.IMAGE}
LOCAL_IDS = ("ollama", "comfyui")


@dataclass(frozen=True)
class ServiceStatus:
    kind: str                       # "text" | "image"
    state: str                      # "ready" | "no_key" | "local_down" | "no_model"
    provider_id: str = ""
    provider_name: str = ""
    local: bool = False

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    @property
    def state_text(self) -> str:
        if self.state == "no_model":
            return i18n.t("ai.state.no_model")
        return i18n.t(f"ai.state.{self.state}")

    @property
    def label(self) -> str:
        name = self.provider_name or i18n.t("ai.status.none")
        return f"{i18n.t('ai.status.' + self.kind)}: {name} · {self.state_text}"


class AIAccess:
    def __init__(self, registry: Any, vault: Any, settings: Any = None) -> None:
        self.registry, self.vault, self.settings = registry, vault, settings
        self.local_state: Dict[str, str] = {}            # "up" | "down" | "no_model"
        self.local_checked = False

    # -- yerel servisler ------------------------------------------------------------------------------
    def probe_local(self, timeout: float = 2.0) -> Dict[str, str]:
        """Ollama/ComfyUI'ye bakar (ağ: yalnız 127.0.0.1). Açık ama modelsiz → ``no_model``. Arka plan iş parçacığında çağır."""
        out: Dict[str, str] = {}
        for pid in LOCAL_IDS:
            p = self.registry.get(pid)
            if p is None:
                continue
            try:
                res = p.test_connection(None, timeout)
            except Exception:                            # noqa: BLE001
                out[pid] = "down"
                continue
            if res.ok:
                out[pid] = "up"
            else:
                code = getattr(res.error, "code", "")
                out[pid] = "no_model" if code in ("ai.local_model_missing", "ai.comfy_no_model") else "down"
        return out

    def set_local_state(self, states: Mapping[str, str]) -> None:
        self.local_state.update(states)
        self.local_checked = True
        for kind, pid in LOCAL_PROVIDER.items():                          # bir kez çalıştığı görülen yerel servis "seçilmiş" sayılır
            if self.local_state.get(pid) == "up" and self.settings is not None:
                try:
                    self.settings.set(f"ai.local_seen.{kind}", True)
                except Exception:                        # noqa: BLE001
                    pass

    def local_up_map(self) -> Dict[str, bool]:
        """Gateway için: bilinen **kapalı** yerel servis ``False`` (denenmez); bilinmeyen/açık/modelsiz → deneme serbest."""
        return {pid: self.local_state.get(pid) != "down" for pid in self.local_state}

    def _prefers_local(self, kind: str) -> bool:
        pid = LOCAL_PROVIDER[kind]
        if self.settings is None:
            return False
        try:
            if self.settings.get(f"ai.local_seen.{kind}") or self.settings.get(f"providers.{pid}.model"):
                return True
            order = (self.settings.get("fallback_order") or {})
            first = (order.get("llm" if kind == "text" else "image") or [None])[0] if isinstance(order, dict) else None
            return first == pid
        except Exception:                                # noqa: BLE001
            return False

    # -- durum ----------------------------------------------------------------------------------------------
    def status(self, kind: str) -> ServiceStatus:
        ids = KIND_PROVIDERS[kind]
        try:                                             # "anahtarı var" (hız sınırı soğumasındaki servis de bağlı sayılır): chain() değil configured()
            chain = sorted((p for p in self.registry.configured(self.vault, PROVIDER_KIND[kind]) if p.id in ids), key=lambda p: ids.index(p.id))
        except Exception:                                # noqa: BLE001
            chain = []
        for p in chain:
            if p.local:
                st = self.local_state.get(p.id)
                if st == "up":
                    return ServiceStatus(kind, "ready", p.id, p.display_name, True)
                continue
            return ServiceStatus(kind, "ready", p.id, p.display_name, False)
        loc = self.registry.get(LOCAL_PROVIDER[kind])
        name = loc.display_name if loc is not None else aierrors.info(LOCAL_PROVIDER[kind]).name
        st = self.local_state.get(LOCAL_PROVIDER[kind])
        if st == "no_model":
            return ServiceStatus(kind, "no_model", LOCAL_PROVIDER[kind], name, True)
        if st == "down" and self._prefers_local(kind):
            return ServiceStatus(kind, "local_down", LOCAL_PROVIDER[kind], name, True)
        return ServiceStatus(kind, "no_key")

    def ready(self, kind: str) -> bool:
        return self.status(kind).ready

    # -- kurulum kartı: kaydet + dene ------------------------------------------------------------------------------
    def key_choices(self, kind: str) -> List[aierrors.ProviderInfo]:
        return [aierrors.info(pid) for pid in KEY_CHOICES.get(kind, ())]

    def save_and_test(self, provider_id: str, secret: str) -> PingResult:
        """Anahtarı kasaya kaydeder, sonra **gerçek ama ucuz** bir çağrı yapar (liste + minik istek). Asla istisna atmaz."""
        provider = self.registry.get(provider_id)
        value = (secret or "").strip()
        if provider is None:
            err = aierrors.error_card("http", provider_id, name=aierrors.info(provider_id).name, status=0)
            return PingResult(False, None, err.summary, err)
        if not value:
            return PingResult(False, None, i18n.t("ai.setup.empty_key"), None)
        key_id = aierrors.key_id_of(provider_id)
        try:
            previous = self.vault.get_secret(key_id) if self.vault.has_secret(key_id) else None
        except Exception:                                # noqa: BLE001 — kasa kilitliyse set_secret asıl hatayı verir
            previous = None
        try:
            self.vault.set_secret(key_id, value)
        except SmartError as err:
            return PingResult(False, None, err.summary, _with_settings(err))
        except ValueError:
            return PingResult(False, None, i18n.t("ai.setup.empty_key"), None)
        except Exception as exc:                         # noqa: BLE001
            err = _with_settings(translate(exc, {"source": "vault"}))
            return PingResult(False, None, err.summary, err)
        quick: Callable[..., PingResult] = getattr(provider, "quick_test", None) or provider.test_connection
        try:
            res = quick(self.vault)
        except Exception as exc:                         # noqa: BLE001
            err = translate(exc, {"provider": provider_id})
            res = PingResult(False, None, err.summary, err)
        if not res.ok and getattr(res.error, "code", "") == "ai.auth":
            self._undo_key(key_id, previous)              # yanlış anahtar "Bağlı" görünmesin; önceki çalışan anahtar geri gelir
        return res

    def _undo_key(self, key_id: str, previous: Optional[str]) -> None:
        try:
            if previous:
                self.vault.set_secret(key_id, previous)
            else:
                self.vault.delete_secret(key_id)
        except Exception:                                # noqa: BLE001
            pass

    def test_provider(self, provider_id: str) -> PingResult:
        """Ayarlar → "Bağlantıyı test et": para harcamayan tek GET (model/kredi listesi)."""
        return self.registry.test_connection(provider_id, vault=self.vault, timeout=8.0)


def _with_settings(err: SmartError) -> SmartError:
    """Kasa hatalarına (kilitli, parola gerekli…) "Ayarlar'ı aç" düğmesi ekler; kasa kartlarında hazır düğme yoksa."""
    if any(a.kind == "open_settings" for a in err.actions):
        return err
    extra = SmartAction("open_settings", i18n.t("ai.action.open_settings"), "open_settings", {"section": "vault"})
    clone = SmartError(err.code, err.title, err.summary, cause=err.cause, steps=err.steps, actions=(*err.actions, extra),
                       severity=err.severity, technical=err.technical, retryable=err.retryable, source=err.source)
    return clone
