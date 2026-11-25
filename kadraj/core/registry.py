"""Modül kayıt defteri — ``kadraj/modules/*`` otomatik keşfi.

Kurallar ``contracts.DISCOVERY_RULES`` içinde belgelidir. Kısaca: her alt paketin
``module.py`` dosyasındaki ``register() -> ModuleSpec`` çağrılır; hata veren modül
atlanır ve ``errors`` listesine düşer, çekirdek asla düşmez.
"""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import i18n
from .contracts import (MODULE_ENTRY, MODULE_ENTRY_FUNC, MODULES_PACKAGE,
                        Accent, ModuleSpec, SmartError)
from .errors import translate


@dataclass(frozen=True)
class PlannedModule:
    """Henüz kayıtlı olmayan ama planda var olan modül (ana ekranda "yakında" kartı)."""
    id: str
    title_key: str
    desc_key: str
    order: int
    accent: Accent
    icon: str
    workspace: str
    shortcut: str = ""
    chip: str = ""


#: Plan §1/§5'teki altı modül. ``settings`` ana ekran kartı değil, üst çubuk dişlisidir.
PLANNED: List[PlannedModule] = [
    PlannedModule("downloader", "mod.downloader.title", "mod.downloader.desc", 10,
                  Accent.PEACH, "download", "capture", "Ctrl+1"),
    PlannedModule("recorder", "mod.recorder.title", "mod.recorder.desc", 20,
                  Accent.LAVENDER, "mic", "capture", "Ctrl+2", ".wav"),
    PlannedModule("editor", "mod.editor.title", "mod.editor.desc", 30,
                  Accent.MINT, "film", "edit", "Ctrl+3"),
    PlannedModule("photo", "mod.photo.title", "mod.photo.desc", 40,
                  Accent.BUTTER, "image", "studio", "Ctrl+4"),
    PlannedModule("series", "mod.series.title", "mod.series.desc", 50,
                  Accent.PINK, "clapper", "studio", "Ctrl+5"),
    PlannedModule("settings", "mod.settings.title", "mod.settings.desc", 90,
                  Accent.NEUTRAL, "gear", "system"),
]


class ModuleRegistry:
    def __init__(self, package: str = MODULES_PACKAGE) -> None:
        self._package = package
        self._specs: Dict[str, ModuleSpec] = {}
        self.errors: List[SmartError] = []

    # -- keşif ------------------------------------------------------------
    def discover(self) -> List[ModuleSpec]:
        """Paketi tarar, bulunan modülleri kaydeder, kayıtlı listeyi döner."""
        try:
            pkg = importlib.import_module(self._package)
        except ImportError as exc:
            self.errors.append(translate(exc, {"source": self._package}))
            return []
        for info in pkgutil.iter_modules(getattr(pkg, "__path__", [])):
            if not info.ispkg or info.name.startswith("_"):
                continue
            self._load(info.name)
        return self.specs()

    def _load(self, name: str) -> Optional[ModuleSpec]:
        try:
            entry = importlib.import_module(f"{self._package}.{name}.{MODULE_ENTRY}")
        except ModuleNotFoundError as exc:
            if exc.name == f"{self._package}.{name}.{MODULE_ENTRY}":
                return None            # henüz module.py yok → sessizce yer tutucu
            self.errors.append(translate(exc, {"source": name}))
            return None
        except Exception as exc:
            self.errors.append(translate(exc, {"source": name}))
            return None
        try:
            spec = getattr(entry, MODULE_ENTRY_FUNC)()
        except Exception as exc:
            self.errors.append(translate(exc, {"source": name}))
            return None
        return self.register(spec, expected_id=name)

    def register(self, spec: ModuleSpec, expected_id: Optional[str] = None) -> Optional[ModuleSpec]:
        """Bir ``ModuleSpec``'i doğrulayıp ekler (testler ve elle kayıt için de açık)."""
        problems = spec.validate() if isinstance(spec, ModuleSpec) else ["ModuleSpec değil"]
        if expected_id and getattr(spec, "id", None) != expected_id:
            problems.append(f"spec.id ({getattr(spec, 'id', None)!r}) paket adıyla ({expected_id!r}) aynı olmalı")
        if problems:
            self.errors.append(SmartError(
                "module.invalid", f"Modül yüklenemedi: {expected_id or getattr(spec, 'id', '?')}",
                "; ".join(problems), source=str(expected_id or "")))
            return None
        self._specs[spec.id] = spec
        if spec.strings:
            i18n.register_strings(spec.strings)
        return spec

    # -- sorgu ------------------------------------------------------------
    def specs(self) -> List[ModuleSpec]:
        return sorted(self._specs.values(), key=lambda s: (s.order, s.id))

    def get(self, module_id: str) -> Optional[ModuleSpec]:
        return self._specs.get(module_id)

    def planned(self, module_id: str) -> Optional[PlannedModule]:
        return next((p for p in PLANNED if p.id == module_id), None)

    def entries(self) -> List["ModuleEntry"]:
        """Ana ekran/çalışma alanı için birleşik liste: kayıtlı modüller + eksik planlılar."""
        out: List[ModuleEntry] = []
        seen = set()
        for spec in self.specs():
            out.append(ModuleEntry(spec.id, spec.title_key, spec.desc_key, spec.order, spec.accent,
                                   spec.icon, spec.workspace, spec.shortcut, spec.chip, spec))
            seen.add(spec.id)
        for p in PLANNED:
            if p.id not in seen:
                out.append(ModuleEntry(p.id, p.title_key, p.desc_key, p.order, p.accent, p.icon,
                                       p.workspace, p.shortcut, p.chip, None))
        return sorted(out, key=lambda e: (e.order, e.id))


@dataclass(frozen=True)
class ModuleEntry:
    id: str
    title_key: str
    desc_key: str
    order: int
    accent: Accent
    icon: str
    workspace: str
    shortcut: str
    chip: str
    spec: Optional[ModuleSpec]          # None → "yakında"

    @property
    def available(self) -> bool:
        return self.spec is not None
