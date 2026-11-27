"""Çeviri katmanı — iskeletin ``t()`` yapısının KADRAJ uyarlaması.

* ``t(key, **fmt)``      : etkin dilde dizeyi döner (yoksa İngilizce, yoksa anahtarın kendisi).
* ``term(key, **fmt)``   : **mod-duyarlı terim**; etkin arayüz moduna göre ``simple.<key>`` ya da
                           ``legend.<key>`` çiftini arar (Basit: "Kayıt noktası", Legend: "Snapshot").
                           Çift yoksa ``t(key)``'e düşer.
* ``register_strings``   : modüllerin kendi dizelerini eklemesi (``ModuleSpec.strings``).
* Android'de ``ANDROID_STRINGS`` (``core/strings.py``) önce aranır: "bilgisayar" yerine "cihaz" ve Android'de
  yapılabilen adımlar (M23). Yalnız orada bulunan anahtarlar için platform sorulur.

Çekirdek tablolar ``core/strings.py`` içindedir. Modül anahtarları ``"<modül>."`` ile başlar;
çekirdek anahtarın üzerine yazmak engellenir.
"""
from __future__ import annotations

from typing import Dict, Mapping, Optional

from .. import device
from .strings import ANDROID_STRINGS, CORE_STRINGS

LANGUAGES: Dict[str, str] = {"tr": "Türkçe", "en": "English"}
DEFAULT_LANGUAGE = "tr"

_tables: Dict[str, Dict[str, str]] = {lang: dict(tbl) for lang, tbl in CORE_STRINGS.items()}
_core_keys = {lang: frozenset(tbl) for lang, tbl in _tables.items()}
_android_tables: Dict[str, Dict[str, str]] = {lang: dict(tbl) for lang, tbl in ANDROID_STRINGS.items()}
_android_keys = frozenset(key for tbl in _android_tables.values() for key in tbl)
_language: Optional[str] = None
_ui_mode: str = "simple"


def system_language() -> str:
    """İlk açılışta kullanılacak dil: **her zaman Türkçe** (karar: sistem yerel ayarından bağımsız; Docker/CI'da ``LANG`` yok ya da
    ``tr_TR`` kurulu değil). İngilizce yalnız kullanıcı Ayarlar'dan seçerse ve ayar dosyasına yazılırsa etkindir."""
    return DEFAULT_LANGUAGE


def get_language() -> str:
    global _language
    if _language is None:
        _language = system_language()
    return _language


def set_language(code: str) -> None:
    """Etkin dili ayarlar. Bilinmeyen kod → ``ValueError`` (çağıran yakalar)."""
    global _language
    if code not in LANGUAGES:
        raise ValueError(f"Bilinmeyen dil kodu: {code!r}")
    _language = code


def set_ui_mode(mode: str) -> None:
    """``term()`` için etkin mod ('simple' | 'legend'). ``AppContext`` çağırır."""
    global _ui_mode
    _ui_mode = "legend" if str(getattr(mode, "value", mode)) == "legend" else "simple"


def get_ui_mode() -> str:
    return _ui_mode


def register_strings(tables: Mapping[str, Mapping[str, str]]) -> None:
    """Modül dizelerini ekler: ``{"tr": {...}, "en": {...}}``.

    Çekirdek anahtarların üzerine yazmaz (sessizce yok sayar), böylece bir modül
    yanlışlıkla ortak metni bozamaz.
    """
    for lang, table in tables.items():
        dest = _tables.setdefault(lang, {})
        protected = _core_keys.get(lang, frozenset())
        for key, value in table.items():
            if key not in protected:
                dest[key] = value


def _lookup(key: str) -> Optional[str]:
    lang = get_language()
    if key in _android_keys and device.is_android():
        value = _android_tables.get(lang, {}).get(key)
        if value is not None:
            return value
    value = _tables.get(lang, {}).get(key)
    if value is None and lang != "en":
        value = _tables.get("en", {}).get(key)
    return value


def _fmt(text: str, fmt: Mapping[str, object]) -> str:
    if not fmt:
        return text
    try:
        return text.format(**fmt)
    except (KeyError, IndexError, ValueError):
        return text


def t(key: str, **fmt: object) -> str:
    value = _lookup(key)
    return _fmt(key if value is None else value, fmt)


def term(key: str, **fmt: object) -> str:
    """Mod-duyarlı terim (bkz. modül açıklaması)."""
    value = _lookup(f"{_ui_mode}.{key}")
    if value is None:
        value = _lookup(key)
    return _fmt(key if value is None else value, fmt)


def has(key: str) -> bool:
    return _lookup(key) is not None


def capitalize(text: str) -> str:
    """Türkçe İ/ı kurallarıyla ilk harfi büyütür."""
    if not text:
        return text
    head = text[0]
    if get_language() == "tr":
        head = {"i": "İ", "ı": "I"}.get(head, head)
    return head.upper() + text[1:]


def upper(text: str) -> str:
    if get_language() == "tr":
        text = text.replace("i", "İ").replace("ı", "I")
    return text.upper()
