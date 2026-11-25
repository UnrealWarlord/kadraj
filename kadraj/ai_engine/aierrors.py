"""AI servislerinin bilgi tablosu ve sade hata kartları.

* ``PROVIDERS``: her servisin resmî "anahtar al" / "ödeme" / yerel indirme adresi (hata kartları, kurulum kartı ve Ayarlar kullanır).
* ``classify``: HTTP durumu + yanıt gövdesinden **neden**i bulur: yanlış anahtar, bakiye/kota bitti, hız sınırı, model yok,
  izin yok, sunucu sorunu. Gövde servislere göre değişir (Anthropic ``error.type``, OpenAI ``error.code``, Google ``error.status``
  ya da ``error.code``, BFL ``detail``, Ollama düz ``{"error": "..."}``) — hepsinde ortak kelimelere bakılır.
* ``error_card``: her neden için ``SmartError``: sade neden + **numaralı adımlar** + eylem düğmeleri.

Anahtar hiçbir yerde yazılmaz; ``technical`` alanı çağıran tarafından ``redact``'lanır.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..core import i18n
from ..core.contracts import Severity, SmartAction, SmartError


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    name: str                  # kısa ad (kartlarda)
    kind: str                  # "text" | "image" | "local"
    key_url: str = ""          # resmî "anahtar al" sayfası
    billing_url: str = ""      # resmî kredi/ödeme sayfası
    local_url: str = ""        # yerel servisin indirme sayfası
    models_url: str = ""       # güncel model adları
    key_steps: Tuple[str, ...] = ()   # i18n anahtarları: o servise özgü 1-2-3 adımı


# Adresler resmî dokümanlardan alındı (2026-11): platform.claude.com/docs/en/get-api-key, developers.openai.com/api/docs/guides/error-codes,
# docs.bfl.ml (dashboard.bfl.ai), ai.google.dev (aistudio.google.com), docs.ollama.com, docs.comfy.org.
PROVIDERS: Dict[str, ProviderInfo] = {
    "anthropic": ProviderInfo("anthropic", "Claude (Anthropic)", "text", "https://platform.claude.com/settings/keys",
                              "https://platform.claude.com/settings/billing", models_url="https://platform.claude.com/docs/en/about-claude/models/overview"),
    "openai_compat": ProviderInfo("openai_compat", "OpenAI", "text", "https://platform.openai.com/settings/organization/api-keys",
                                  "https://platform.openai.com/settings/organization/billing", models_url="https://developers.openai.com/api/docs/models"),
    "openai_image": ProviderInfo("openai_image", "OpenAI görsel", "image", "https://platform.openai.com/settings/organization/api-keys",
                                 "https://platform.openai.com/settings/organization/billing", models_url="https://developers.openai.com/api/docs/models"),
    "bfl_flux": ProviderInfo("bfl_flux", "FLUX (Black Forest Labs)", "image", "https://dashboard.bfl.ai", "https://dashboard.bfl.ai",
                             models_url="https://docs.bfl.ml/"),
    "google_imagen": ProviderInfo("google_imagen", "Google Gemini görsel", "image", "https://aistudio.google.com/apikey",
                                  "https://aistudio.google.com/billing", models_url="https://ai.google.dev/gemini-api/docs/image-generation"),
    "ollama": ProviderInfo("ollama", "Ollama (yerel)", "local", local_url="https://ollama.com/download", models_url="https://ollama.com/search"),
    "comfyui": ProviderInfo("comfyui", "ComfyUI (yerel)", "local", local_url="https://www.comfy.org/download",
                            models_url="https://docs.comfy.org/get_started/first_generation"),
    "elevenlabs": ProviderInfo("elevenlabs", "ElevenLabs", "text", "https://elevenlabs.io/app/settings/api-keys"),
    "video": ProviderInfo("video", "Video servisi", "text"),
}
#: bir servisin anahtarı başka bir servisinkiyle paylaşılır (kasada tek kayıt)
KEY_ALIASES = {"openai_image": "openai_compat"}
#: Ollama için önerilen model (resmî hızlı başlangıç sayfasındaki örnek)
OLLAMA_SUGGESTED = "gemma4:e2b"


def info(provider_id: str) -> ProviderInfo:
    return PROVIDERS.get(provider_id) or ProviderInfo(provider_id, provider_id, "text")


def key_id_of(provider_id: str) -> str:
    return KEY_ALIASES.get(provider_id, provider_id)


# ---------------------------------------------------------------------------
# Nedeni bul
# ---------------------------------------------------------------------------
_HARD_QUOTA = ("insufficient_quota", "credit_balance", "credit balance", "billing_hard_limit", "spend_limit", "spend limit", "out of funds",
               "out of credits", "insufficient credit", "insufficient funds", "balance is too low", "enforced_spend", "billing_error")
_QUOTA = ("insufficient_quota", "credit_balance", "credit balance", "billing_hard_limit", "spend_limit", "spend limit", "out of funds",
          "out of credits", "insufficient credit", "insufficient funds", "payment required", "billing_error", "enforced_spend",
          "plans and billing", "check your plan and billing", "quota_exceeded", "exceeded your current quota", "no credits",
          "balance is too low", "prepay", "usage_limit_exceeded")
_AUTH = ("api key not valid", "api_key_invalid", "invalid api key", "invalid x-api-key", "incorrect api key", "invalid_api_key",
         "authentication_error", "authentication", "unauthorized", "invalid_authentication", "api key expired", "key is invalid", "invalid key")
_MODEL = ("model_not_found", "not_found_error", "model not found", "does not exist", "unknown model", "invalid model", "is not found for api",
          "is not supported", "model:", "no such model", "not_found", "deprecated", "has been shut down", "decommission", "sunset")
_PERMISSION = ("verification", "verify your organization", "permission", "not allowed", "denied access", "free_tier", "country", "region",
               "forbidden", "not available in your", "access to this model")


def _has(low: str, words: Sequence[str]) -> bool:
    return any(w in low for w in words)


def classify(status: int, body: str = "", *, headers: Optional[Mapping[str, str]] = None, has_model: bool = True) -> str:
    """HTTP durumu + gövde → neden kodu: ``auth quota rate_limit model_not_found permission server http``."""
    low = (body or "").lower()
    if status == 401:
        return "auth"
    if status == 402:
        return "quota"
    if status in (408, 504) or status >= 500:
        return "server"
    if status == 429:
        if _has(low, _HARD_QUOTA):
            return "quota"
        retry_after = any(k.lower() == "retry-after" for k in (headers or {}))
        return "rate_limit" if retry_after else ("quota" if _has(low, _QUOTA) else "rate_limit")
    if status == 403:
        if _has(low, _QUOTA):
            return "quota"
        if _has(low, ("api key", "invalid_api_key", "invalid key")) and not _has(low, _PERMISSION):
            return "auth"
        return "permission"
    if status == 404:
        return "model_not_found" if has_model else "http"
    if status == 400 or status == 422:
        if _has(low, _QUOTA):
            return "quota"
        if _has(low, _AUTH[:8]):
            return "auth"
        if has_model and _has(low, ("model_not_found", "model not found", "invalid model", "unknown model", "no such model",
                                    "does not exist", "is not found for api", "has been shut down", "decommission", "sunset")) and "model" in low:
            return "model_not_found"
        return "http"
    return "http"


# ---------------------------------------------------------------------------
# Kartlar
# ---------------------------------------------------------------------------
def _act(action_id: str, kind: str, payload: Optional[Mapping[str, Any]] = None) -> SmartAction:
    return SmartAction(action_id, i18n.t(f"ai.action.{action_id}"), kind, dict(payload or {}))


def _actions(code: str, pi: ProviderInfo, technical: str) -> Tuple[SmartAction, ...]:
    out = []
    if code in ("auth", "no_key", "permission") and pi.key_url:
        out.append(_act("open_key_page", "open_url", {"url": pi.key_url}))
    if code in ("quota", "rate_limit") and pi.billing_url:
        out.append(_act("open_billing_page", "open_url", {"url": pi.billing_url}))
    if code in ("local_down", "local_model_missing", "comfy_no_model") and pi.local_url:
        out.append(_act("open_download_page", "open_url", {"url": pi.local_url}))
    if code == "local_model_missing" and pi.id == "ollama":
        out.append(_act("copy_pull", "copy", {"text": f"ollama pull {OLLAMA_SUGGESTED}"}))
    if code in ("auth", "no_key", "quota", "model_not_found", "permission"):
        out.append(_act("open_settings", "open_settings", {"section": "vault"}))
    if code in ("quota", "rate_limit", "offline", "timeout", "server", "local_down", "local_model_missing", "comfy_no_model", "model_not_found", "http", "auth", "permission"):
        out.append(_act("retry", "retry"))
    if code == "http" and technical:
        out.append(SmartAction("copy_details", i18n.t("err.action.copy_details") if i18n.has("err.action.copy_details") else "Copy", "copy",
                               {"text": technical}))
    return tuple(out)


#: ``ai_err`` ile kurulan kartlara (aksi verilmedikçe) varsayılan düğmeler: "servis hazır değil" → Ayarlar, geçici sorun → yeniden dene
_NEEDS_SETTINGS = ("no_llm", "no_vision", "no_image", "no_video", "no_tts", "edit_unavailable")
_NEEDS_RETRY = ("bad_response", "invalid_json", "timeout", "moderated", "refused", "comfy")


def default_actions(code: str) -> Tuple[SmartAction, ...]:
    if code in _NEEDS_SETTINGS:
        return (_act("open_settings", "open_settings", {"section": "vault"}),)
    if code in _NEEDS_RETRY:
        return (_act("retry", "retry"),)
    return ()


_RETRYABLE = {"rate_limit", "offline", "timeout", "server", "local_down", "local_model_missing", "comfy_no_model"}
_WARN = {"quota", "rate_limit", "offline", "timeout", "server", "local_down"}


def error_card(code: str, provider_id: str, *, name: str = "", model: str = "", technical: str = "", retry_in: Optional[float] = None,
               **extra: Any) -> SmartError:
    """``ai.<code>`` kartı: başlık, özet, neden, **numaralı adımlar**, düğmeler. Anahtar içermez."""
    pi = info(provider_id)
    # yerel servisler ve bulut aynı kodu paylaşır; metin yerel mi buluta göre seçilir
    variant = code
    if code == "model_not_found" and pi.kind == "local":
        variant = "local_model_missing"
        code = "local_model_missing"
    fmt: Dict[str, Any] = {"name": name or pi.name, "model": model or "—", "key_url": pi.key_url, "billing_url": pi.billing_url,
                           "local_url": pi.local_url, "models_url": pi.models_url, "pull_cmd": f"ollama pull {OLLAMA_SUGGESTED}",
                           "retry_in": str(max(1, -(-int(retry_in) // 60))) if retry_in else "1-2", **extra}   # saniye → dakika (yukarı yuvarla)
    base = f"ai.err.{variant}"
    title = i18n.t(f"{base}.title", **fmt)
    summary = i18n.t(f"{base}.summary", **fmt)
    cause = i18n.t(f"{base}.cause", **fmt) if i18n.has(f"{base}.cause") else ""
    steps = []
    for i in range(1, 9):
        key = f"{base}.step{i}"
        if not i18n.has(key):
            break
        steps.append(i18n.t(key, **fmt))
    err = SmartError(f"ai.{code}", title, summary, cause=cause, steps=tuple(steps), actions=_actions(code, pi, technical),
                     severity=Severity.WARNING if code in _WARN else Severity.ERROR, technical=technical,
                     retryable=code in _RETRYABLE, source=provider_id)
    return err


def from_http(provider_id: str, name: str, status: int, body: str, *, headers: Optional[Mapping[str, str]] = None, model: str = "",
              technical: str = "", has_model: bool = True) -> Tuple[str, SmartError]:
    code = classify(status, body, headers=headers, has_model=has_model)
    if code == "http":
        return code, error_card("http", provider_id, name=name, model=model, technical=technical, status=status)
    return code, error_card(code, provider_id, name=name, model=model, technical=technical)
