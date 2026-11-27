"""Uygulamanın çalıştığı cihaz: masaüstü, Android telefon ya da Android tablet (``docs/MOBILE_PLAN.md``, M1–M2).

* ``is_android()`` — Android'de mi çalışıyoruz? (CPython Android yapısı ``sys.platform == "android"`` der ve
  ``sys.getandroidapilevel`` sunar; python-for-android ayrıca ``ANDROID_ARGUMENT`` / ``ANDROID_PRIVATE`` ortam
  değişkenlerini koyar.)
* ``form_factor()`` — ``desktop`` | ``phone`` | ``tablet``. Android'de ekranın **en küçük genişliği** 600 dp ve
  üstüyse tablet (Android'in ``sw600dp`` eşiği). Telefonda yalnız Basit Mod; tablette masaüstündeki gibi iki mod
  (``legend_allowed()``). ``is_mobile()``: telefon ya da tablet.
* ``android_private_dir()`` — uygulamanın özel (iç) dosya klasörü; ``~/.kadraj`` karşılığı bunun altındadır.
* ``native_library_dir()`` — APK'nın yerel kütüphane klasörü (``nativeLibraryDir``). Android 10+ yazılabilir
  klasörden ikili çalıştırmaya izin vermez (W^X); FFmpeg gibi araçlar ``lib<ad>.so`` adıyla bu klasöre gömülür.

Modül Qt'ye bağlı değildir (ekran ölçüsü yalnız ``form_factor`` çağrılınca, Qt varsa okunur) ve ağa çıkmaz.
Geliştirme/test için taklit: ``KADRAJ_PLATFORM=android``, ``KADRAJ_FORM_FACTOR=phone|tablet|desktop``,
``KADRAJ_ANDROID_PRIVATE=<klasör>``, ``KADRAJ_ANDROID_LIBDIR=<klasör>``.

Not: Plan bu modülü ``kadraj/platform.py`` diye anıyordu; standart kütüphanedeki ``platform`` modülünü gölgelememek
için adı ``device`` oldu.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

DESKTOP, PHONE, TABLET = "desktop", "phone", "tablet"
FORM_FACTORS = (DESKTOP, PHONE, TABLET)
#: Android'in tablet eşiği (``sw600dp``): ekranın kısa kenarı bu kadar bağımsız pikselse tablet sayılır.
TABLET_MIN_DP = 600

#: ``/proc/self/maps`` içinde yerel kütüphane klasörünü ele veren, uygulamayla gelen kütüphaneler.
_LIB_MARKERS = ("libpython3", "libmain.so", "libQt6Core", "libpyside6", "libshiboken6")


def is_android() -> bool:
    """Android'de mi çalışıyoruz? ``KADRAJ_PLATFORM`` verilmişse o kazanır (``android`` ya da başka bir değer)."""
    forced = os.environ.get("KADRAJ_PLATFORM", "").strip().lower()
    if forced:
        return forced == "android"
    return (sys.platform == "android" or hasattr(sys, "getandroidapilevel")
            or "ANDROID_ARGUMENT" in os.environ or "ANDROID_PRIVATE" in os.environ)


def android_api_level() -> int:
    """Cihazın Android API düzeyi (ör. 34). Android değilse ya da bilinmiyorsa 0.

    Python 3.13+ ``platform.android_ver().api_level`` cihazın gerçek düzeyini verir. ``sys.getandroidapilevel()``
    ise Python'un derlendiği (en düşük) düzeydir; yalnız yedek olarak kullanılır.
    """
    if is_android():
        try:
            import platform
            ver = getattr(platform, "android_ver", None)
            level = int(ver().api_level) if ver is not None else 0
        except Exception:  # noqa: BLE001
            level = 0
        if level > 0:
            return level
    get = getattr(sys, "getandroidapilevel", None)
    if get is None:
        return 0
    try:
        return int(get())
    except Exception:  # noqa: BLE001
        return 0


def classify(smallest_width_dp: Optional[float]) -> str:
    """Kısa kenar (dp) → ``phone`` | ``tablet``. Ölçü bilinmiyorsa güvenli taraf: ``phone``."""
    if smallest_width_dp is None or smallest_width_dp <= 0:
        return PHONE
    return TABLET if smallest_width_dp >= TABLET_MIN_DP else PHONE


def screen_smallest_dp() -> Optional[float]:
    """Birincil ekranın kısa kenarı, cihazdan bağımsız piksel olarak (Qt yoksa ya da ekran yoksa ``None``).

    Qt 6 yüksek DPI ölçeklemesiyle ``QScreen.size()`` mantıksal piksel döner; Android'de bu dp'ye karşılık gelir.
    """
    try:
        from PySide6.QtGui import QGuiApplication
    except Exception:  # noqa: BLE001
        return None
    app = QGuiApplication.instance()
    screen = QGuiApplication.primaryScreen() if app is not None else None
    if screen is None:
        return None
    size = screen.size()
    return float(min(size.width(), size.height()))


def form_factor(smallest_width_dp: Optional[float] = None) -> str:
    """``desktop`` | ``phone`` | ``tablet``. ``KADRAJ_FORM_FACTOR`` verilmişse o kazanır."""
    forced = os.environ.get("KADRAJ_FORM_FACTOR", "").strip().lower()
    if forced in FORM_FACTORS:
        return forced
    if not is_android():
        return DESKTOP
    if smallest_width_dp is None:
        smallest_width_dp = screen_smallest_dp()
    return classify(smallest_width_dp)


def is_phone() -> bool:
    return form_factor() == PHONE


def is_tablet() -> bool:
    return form_factor() == TABLET


def is_mobile() -> bool:
    """Telefon ya da tablet (dokunmatik, tam ekran pencere). ``KADRAJ_FORM_FACTOR`` ile masaüstünde de denenir."""
    return form_factor() != DESKTOP


def legend_allowed() -> bool:
    """Legend Mod bu cihazda açılabilir mi? Telefonda yalnız Basit Mod (M1); tablette ve masaüstünde iki mod (M2)."""
    return form_factor() != PHONE


def android_private_dir() -> Optional[Path]:
    """Uygulamanın özel dosya klasörü (``Context.getFilesDir()``). Android değilse ``None``.

    Sıra: ``KADRAJ_ANDROID_PRIVATE`` → python-for-android'in ``ANDROID_PRIVATE`` / ``ANDROID_ARGUMENT`` değişkenleri
    → ``HOME`` (yazılabilirse).
    """
    override = os.environ.get("KADRAJ_ANDROID_PRIVATE")
    if override:
        return Path(override)
    if not is_android():
        return None
    for var in ("ANDROID_PRIVATE", "ANDROID_ARGUMENT"):
        value = os.environ.get(var)
        if value:
            return Path(value)
    home = os.environ.get("HOME")
    if home and os.access(home, os.W_OK):
        return Path(home)
    return None


def libdir_from_maps(text: str) -> Optional[Path]:
    """``/proc/self/maps`` metninden uygulamanın yerel kütüphane klasörü.

    Kütüphaneler APK'nın içinden (``base.apk!/lib/...``) yükleniyorsa ``None`` döner: o durumda dosyalar diske
    açılmamıştır ve oradan ikili çalıştırılamaz (APK ``extractNativeLibs=true`` ile paketlenmelidir).
    """
    for line in text.splitlines():
        parts = line.split(maxsplit=5)
        if len(parts) < 6:
            continue
        path = parts[5].strip()
        if not path.startswith("/") or "!" in path:
            continue
        name = os.path.basename(path)
        if name.endswith(".so") or ".so." in name:
            if any(name.startswith(marker) for marker in _LIB_MARKERS):
                return Path(path).parent
    return None


def native_library_dir() -> Optional[Path]:
    """APK'nın açılmış yerel kütüphane klasörü (``ApplicationInfo.nativeLibraryDir``). Android değilse ``None``."""
    override = os.environ.get("KADRAJ_ANDROID_LIBDIR")
    if override:
        return Path(override)
    if not is_android():
        return None
    try:
        with open("/proc/self/maps", encoding="utf-8", errors="replace") as fh:
            return libdir_from_maps(fh.read())
    except OSError:
        return None
