"""Profiller — ``~/.kadraj/profiles/`` (plan §6).

Her profilin kendi ayar katmanı vardır (``<id>.json``): arayüz modu ve erişilebilirlik
tercihleri. Kasa ad alanı (``Profile.vault_namespace``) profil kimliğinden türetilir:
``profile.<id>``; kasa tarafı sırları bu ad alanına göre tutar (Adım 3). Etkin profil
``index.json`` içinde tutulur. Profil silmek kasadaki sırları silmez.

Oluşturma, değiştirme, geçiş ve silme hata durumunda ``OSError`` yükseltir: kullanıcı
eylemi sessizce kaybolmasın. Okuma bozuk dosyada varsayılan değerlere döner.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .. import paths
from ..core.i18n import register_strings, t

DEFAULT_ID = "default"
DEFAULT_NAME = "Varsayılan"
FORMAT_VERSION = 1
UI_MODES = ("simple", "legend")
A11Y_KEYS = ("large_text", "high_contrast", "reduced_motion")
MAX_NAME = 40
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_UPPER_TR = str.maketrans({"Ç": "c", "Ğ": "g", "İ": "i", "I": "i", "Ö": "o", "Ş": "s", "Ü": "u"})
_LOWER_TR = str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
                           "â": "a", "î": "i", "û": "u"})

register_strings({
    "tr": {
        "storage.profile.name_invalid": "Profil adı en az bir harf ya da rakam içermeli.",
        "storage.profile.exists": "Bu adda bir profil zaten var.",
        "storage.profile.not_found": "Profil bulunamadı.",
        "storage.profile.default_locked": "Varsayılan profil silinemez.",
        "storage.profile.mode_invalid": "Geçersiz arayüz modu.",
    },
    "en": {
        "storage.profile.name_invalid": "The profile name needs at least one letter or digit.",
        "storage.profile.exists": "A profile with this name already exists.",
        "storage.profile.not_found": "Profile not found.",
        "storage.profile.default_locked": "The default profile can't be deleted.",
        "storage.profile.mode_invalid": "Invalid interface mode.",
    },
})


class ProfileError(ValueError):
    """Profil işlemi reddedildi. ``code`` kararlı kimliktir; metin Basit Türkçedir."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _err(code: str) -> ProfileError:
    return ProfileError(code, t(f"storage.profile.{code}"))


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    ui_mode: str
    a11y: Dict[str, bool]
    created: float
    active: bool = False

    @property
    def vault_namespace(self) -> str:
        return f"profile.{self.id}"


def _valid_id(pid: Any) -> bool:
    return isinstance(pid, str) and len(pid) <= MAX_NAME and bool(_ID_RE.match(pid))


def slugify(name: Any) -> str:
    """Profil adını dosya-güvenli kimliğe çevirir (Türkçe harfler sadeleşir). Boşsa ``ProfileError``."""
    text = str(name or "").translate(_UPPER_TR).lower().translate(_LOWER_TR)
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:MAX_NAME].rstrip("-")
    if not slug:
        raise _err("name_invalid")
    return slug


# ---------------------------------------------------------------------------
# Dosya yardımcıları
# ---------------------------------------------------------------------------
def _index_file() -> Path:
    return paths.profiles_dir() / "index.json"


def _layer_file(pid: str) -> Path:
    return paths.profiles_dir() / f"{pid}.json"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(dict(data), fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _layer_from(pid: str, raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Ham katmanı güvenli bir profil kaydına çevirir; eksik ya da bozuk alanlar varsayılana döner."""
    src: Mapping[str, Any] = raw or {}
    name = src.get("name")
    a11y_src = src.get("a11y") if isinstance(src.get("a11y"), dict) else {}
    created = src.get("created")
    if isinstance(name, str) and name.strip():
        display = name.strip()[:MAX_NAME]
    else:
        display = DEFAULT_NAME if pid == DEFAULT_ID else pid
    return {
        "version": FORMAT_VERSION,
        "id": pid,
        "name": display,
        "created": float(created) if isinstance(created, (int, float)) else 0.0,
        "ui_mode": src.get("ui_mode") if src.get("ui_mode") in UI_MODES else "simple",
        "a11y": {k: bool(a11y_src.get(k, False)) for k in A11Y_KEYS},
    }


def _to_profile(pid: str, raw: Optional[Mapping[str, Any]], active_id: str) -> Profile:
    layer = _layer_from(pid, raw)
    return Profile(id=pid, name=layer["name"], ui_mode=layer["ui_mode"],
                   a11y=dict(layer["a11y"]), created=layer["created"], active=(pid == active_id))


def _load_index() -> Dict[str, Any]:
    raw = _read_json(_index_file()) or {}
    order: List[str] = []
    for pid in raw.get("order") if isinstance(raw.get("order"), list) else []:
        if _valid_id(pid) and pid not in order:
            order.append(pid)
    if DEFAULT_ID not in order:
        order.insert(0, DEFAULT_ID)
    active = raw.get("active")
    if not (_valid_id(active) and active in order):
        active = DEFAULT_ID
    return {"active": active, "order": order}


def _save_index(index: Mapping[str, Any]) -> None:
    _write_json(_index_file(), {"version": FORMAT_VERSION,
                                "active": index["active"], "order": list(index["order"])})


def _ensure_default() -> None:
    """Varsayılan profil dosyasını yazmaya çalışır; yazılamazsa bellekteki varsayılanla devam edilir."""
    try:
        if not _layer_file(DEFAULT_ID).exists():
            _write_json(_layer_file(DEFAULT_ID), _layer_from(DEFAULT_ID, {"created": time.time()}))
    except OSError:
        pass


def _lookup(pid: str, index: Mapping[str, Any]) -> Profile:
    if not _valid_id(pid) or pid not in index["order"]:
        raise _err("not_found")
    raw = _read_json(_layer_file(pid))
    if raw is None and pid != DEFAULT_ID:
        raise _err("not_found")
    return _to_profile(pid, raw, index["active"])


# ---------------------------------------------------------------------------
# Genel API
# ---------------------------------------------------------------------------
def list_profiles() -> List[Profile]:
    """Profilleri sıralı döner. Katmanı kalmamış (bozuk kayıt) profiller gösterilmez."""
    _ensure_default()
    index = _load_index()
    out: List[Profile] = []
    for pid in index["order"]:
        raw = _read_json(_layer_file(pid))
        if raw is None and pid != DEFAULT_ID:
            continue
        out.append(_to_profile(pid, raw, index["active"]))
    return out


def get_profile(pid: str) -> Profile:
    _ensure_default()
    return _lookup(pid, _load_index())


def active_profile() -> Profile:
    """Etkin profil. Etkin kayıt bozuksa varsayılana düşer (asla istisna atmaz)."""
    _ensure_default()
    index = _load_index()
    try:
        return _lookup(index["active"], index)
    except ProfileError:
        return _lookup(DEFAULT_ID, index)


def create_profile(name: str, *, ui_mode: str = "simple",
                   a11y: Optional[Mapping[str, bool]] = None) -> Profile:
    display = " ".join(str(name or "").split())[:MAX_NAME]
    pid = slugify(display)
    if ui_mode not in UI_MODES:
        raise _err("mode_invalid")
    _ensure_default()
    index = _load_index()
    if pid in index["order"] or _layer_file(pid).exists():
        raise _err("exists")
    layer = _layer_from(pid, {
        "name": display, "ui_mode": ui_mode, "created": time.time(),
        "a11y": {k: bool(v) for k, v in (a11y or {}).items() if k in A11Y_KEYS},
    })
    _write_json(_layer_file(pid), layer)
    index["order"].append(pid)
    _save_index(index)
    return _lookup(pid, index)


def update_profile(pid: str, *, ui_mode: Optional[str] = None,
                   a11y: Optional[Mapping[str, bool]] = None) -> Profile:
    index = _load_index()
    current = _lookup(pid, index)
    if ui_mode is not None and ui_mode not in UI_MODES:
        raise _err("mode_invalid")
    merged_a11y = dict(current.a11y)
    merged_a11y.update({k: bool(v) for k, v in (a11y or {}).items() if k in A11Y_KEYS})
    layer = _layer_from(pid, {
        "name": current.name, "created": current.created,
        "ui_mode": ui_mode or current.ui_mode, "a11y": merged_a11y,
    })
    _write_json(_layer_file(pid), layer)
    return _lookup(pid, index)


def switch_profile(pid: str) -> Profile:
    """Etkin profili değiştirir. Projeler ve geri alma geçmişi bu işlemden etkilenmez."""
    _ensure_default()
    index = _load_index()
    profile = _lookup(pid, index)
    index["active"] = pid
    _save_index(index)
    return replace(profile, active=True)


def delete_profile(pid: str) -> None:
    """Profili siler; silinen etkin profilin yerine varsayılan geçer. Varsayılan silinemez."""
    if pid == DEFAULT_ID:
        raise _err("default_locked")
    index = _load_index()
    if pid not in index["order"]:
        raise _err("not_found")
    index["order"] = [i for i in index["order"] if i != pid]
    if index["active"] == pid:
        index["active"] = DEFAULT_ID
    _save_index(index)
    try:
        _layer_file(pid).unlink()
    except FileNotFoundError:
        pass
