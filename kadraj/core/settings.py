"""Kalıcı ayarlar deposu — ``~/.kadraj/settings.json``.

İskeletin ``STORE_FILE`` mantığı korunur (tek JSON dosyası, bozuksa sessizce
varsayılana dön) ama şunlar eklenmiştir: atomik yazma, noktalı anahtar yolu,
şema sürümü, değişim sinyali ve bozuk dosyanın ``.bad`` olarak yedeklenmesi.
Gizli bilgi (API anahtarı) **buraya yazılmaz**; o kasanın işidir.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from PySide6.QtCore import QObject, QStandardPaths, Signal

from .. import paths
from .contracts import WizardAnswers

SCHEMA_VERSION = 1

DEFAULTS: Dict[str, Any] = {
    "schema": SCHEMA_VERSION,
    "ui_mode": "simple",                 # "simple" | "legend"
    "language": "",                      # "" → varsayılan dil (tr); sistem yerel ayarına bakılmaz
    "a11y": {
        "large_text": False,             # Büyük Yazı ×1.25
        "high_contrast": False,          # Yüksek Kontrast
        "reduced_motion": False,         # Geçişler 480 → 120 ms
    },
    "project_root": "",                  # yeni projelerin üst klasörü
    "wizard": {"done": False, "tasks": [], "api": "skip"},
    "fallback_order": {},                # {"llm": ["anthropic", "ollama"], ...}
    "last_project": "",
    "window": {"geometry": "", "legend_state": {}},   # base64; çalışma alanı başına saveState
    "workspace": "project",              # Legend'de son çalışma alanı
    "splash": True,
    "autosave_seconds": 20,
    "jobs": {"max_parallel": 2},
    "tools": {"auto_update": True},      # açılışta yt-dlp / FFmpeg / Deno'yu resmî kaynaklardan güncelle
    "notifications": {"desktop": True, "min_seconds": 5},   # masaüstü bildirimi yalnızca ≥ 5 sn süren işler için
}


def _deep_merge(base: Dict[str, Any], over: Mapping[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class SettingsStore(QObject):
    """``SettingsAPI`` uygulaması."""

    changed = Signal(str, object)        # (anahtar, yeni değer); toplu güncellemede anahtar "*"

    def __init__(self, path: Optional[Path] = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._path = Path(path) if path else paths.settings_file()
        self._data: Dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()

    # -- dosya ------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError, UnicodeDecodeError):
            self._quarantine()
            return
        if isinstance(raw, dict):
            self._data = _deep_merge(DEFAULTS, raw)
        else:
            self._quarantine()

    def _quarantine(self) -> None:
        """Bozuk dosyayı ``.bad`` olarak kenara al (veri kaybı olmasın), varsayılanla devam et."""
        try:
            self._path.replace(self._path.with_suffix(".json.bad"))
        except OSError:
            pass

    def save(self) -> bool:
        tmp = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".tmp",
                                       dir=str(self._path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
            tmp = None
            return True
        except OSError:
            return False
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # -- erişim -----------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return copy.deepcopy(node)

    def set(self, key: str, value: Any, *, save: bool = True) -> None:
        parts = key.split(".")
        node = self._data
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = node[part] = {}
            node = nxt
        if node.get(parts[-1]) == value:
            return
        node[parts[-1]] = copy.deepcopy(value)
        if save:
            self.save()
        self.changed.emit(key, value)

    def update(self, values: Mapping[str, Any]) -> None:
        for key, value in values.items():
            self.set(key, value, save=False)
        self.save()
        self.changed.emit("*", None)

    def as_dict(self) -> Dict[str, Any]:
        return copy.deepcopy(self._data)

    # -- kolaylık ---------------------------------------------------------
    @property
    def large_text(self) -> bool:
        return bool(self.get("a11y.large_text", False))

    @property
    def high_contrast(self) -> bool:
        return bool(self.get("a11y.high_contrast", False))

    @property
    def reduced_motion(self) -> bool:
        return bool(self.get("a11y.reduced_motion", False)) or \
            os.environ.get("KADRAJ_REDUCED_MOTION") == "1"


# ---------------------------------------------------------------------------
# Sihirbaz → ayarlar
# ---------------------------------------------------------------------------
def default_project_root(place: str, custom: str = "") -> Path:
    """Sihirbaz 2. soru: kayıt yeri → klasör. ``videos`` / ``desktop`` / ``custom``."""
    if place == "custom" and custom:
        return Path(custom).expanduser()
    loc = (QStandardPaths.DesktopLocation if place == "desktop"
           else QStandardPaths.MoviesLocation)
    base = QStandardPaths.writableLocation(loc)
    root = Path(base) if base else Path.home() / ("Desktop" if place == "desktop" else "Videos")
    return root / "KADRAJ_Projeler"


def apply_wizard_answers(settings: SettingsStore, answers: WizardAnswers) -> Path:
    """Cevapları ayarlara yazar ve proje kök klasörünü döner (oluşturmaz; bunu Adım 2'nin
    ``storage.folders`` modülü yapar). İlk açılışta mod her zaman **Basit**tir."""
    ans = answers.normalized()
    root = default_project_root(ans.place, ans.root_path)
    settings.update({
        "project_root": str(root),
        "wizard.done": True,
        "wizard.tasks": list(ans.tasks),
        "wizard.api": ans.api,
        "wizard.completed_at": time.time(),
    })
    return root
