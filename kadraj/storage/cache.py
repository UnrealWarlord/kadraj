"""Proje önbelleği temizleyici.

Üç kategori, yalnızca yeniden üretilebilir veri:

* ``cache``   — ``.kadraj/cache`` içindeki her dosya;
* ``proxies`` — ``06_Proxies`` içindeki her dosya (düzenleme önizlemeleri);
* ``partial`` — yarım kalmış indirmeler (``*.part``, ``*.part-FragN``, ``*.ytdl``, ``*.temp``),
  proje alt klasörlerinde (``06_Proxies`` hariç).

Taranmaz ve silinmez: ``.kadraj/autosave``, ``.kadraj/snapshots``, ``.kadraj/proje.db``,
``proje.kadraj`` ve klasörlerin kendisi. Simge bağlantılar izlenmez; proje dışına çıkan
her dosya atlanır.
"""
from __future__ import annotations

import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Union

from ..core.contracts import HIDDEN_DIR, PROJECT_SUBFOLDERS

PathLike = Union[str, Path]
CATEGORY_CACHE = "cache"
CATEGORY_PROXIES = "proxies"
CATEGORY_PARTIAL = "partial"
CATEGORIES: tuple = (CATEGORY_CACHE, CATEGORY_PROXIES, CATEGORY_PARTIAL)
PROXY_FOLDER = "06_Proxies"
PARTIAL_RE = re.compile(r"\.(part(-Frag\d+)?|ytdl|temp)$", re.IGNORECASE)
_DAY = 86400.0


@dataclass(frozen=True)
class CacheFile:
    category: str
    path: Path
    size: int
    mtime: float


@dataclass(frozen=True)
class CategoryUsage:
    category: str
    files: int
    bytes: int


@dataclass(frozen=True)
class CleanReport:
    removed: int
    freed: int
    failed: int
    dry_run: bool


def _walk_files(root: Path) -> Iterator[Path]:
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            yield Path(dirpath) / name


def _regular_stat(path: Path) -> Optional[os.stat_result]:
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return st if stat.S_ISREG(st.st_mode) else None


def _inside(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def scan(project_dir: PathLike, categories: Sequence[str] = CATEGORIES) -> List[CacheFile]:
    """İstenen kategorilerdeki dosyaları listeler (salt okunur)."""
    project = Path(project_dir).expanduser()
    try:
        base = project.resolve()
    except (OSError, RuntimeError):
        return []
    wanted = set(categories)
    found: List[CacheFile] = []

    def add(category: str, path: Path) -> None:
        st = _regular_stat(path)
        if st is None or not _inside(path, base):
            return
        found.append(CacheFile(category, path, st.st_size, st.st_mtime))

    if CATEGORY_CACHE in wanted:
        for f in _walk_files(project / HIDDEN_DIR / "cache"):
            add(CATEGORY_CACHE, f)
    if CATEGORY_PROXIES in wanted:
        for f in _walk_files(project / PROXY_FOLDER):
            add(CATEGORY_PROXIES, f)
    if CATEGORY_PARTIAL in wanted:
        for name in PROJECT_SUBFOLDERS:
            if name == PROXY_FOLDER:
                continue
            for f in _walk_files(project / name):
                if PARTIAL_RE.search(f.name):
                    add(CATEGORY_PARTIAL, f)
    return found


def usage(project_dir: PathLike) -> Dict[str, CategoryUsage]:
    """Her kategori için dosya sayısı ve toplam bayt (yoksa sıfır)."""
    totals: Dict[str, List[int]] = {c: [0, 0] for c in CATEGORIES}
    for f in scan(project_dir):
        totals[f.category][0] += 1
        totals[f.category][1] += f.size
    return {c: CategoryUsage(c, n, b) for c, (n, b) in totals.items()}


def clean(project_dir: PathLike, categories: Sequence[str] = CATEGORIES, *,
          older_than_days: Optional[float] = None, now: Optional[float] = None,
          dry_run: bool = False) -> CleanReport:
    """Seçili kategorilerdeki dosyaları siler.

    ``older_than_days`` verilirse yalnızca bu günden eski dosyalar silinir.
    ``dry_run`` True ise hiçbir dosya silinmez; rapor "silinebilecek" sayıyı verir.
    """
    cutoff: Optional[float] = None
    if older_than_days is not None:
        cutoff = (time.time() if now is None else now) - float(older_than_days) * _DAY
    removed = freed = failed = 0
    for f in scan(project_dir, categories):
        if cutoff is not None and f.mtime > cutoff:
            continue
        if not dry_run:
            try:
                os.remove(f.path)
            except OSError:
                failed += 1
                continue
        removed += 1
        freed += f.size
    return CleanReport(removed, freed, failed, dry_run)
