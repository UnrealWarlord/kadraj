"""İndirici hata açıklayıcısı: yt-dlp/FFmpeg hatasını **sade nedeni, numaralı adımları ve bir eylemi** olan ``SmartError``'a çevirir.

``explain`` tanıdığı durumlarda kart döner, tanımazsa ``None`` (çağıran ``core.errors.translate``'e düşer). Hiçbir durumda
yalnızca "Olmadı" demez. Eylem ``update_components`` (kind ``custom``, konu ``components.update``) bileşen güncelleyicisini
çalıştırır; ``open_settings`` Ayarlar → Araçlar'ı açar.
"""
from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional, Tuple

from ...core import components, i18n
from ...core.contracts import SmartAction, SmartError
from .strings import K

TOPIC_UPDATE = "components.update"
SOURCE = "downloader"


def _blob(exc: BaseException, warnings: Iterable[str]) -> str:
    return (str(exc) + "\n" + "\n".join(warnings)).lower().replace("’", "'")


def _has(text: str, *needles: str) -> bool:
    return any(n in text for n in needles)


_JS = ("javascript runtime", "no supported javascript", "challenge solving failed", "n challenge", "yt-dlp-ejs",
       "ejs solver", "js challenge", "nsig extraction failed", "signature extraction failed")
_FFMPEG = ("ffmpeg is not installed", "ffmpeg not found", "ffprobe and ffmpeg not found", "ffmpeg or avconv",
           "unable to find ffmpeg", "ffmpeg location", "you have requested merging of multiple formats but ffmpeg")
_BOT = ("not a bot", "confirm you're not a bot")
_FORMAT = ("requested format is not available", "no video formats found", "only images are available")
_OUTDATED = ("unable to extract", "please report this issue", "update yt-dlp", "is no longer available in this version",
             "sign in to confirm")        # son ikisi: BOT/yaş kuralları önce denenir


def classify(exc: BaseException, warnings: Iterable[str] = ()) -> Optional[str]:
    """Hata kimliği (``JS|FFMPEG|BOT|FORMAT|OUTDATED``) ya da ``None``."""
    text = _blob(exc, warnings)
    if isinstance(exc, FileNotFoundError) and "ffmpeg" in text:
        return "FFMPEG"
    if _has(text, *_JS):
        return "JS"
    if _has(text, *_FFMPEG):
        return "FFMPEG"
    if _has(text, *_BOT) and "age" not in text:
        return "BOT"
    if _has(text, *_FORMAT):
        # YouTube'da JS çalıştırıcısı (Deno / Android'de QuickJS) yoksa "format yok" büyük olasılıkla çözücü eksikliğidir.
        return "JS" if ("youtube" in text and components.js_runtime() is None) else "FORMAT"
    if _has(text, "unable to extract", "please report this issue", "update yt-dlp", "nsig"):
        return "OUTDATED"
    return None


def build(error_id: str, technical: str = "") -> SmartError:
    t = i18n.t
    k = lambda part: getattr(K, f"ERR_{error_id}_{part}")      # noqa: E731
    steps = tuple(f"{i}. {t(k(f'STEP{i}'))}" for i in (1, 2, 3))
    actions: List[SmartAction] = []
    if error_id != "FORMAT":
        actions.append(SmartAction("update_components", t(K.ERR_ACTION_UPDATE), "custom", {"topic": TOPIC_UPDATE}))
    actions.append(SmartAction("open_tools", t(K.ERR_ACTION_TOOLS), "open_settings", {"section": "tools"}))
    return SmartError(f"downloader.{error_id.lower()}", t(k("TITLE")), t(k("SUMMARY")), cause=t(k("CAUSE")),
                      steps=steps, actions=tuple(actions), technical=technical[-800:], retryable=True, source=SOURCE)


def explain(exc: BaseException, warnings: Iterable[str] = ()) -> Optional[SmartError]:
    if isinstance(exc, SmartError):
        return exc
    warnings = list(warnings)
    rid = classify(exc, warnings)
    if rid is None:
        return None
    tech = str(exc) + (("\n" + "\n".join(warnings)) if warnings else "")
    return build(rid, tech)


class Collector:
    """yt-dlp ``logger`` seçeneği: uyarıları toplar (``no_warnings`` onları gizlerdi), ekrana bir şey yazmaz."""

    def __init__(self) -> None:
        self.warnings: List[str] = []
        self.errors: List[str] = []

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        self.warnings.append(str(msg))

    def error(self, msg: str) -> None:
        self.errors.append(str(msg))

    def degraded(self) -> bool:
        """YouTube çözücüsü eksik olduğu için biçimler kırpıldı mı?"""
        return any(_has(w.lower(), *_JS) for w in self.warnings)
