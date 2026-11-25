"""İlk açılış sihirbazının veri tarafı: 3 cevap → kök klasör, ilk proje iskeleti, ayarlar.

Arayüz cevapları toplar; bu modül klasörleri kurar ve ayarları yazar. Sıra bilinçlidir:
önce klasörler (bir hata olursa ayarlar değişmez), sonra ayarlar. ``OSError`` yükselirse
çağıran taraf ``core.errors.translate`` ile kartı gösterir ve sihirbaz tamamlanmış sayılmaz.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from ..core.contracts import WizardAnswers
from ..core.i18n import register_strings, t
from ..core.settings import SettingsStore, apply_wizard_answers, default_project_root
from .folders import ensure_project_folders, ensure_root

_MSG_EMPTY = "storage.onboarding.name_invalid"
register_strings({
    "tr": {_MSG_EMPTY: "Proje adı boş olamaz."},
    "en": {_MSG_EMPTY: "The project name can't be empty."},
})

MAX_PROJECT_NAME = 60
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class OnboardingResult:
    root: Path
    project_dir: Optional[Path]
    folders: Tuple[Path, ...]


def clean_project_name(name: str) -> str:
    """Proje klasörü adını dosya sistemi için güvenli hâle getirir. Boş kalırsa ``ValueError``."""
    cleaned = " ".join(_BAD_CHARS.sub(" ", str(name or "")).split())
    cleaned = cleaned[:MAX_PROJECT_NAME].strip(" .")
    if not cleaned:
        raise ValueError(t(_MSG_EMPTY))
    return cleaned


def run(settings: SettingsStore, answers: WizardAnswers, *,
        first_project: Optional[str] = None) -> OnboardingResult:
    """Sihirbaz cevaplarını uygular.

    1. Kök klasörü oluşturur (yoksa).
    2. ``first_project`` doluysa, kök altında ilk proje klasörünü iskeletiyle kurar.
    3. Ayarlara (``project_root``, ``wizard.*``) yazar. Mod her zaman Basit kalır.

    ``first_project`` boş ya da ``None`` ise proje klasörü oluşturulmaz.
    """
    ans = answers.normalized()
    root = ensure_root(default_project_root(ans.place, ans.root_path))
    project_dir: Optional[Path] = None
    folders: Tuple[Path, ...] = ()
    if first_project and str(first_project).strip():
        project_dir = root / clean_project_name(first_project)
        folders = tuple(ensure_project_folders(project_dir))
    apply_wizard_answers(settings, ans)
    return OnboardingResult(root=root, project_dir=project_dir, folders=folders)
