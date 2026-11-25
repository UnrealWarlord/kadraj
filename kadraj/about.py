"""Hakkında bilgileri ve atıflar.

KADRAJ'ın uygulama iskeleti (ayar deposu, i18n ``t()`` yapısı, onay/güncelleme/son projeler/proje
dosyası mantığı, açılış akışı) MIT lisanslı **SoilClassPro** projesinden uyarlanmıştır; özgün
yazar atfı ``LICENSE`` dosyasında korunur.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from .core.i18n import get_language

SKELETON_NAME = "SoilClassPro"
SKELETON_AUTHORS = (
    "Batuhan Barış Döğer",
    "Erol Boz",
    "Furkan Kağan Ekici",
)
SKELETON_ACKNOWLEDGED = "Nejan Huvaj"
SKELETON_LICENSE = "MIT"
COPYRIGHT_YEAR = 2026
LICENSE_NAME = "MIT"

_MONTHS = {
    "en": ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"),
    "tr": ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
           "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"),
}


def format_date(day: Optional[date] = None, lang: Optional[str] = None) -> str:
    day = day or date.today()
    months = _MONTHS.get(lang or get_language(), _MONTHS["en"])
    return f"{day.day} {months[day.month - 1]} {day.year}"


def skeleton_credit() -> str:
    return f"{SKELETON_NAME} ({SKELETON_LICENSE}) — " + ", ".join(SKELETON_AUTHORS)
