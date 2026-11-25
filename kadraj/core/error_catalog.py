"""Akıllı Hata sözlüğü: FFmpeg, yt-dlp, HTTP, CUDA ve ses girişi için ``ErrorRule`` listesi.

``errors.translate`` bu listeyi ``RULES`` üzerinden tembelce yükler. Her kural:

* ``matches`` yan etkisiz ve hızlıdır; ``tool``, ``stderr``, ``status`` gibi bağlam
  anahtarlarına (bkz. ``contracts.ErrorContext``) ve istisna türüne bakar;
* ``build`` Basit Türkçe başlık, tek cümlelik özet, neden ve eylem içeren ``SmartError`` döner.

Öncelik: özel kurallar 10–400, araca bağlı genel yedekler 90–190. Yerleşik kurallar 900 ve
üzerindedir; bu yüzden ağ, zaman aşımı ve izin hataları yedeklerde yakalanmaz
(``_transport`` ile dışlanır) ve yerleşik kartlara bırakılır.

Metinler ``err.cat.<kural>.{title,summary,cause,step1}`` anahtarlarıyla TR ve EN olarak
modül yüklenirken kaydedilir (``register_strings``; çekirdek anahtarların üzerine yazmaz).
"""
from __future__ import annotations

import errno
import socket
from typing import Callable, Dict, Optional, Tuple

from .contracts import ErrorContext, ErrorRule, Severity, SmartAction, SmartError
from .i18n import has, register_strings, t

_FIELDS = ("title", "summary", "cause", "step1")

# rule_id: (başlık, özet, neden, eylem) — tek cümleler, Basit Türkçe.
_TR: Dict[str, Tuple[str, str, str, str]] = {
    "ffmpeg.disk_full": (
        "Video işlemi için yer kalmadı",
        "FFmpeg dosyayı yazarken diskte boş alan bitti.",
        "Projenin bulunduğu disk dolmuş olabilir.",
        "Diskte yer aç ya da projeyi daha boş bir diske taşı."),
    "ffmpeg.corrupt_input": (
        "Video dosyası okunamadı",
        "Kaynak dosya bozuk ya da eksik görünüyor.",
        "İndirme yarım kalmış ya da dosya hasarlı olabilir.",
        "Dosyayı yeniden indir ya da başka bir kopyasını seç."),
    "ffmpeg.no_encoder": (
        "Bu çıktı biçimi bu bilgisayarda yok",
        "FFmpeg gerekli kodlayıcıyı bulamadı.",
        "Kurulu FFmpeg sürümünde bu codec olmayabilir.",
        "FFmpeg'i tam sürümle yeniden kur."),
    "ffmpeg.permission": (
        "FFmpeg bu klasöre yazamadı",
        "Çıktı klasörüne yazma izni yok.",
        "Klasör korumalı ya da başka bir program kullanıyor olabilir.",
        "Başka bir çıktı klasörü seç."),
    "ffmpeg.failed": (
        "Video işlemi tamamlanamadı",
        "FFmpeg beklenmedik bir hatayla durdu.",
        "Girdi, bir ayar ya da geçici bir sorun neden olmuş olabilir.",
        "İşlemi yeniden dene. Sürerse “Ayrıntıları kopyala” ile bilgiyi paylaş."),
    "ytdlp.geo_blocked": (
        "Bu video bölgende açılamıyor",
        "Video, bulunduğun ülkede erişime kapalı.",
        "İçerik sahibi yayını bu bölgede kısıtlamış olabilir.",
        "Bu videoyu bu bağlantıyla indiremezsin; başka bir kaynak dene."),
    "ytdlp.age_restricted": (
        "Video yaş sınırlı",
        "Bu video yaş doğrulaması istiyor.",
        "Platform bu içerik için giriş ya da yaş onayı istiyor.",
        "Uygulama bu videoyu indiremez; başka bir kopya dene."),
    "ytdlp.private": (
        "Video özel ya da üyelere açık",
        "Bu video herkese açık değil.",
        "Sahibi videoyu gizli tutuyor ya da üyelik arkasına koymuş olabilir.",
        "Videonun herkese açık bağlantısını kullan."),
    "ytdlp.unavailable": (
        "Video artık yayında değil",
        "Video silinmiş ya da kaldırılmış görünüyor.",
        "İçerik sahibi videoyu kaldırmış olabilir.",
        "Bağlantının doğru olduğunu kontrol et."),
    "ytdlp.unsupported_url": (
        "Bu bağlantı desteklenmiyor",
        "Uygulama bu adresten video okuyamıyor.",
        "Adres bir video sayfası değil ya da site desteklenmiyor.",
        "Video sayfasının tam bağlantısını yapıştır."),
    "ytdlp.connection": (
        "İndirme yarıda kesildi",
        "Bağlantı indirme sırasında koptu.",
        "İnternet bağlantın zayıf ya da kesik olmuş olabilir.",
        "Bağlanınca “Yeniden dene”ye bas."),
    "ytdlp.http_403": (
        "Video sunucusu erişimi reddetti",
        "Bu video için erişim reddedildi (403).",
        "Video sitesi değişmiş ya da indirici güncel olmayabilir.",
        "Ayarlardan indiriciyi güncelle, sonra yeniden dene."),
    "ytdlp.generic": (
        "İndirme tamamlanamadı",
        "Video indirici beklenmedik bir hatayla durdu.",
        "Bağlantı, adres ya da indirici sürümü neden olmuş olabilir.",
        "Yeniden dene; sürerse ayarlardan indiriciyi güncelle."),
    "downloader.generic": (
        "Video indirilemedi",
        "Video indirilemedi ve tam sebebi belirlenemedi.",
        "Bağlantı, sitenin kendisi ya da bu videonun biçimi indiriciyle uyumsuz olabilir.",
        "Bağlantıyı tarayıcıdan yeniden kopyalayıp dene; olmazsa kaliteyi ya da biçimi bir kademe değiştir."),
    "http.unauthorized": (
        "Hizmet anahtarın kabul edilmedi",
        "Sağlayıcı isteği yetkisiz buldu (401).",
        "Anahtar yanlış, süresi dolmuş ya da eksik olabilir.",
        "Ayarlardaki anahtarı kontrol et ve yeniden gir."),
    "http.forbidden": (
        "Bu işlem için izin yok",
        "Sunucu isteği reddetti (403).",
        "Hesabın bu özelliğe erişimi olmayabilir.",
        "Hesap ayarlarını kontrol et ya da başka bir sağlayıcı seç."),
    "http.rate_limited": (
        "Çok fazla istek gönderildi",
        "Sağlayıcı kısa süreliğine istekleri sınırladı (429).",
        "Kota ya da hız sınırına takılmış olabilirsin.",
        "Birkaç dakika bekleyip yeniden dene."),
    "http.server_error": (
        "Sağlayıcı sunucusunda sorun var",
        "Sunucu isteğe yanıt veremedi (5xx).",
        "Sunucu geçici olarak bakımda ya da yoğun olabilir.",
        "Biraz sonra yeniden dene ya da yedek sağlayıcıyı kullan."),
    "http.timeout": (
        "Sağlayıcı zamanında yanıt vermedi",
        "İstek zaman aşımına uğradı.",
        "Bağlantı yavaş ya da sağlayıcı yoğun olabilir.",
        "Biraz bekleyip yeniden dene; yedek sağlayıcıya geçebilirsin."),
    "cuda.oom": (
        "Ekran kartının belleği yetmedi",
        "Yerel AI modeli GPU belleğine sığmadı.",
        "Model büyük ya da başka programlar GPU belleğini kullanıyor olabilir.",
        "Daha küçük bir model seç ya da diğer programları kapat."),
    "cuda.unavailable": (
        "Ekran kartı AI için kullanılamıyor",
        "GPU sürücüsü ya da CUDA bu işlemi desteklemiyor.",
        "Sürücü eksik ya da eski olabilir.",
        "Ekran kartı sürücünü güncelle; olmazsa işlemci modunu kullan."),
    "audio.no_device": (
        "Mikrofon bulunamadı",
        "Ses kaydı için bir giriş cihazı açılamadı.",
        "Mikrofon takılı değil, kapalı ya da başka bir uygulama kullanıyor.",
        "Mikrofonu bağla ve ses ayarlarından giriş cihazını seç."),
}

_EN: Dict[str, Tuple[str, str, str, str]] = {
    "ffmpeg.disk_full": (
        "There's no room left for the video job",
        "FFmpeg ran out of disk space while writing the file.",
        "The disk that holds the project may be full.",
        "Free up space or move the project to a disk with more room."),
    "ffmpeg.corrupt_input": (
        "The video file can't be read",
        "The source file looks damaged or incomplete.",
        "The download may have stopped halfway or the file is damaged.",
        "Download it again or choose another copy."),
    "ffmpeg.no_encoder": (
        "This output format isn't available here",
        "FFmpeg couldn't find the encoder it needs.",
        "The installed FFmpeg build may lack this codec.",
        "Reinstall FFmpeg with the full build."),
    "ffmpeg.permission": (
        "FFmpeg couldn't write to this folder",
        "There's no permission to write to the output folder.",
        "The folder may be protected or in use by another program.",
        "Choose another output folder."),
    "ffmpeg.failed": (
        "The video job couldn't finish",
        "FFmpeg stopped with an unexpected error.",
        "The input, a setting or a temporary problem may be the cause.",
        "Try again. If it persists, share the info with “Copy details”."),
    "ytdlp.geo_blocked": (
        "This video is blocked in your region",
        "The video isn't available in your country.",
        "The owner may have restricted it to other regions.",
        "You can't download it this way; try another source."),
    "ytdlp.age_restricted": (
        "This video is age-restricted",
        "This video asks for age verification.",
        "The platform requires sign-in or age confirmation for this content.",
        "The app can't download it; try another copy."),
    "ytdlp.private": (
        "This video is private or members-only",
        "This video isn't public.",
        "The owner may keep it private or behind membership.",
        "Use a public link to the video."),
    "ytdlp.unavailable": (
        "The video is no longer available",
        "The video appears to be deleted or removed.",
        "The owner may have removed it.",
        "Check that the link is correct."),
    "ytdlp.unsupported_url": (
        "This link isn't supported",
        "The app can't read a video from this address.",
        "The address isn't a video page, or the site isn't supported.",
        "Paste the full link to the video page."),
    "ytdlp.connection": (
        "The download was interrupted",
        "The connection dropped during the download.",
        "Your internet may be weak or was cut off.",
        "Press “Try again” once you're back online."),
    "ytdlp.http_403": (
        "The video server refused access",
        "Access to this video was denied (403).",
        "The site may have changed, or the downloader may be out of date.",
        "Update the downloader in settings, then try again."),
    "ytdlp.generic": (
        "The download couldn't finish",
        "The video downloader stopped with an unexpected error.",
        "The link, the connection or the downloader version may be the cause.",
        "Try again; if it persists, update the downloader in settings."),
    "downloader.generic": (
        "The video could not be downloaded",
        "The video could not be downloaded and the exact cause is unknown.",
        "The link, the site, or this video's format may not work with the downloader.",
        "Copy the link again from your browser and retry; if it fails, change the quality or format one step."),
    "http.unauthorized": (
        "Your service key was not accepted",
        "The provider rejected the request as unauthorized (401).",
        "The key may be wrong, expired or missing.",
        "Check the key in settings and enter it again."),
    "http.forbidden": (
        "This action isn't allowed",
        "The server refused the request (403).",
        "Your account may not have access to this feature.",
        "Check your account or choose another provider."),
    "http.rate_limited": (
        "Too many requests were sent",
        "The provider temporarily limited requests (429).",
        "You may have hit a quota or rate limit.",
        "Wait a few minutes and try again."),
    "http.server_error": (
        "The provider's server has a problem",
        "The server couldn't answer the request (5xx).",
        "The server may be under maintenance or busy.",
        "Try again later or use a backup provider."),
    "http.timeout": (
        "The provider didn't answer in time",
        "The request timed out.",
        "The connection may be slow or the provider busy.",
        "Wait a moment and retry; you can switch to a backup provider."),
    "cuda.oom": (
        "Not enough graphics card memory",
        "The local AI model didn't fit in GPU memory.",
        "The model may be large, or other programs are using GPU memory.",
        "Choose a smaller model or close other programs."),
    "cuda.unavailable": (
        "The graphics card can't be used for AI",
        "The GPU driver or CUDA doesn't support this job.",
        "The driver may be missing or outdated.",
        "Update the graphics driver, or use CPU mode."),
    "audio.no_device": (
        "No microphone found",
        "No audio input device could be opened for recording.",
        "The microphone may be unplugged, turned off, or in use by another app.",
        "Connect the microphone and pick the input device in sound settings."),
}


def _register_texts() -> None:
    tables: Dict[str, Dict[str, str]] = {"tr": {}, "en": {}}
    for lang, source in (("tr", _TR), ("en", _EN)):
        for rid, parts in source.items():
            for field_name, value in zip(_FIELDS, parts):
                tables[lang][f"err.cat.{rid}.{field_name}"] = value
    register_strings(tables)


_register_texts()


# ---------------------------------------------------------------------------
# Bağlam yardımcıları (yan etkisiz)
# ---------------------------------------------------------------------------
def _blob(exc: BaseException, ctx: ErrorContext) -> str:
    parts = (str(exc), ctx.get("stderr"), ctx.get("technical"))
    return " ".join(str(p) for p in parts if p).lower()


def _has(exc: BaseException, ctx: ErrorContext, *phrases: str) -> bool:
    text = _blob(exc, ctx)
    return any(p in text for p in phrases)


def _tool(ctx: ErrorContext) -> str:
    return str(ctx.get("tool") or "").lower()


def _names(exc: BaseException) -> set:
    return {c.__name__ for c in type(exc).__mro__}


def _status(exc: BaseException, ctx: ErrorContext) -> Optional[int]:
    value = ctx.get("status")
    if isinstance(value, int):
        return value
    candidates = (getattr(exc, "code", None),                                  # urllib HTTPError
                  getattr(getattr(exc, "response", None), "status_code", None))  # httpx
    for candidate in candidates:
        if isinstance(candidate, int) and 100 <= candidate <= 599:
            return candidate
    return None


def _enospc(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT)


def _transport(exc: BaseException, ctx: ErrorContext) -> bool:
    """Ağ ya da zaman aşımı türü hata (HTTP durumu taşımayan). Bu hatalar yerleşik kartlara bırakılır."""
    if _status(exc, ctx) is not None:
        return False
    if isinstance(exc, (ConnectionError, TimeoutError, socket.timeout, socket.gaierror)):
        return True
    return any("Timeout" in n or "Connect" in n or n == "URLError" for n in _names(exc))


def _is_ffmpeg(exc: BaseException, ctx: ErrorContext) -> bool:
    return _tool(ctx) == "ffmpeg" or "ffmpeg" in _blob(exc, ctx)


def _is_ytdlp(exc: BaseException, ctx: ErrorContext) -> bool:
    return _tool(ctx) == "yt-dlp" or _has(exc, ctx, "yt-dlp", "[youtube]", "[generic]")


# ---------------------------------------------------------------------------
# Eşleşme kuralları
# ---------------------------------------------------------------------------
def _m_ffmpeg_disk(e: BaseException, c: ErrorContext) -> bool:
    return _is_ffmpeg(e, c) and (_enospc(e) or _has(e, c, "no space left"))


def _m_ffmpeg_corrupt(e: BaseException, c: ErrorContext) -> bool:
    return _is_ffmpeg(e, c) and _has(e, c, "invalid data found", "moov atom not found",
                                     "error while decoding", "partial file", "invalid nal")


def _m_ffmpeg_encoder(e: BaseException, c: ErrorContext) -> bool:
    return _is_ffmpeg(e, c) and _has(e, c, "unknown encoder", "encoder not found",
                                     "not currently supported in container", "unknown decoder")


def _m_ffmpeg_permission(e: BaseException, c: ErrorContext) -> bool:
    return _is_ffmpeg(e, c) and (isinstance(e, PermissionError) or _has(e, c, "permission denied"))


def _m_ffmpeg_failed(e: BaseException, c: ErrorContext) -> bool:
    # Dosya, izin ve ağ hataları yerleşik kartlara bırakılır (tool.missing, fs.*, net.*).
    return _tool(c) == "ffmpeg" and not isinstance(e, OSError) and not _transport(e, c)


def _m_ytdlp_geo(e: BaseException, c: ErrorContext) -> bool:
    return _is_ytdlp(e, c) and _has(e, c, "not available in your country", "geo restricted",
                                    "geo-restricted", "blocked in your country")


def _m_ytdlp_age(e: BaseException, c: ErrorContext) -> bool:
    return _is_ytdlp(e, c) and _has(e, c, "sign in to confirm your age", "age-restricted",
                                    "age restricted")


def _m_ytdlp_private(e: BaseException, c: ErrorContext) -> bool:
    return _is_ytdlp(e, c) and _has(e, c, "private video", "this video is private",
                                    "members-only", "members only")


def _m_ytdlp_unavailable(e: BaseException, c: ErrorContext) -> bool:
    return _is_ytdlp(e, c) and _has(e, c, "video unavailable", "this video is unavailable",
                                    "video has been removed", "video is no longer available")


def _m_ytdlp_unsupported(e: BaseException, c: ErrorContext) -> bool:
    return _is_ytdlp(e, c) and _has(e, c, "unsupported url")


def _m_ytdlp_connection(e: BaseException, c: ErrorContext) -> bool:
    return _is_ytdlp(e, c) and _has(e, c, "incompleteread", "connection reset", "remote end closed",
                                    "connection aborted", "unexpected_eof_while_reading",
                                    "response ended prematurely")


def _m_ytdlp_403(e: BaseException, c: ErrorContext) -> bool:
    return _is_ytdlp(e, c) and (_status(e, c) == 403 or _has(e, c, "http error 403"))


def _m_ytdlp_generic(e: BaseException, c: ErrorContext) -> bool:
    return _is_ytdlp(e, c) and not isinstance(e, OSError) and not _transport(e, c)


def _m_401(e: BaseException, c: ErrorContext) -> bool:
    return _status(e, c) == 401


def _m_403(e: BaseException, c: ErrorContext) -> bool:
    return _status(e, c) == 403


def _m_429(e: BaseException, c: ErrorContext) -> bool:
    return _status(e, c) == 429


def _m_5xx(e: BaseException, c: ErrorContext) -> bool:
    status = _status(e, c)
    return status is not None and 500 <= status <= 599


def _m_timeout(e: BaseException, c: ErrorContext) -> bool:
    if _status(e, c) is not None:
        return False
    timed_out = isinstance(e, TimeoutError) or any("Timeout" in n for n in _names(e))
    return timed_out and (_tool(c) in ("http", "ai", "provider") or bool(c.get("provider")))


def _m_cuda_oom(e: BaseException, c: ErrorContext) -> bool:
    return (_has(e, c, "cuda out of memory", "cublas_status_alloc_failed", "cudnn_status_alloc_failed")
            or "OutOfMemoryError" in _names(e))


def _m_cuda_unavailable(e: BaseException, c: ErrorContext) -> bool:
    return _has(e, c, "no cuda-capable device", "cuda driver version is insufficient",
                "no kernel image is available")


def _m_audio(e: BaseException, c: ErrorContext) -> bool:
    return ("PortAudioError" in _names(e)
            or _has(e, c, "no default input device", "invalid input device",
                    "error querying device", "no input device"))


# ---------------------------------------------------------------------------
# Kart kurucuları
# ---------------------------------------------------------------------------
def _steps(rid: str) -> Tuple[str, ...]:
    key = f"err.cat.{rid}.step1"
    return (t(key),) if has(key) else ()


def _tech(exc: BaseException, ctx: ErrorContext) -> str:
    stderr = ctx.get("stderr")
    if stderr:
        tail = "\n".join(str(stderr).strip().splitlines()[-6:])
        if tail:
            return tail[:800]
    return f"{type(exc).__name__}: {exc}"[:800]


def _card(rid: str, exc: BaseException, ctx: ErrorContext, *, actions: Tuple[SmartAction, ...] = (),
          retryable: bool = False) -> SmartError:
    return SmartError(
        rid,
        t(f"err.cat.{rid}.title"),
        t(f"err.cat.{rid}.summary"),
        cause=t(f"err.cat.{rid}.cause"),
        steps=_steps(rid),
        actions=actions,
        severity=Severity.ERROR,
        technical=_tech(exc, ctx),
        retryable=retryable,
        source=str(ctx.get("source") or ctx.get("tool") or ""),
    )


def _retry() -> SmartAction:
    return SmartAction("retry", t("err.action.retry"), "retry")


def _settings(section: str) -> SmartAction:
    return SmartAction("open_settings", t("err.action.open_settings"), "open_settings",
                       {"section": section})


def _copy(text: str) -> SmartAction:
    return SmartAction("copy", t("err.action.copy_details"), "copy", {"text": text})


Actions = Callable[[BaseException, ErrorContext], Tuple[SmartAction, ...]]


def _rule(rid: str, matches: Callable[[BaseException, ErrorContext], bool], priority: int, *,
          actions: Optional[Actions] = None, retryable: bool = False) -> ErrorRule:
    def build(exc: BaseException, ctx: ErrorContext) -> SmartError:
        acts = actions(exc, ctx) if actions else ()
        return _card(rid, exc, ctx, actions=acts, retryable=retryable)

    return ErrorRule(rid, matches, build, priority)


def _retry_only(_e: BaseException, _c: ErrorContext) -> Tuple[SmartAction, ...]:
    return (_retry(),)


RULES: Tuple[ErrorRule, ...] = tuple(sorted((
    # FFmpeg — özel kurallar önce
    _rule("ffmpeg.disk_full", _m_ffmpeg_disk, 10, actions=_retry_only, retryable=True),
    _rule("ffmpeg.corrupt_input", _m_ffmpeg_corrupt, 20),
    _rule("ffmpeg.no_encoder", _m_ffmpeg_encoder, 30),
    _rule("ffmpeg.permission", _m_ffmpeg_permission, 40),
    # yt-dlp — kaynağa özgü kurallar
    _rule("ytdlp.geo_blocked", _m_ytdlp_geo, 60),
    _rule("ytdlp.age_restricted", _m_ytdlp_age, 65),
    _rule("ytdlp.private", _m_ytdlp_private, 70),
    _rule("ytdlp.unavailable", _m_ytdlp_unavailable, 75),
    _rule("ytdlp.unsupported_url", _m_ytdlp_unsupported, 80),
    # FFmpeg genel yedeği (yalnız araç açıkça ffmpeg ise)
    _rule("ffmpeg.failed", _m_ffmpeg_failed, 90, actions=_retry_only, retryable=True),
    _rule("ytdlp.connection", _m_ytdlp_connection, 135, actions=_retry_only, retryable=True),
    _rule("ytdlp.http_403", _m_ytdlp_403, 140,
          actions=lambda e, c: (_settings("updates"), _retry())),
    _rule("ytdlp.generic", _m_ytdlp_generic, 190, retryable=True,
          actions=lambda e, c: (_retry(), _copy(_tech(e, c)))),
    # İndirici genel yedeği: kaynak "downloader" ve özel kural eşleşmedi; ağ/izin/disk hataları yerleşik kartlara kalır
    _rule("downloader.generic",
          lambda e, c: c.get("source") == "downloader" and not isinstance(e, OSError) and not _transport(e, c),
          450, retryable=True, actions=lambda e, c: (_retry(), _copy(_tech(e, c)))),
    # HTTP
    _rule("http.unauthorized", _m_401, 200, actions=lambda e, c: (_settings("ai"),)),
    _rule("http.forbidden", _m_403, 210),
    _rule("http.rate_limited", _m_429, 220, actions=_retry_only, retryable=True),
    _rule("http.server_error", _m_5xx, 230, actions=_retry_only, retryable=True),
    _rule("http.timeout", _m_timeout, 240, retryable=True,
          actions=lambda e, c: (_retry(), _settings("fallback"))),
    # CUDA ve ses girişi
    _rule("cuda.oom", _m_cuda_oom, 300),
    _rule("cuda.unavailable", _m_cuda_unavailable, 305,
          actions=lambda e, c: (_settings("system"),)),
    _rule("audio.no_device", _m_audio, 400,
          actions=lambda e, c: (_settings("audio"),)),
), key=lambda r: r.priority))
