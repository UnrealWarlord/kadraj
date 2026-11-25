"""Proje klasör iskeleti (plan §1, sözleşme: ``ensure_project_folders``).

* İdempotenttir: var olan klasör ve dosyalara dokunmaz, hiçbir şey silmez.
* Yalnız ``mkdir`` kullanır. Bir yol dosya olarak duruyorsa ``OSError`` yükselir;
  çağıran taraf ``core.errors.translate`` ile Basit Türkçe karta çevirir.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Union

from ..core.contracts import HIDDEN_DIR, HIDDEN_SUBDIRS, PROJECT_SUBFOLDERS

PathLike = Union[str, Path]

#: İçerik türü → proje alt klasörü. Anahtarlar kodun kullandığı kararlı kimliklerdir.
FOLDER_BY_KIND: Dict[str, str] = {
    "download": "01_Downloads",
    "wav": "02_Audio_WAV",
    "ai_image": "03_AI_Images",
    "ai_scene": "04_AI_Scenes",
    "script": "05_Scripts",
    "proxy": "06_Proxies",
    "export": "07_Exports",
}


def folder_for(kind: str) -> str:
    """İçerik türünün proje alt klasörü adını döner (bilinmeyen tür → ``ValueError``)."""
    try:
        return FOLDER_BY_KIND[kind]
    except KeyError:
        raise ValueError(f"Bilinmeyen klasör türü: {kind!r}") from None


def ensure_root(root: PathLike) -> Path:
    """Proje kök klasörünü (yoksa üst klasörleriyle birlikte) oluşturur ve döner."""
    path = Path(root).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_project_folders(project_dir: PathLike) -> List[Path]:
    """Proje klasörünün 7 görünür alt klasörünü ve gizli ``.kadraj`` alt klasörlerini oluşturur.

    Dönen liste yalnızca görünür alt klasörleri içerir (``PROJECT_SUBFOLDERS`` sırasıyla).
    """
    project = Path(project_dir).expanduser()
    project.mkdir(parents=True, exist_ok=True)
    created: List[Path] = []
    for name in PROJECT_SUBFOLDERS:
        folder = project / name
        folder.mkdir(exist_ok=True)
        created.append(folder)
    hidden = project / HIDDEN_DIR
    for sub in HIDDEN_SUBDIRS:
        (hidden / sub).mkdir(parents=True, exist_ok=True)
    return created
