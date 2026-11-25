"""Disk alanı: erken uyarı ve iş öncesi "yer yeter mi?" kontrolü.

Eşikler: boş alan 10 GB altına inerse **uyarı** (iş sürer); 2 GB altına inerse
**kritik** (yeni kayıt ya da indirme riskli). Ölçüm, yol henüz yoksa en yakın var olan
üst klasörde yapılır. Ölçüm hata verirse ``Level.UNKNOWN`` döner ve hiçbir şey engellenmez.
"""
from __future__ import annotations

import enum
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from ..core.contracts import SmartAction, SmartError, Severity
from ..core.i18n import register_strings, t

GIB = 1024 ** 3
WARN_FREE_BYTES = 10 * GIB
CRITICAL_FREE_BYTES = 2 * GIB
PathLike = Union[str, Path]

_STRINGS = {
    "tr": {
        "storage.disk.low.title": "Disk dolmaya yaklaşıyor",
        "storage.disk.low.summary": "Seçili diskte yalnızca {free} boş yer kaldı.",
        "storage.disk.low.cause": "Büyük video ve ses dosyaları hızla yer kaplar.",
        "storage.disk.low.step1": "Gereksiz dosyaları sil ya da önbelleği temizle.",
        "storage.disk.critical.title": "Diskte çok az yer kaldı",
        "storage.disk.critical.summary": "Yalnızca {free} boş yer var; yeni kayıt ve indirme riskli.",
        "storage.disk.critical.cause": "Seçili diskin boş alanı işlemler için çok düşük.",
        "storage.disk.critical.step1": "Önbelleği temizle ya da projeyi başka bir diske taşı.",
        "storage.disk.insufficient.title": "İşlem için yer yetmiyor",
        "storage.disk.insufficient.summary": "Bu işlem yaklaşık {needed} yer istiyor; boş alan {free}.",
        "storage.disk.insufficient.cause": "Çıktı diskinin boş alanı işlem için yetersiz.",
        "storage.disk.insufficient.step1": "Yer aç ya da çıktı klasörünü daha boş bir diske seç.",
    },
    "en": {
        "storage.disk.low.title": "Disk is filling up",
        "storage.disk.low.summary": "Only {free} of free space is left on the selected disk.",
        "storage.disk.low.cause": "Large video and audio files take space quickly.",
        "storage.disk.low.step1": "Delete unneeded files or clear the cache.",
        "storage.disk.critical.title": "Very little space left on the disk",
        "storage.disk.critical.summary": "Only {free} is free; new recordings and downloads are risky.",
        "storage.disk.critical.cause": "The free space on the selected disk is too low for jobs.",
        "storage.disk.critical.step1": "Clear the cache or move the project to another disk.",
        "storage.disk.insufficient.title": "Not enough space for this job",
        "storage.disk.insufficient.summary": "This job needs about {needed}; free space is {free}.",
        "storage.disk.insufficient.cause": "The output disk doesn't have enough free space for the job.",
        "storage.disk.insufficient.step1": "Free up space or choose an output folder on a disk with more room.",
    },
}
register_strings(_STRINGS)


class Level(str, enum.Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DiskStatus:
    path: str
    total: int
    free: int
    level: Level

    @property
    def free_gb(self) -> float:
        return self.free / GIB


def level_for(free_bytes: int) -> Level:
    if free_bytes < CRITICAL_FREE_BYTES:
        return Level.CRITICAL
    if free_bytes < WARN_FREE_BYTES:
        return Level.WARNING
    return Level.OK


def human_size(n: int) -> str:
    """Bayt sayısını Türkçe ondalık virgüllü metne çevirir (örn. ``12,3 GB``)."""
    value = float(max(int(n), 0))
    units = ("B", "KB", "MB", "GB", "TB")
    i = 0
    while value >= 1024 and i < len(units) - 1:
        value /= 1024
        i += 1
    if i == 0:
        return f"{int(value)} B"
    return f"{value:.1f} {units[i]}".replace(".", ",")


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _measure_point(path: PathLike) -> Path:
    point = Path(path).expanduser()
    try:
        point = point.resolve()
    except (OSError, RuntimeError):
        pass
    while not _exists(point) and point.parent != point:
        point = point.parent
    return point


def status(path: PathLike) -> DiskStatus:
    """Yolun bulunduğu diskin durumu. Ölçülemezse ``Level.UNKNOWN`` (asla istisna atmaz)."""
    try:
        usage = shutil.disk_usage(str(_measure_point(path)))
    except OSError:
        return DiskStatus(str(path), 0, 0, Level.UNKNOWN)
    return DiskStatus(str(path), usage.total, usage.free, level_for(usage.free))


def has_room_for(path: PathLike, needed_bytes: int = 0, *,
                 reserve: int = CRITICAL_FREE_BYTES) -> bool:
    """İşlem ``needed_bytes`` yer istiyorsa ve sonrasında ``reserve`` kadar boş alan kalıyorsa True.

    Ölçülemiyorsa (UNKNOWN) engellemez ve True döner.
    """
    st = status(path)
    if st.level is Level.UNKNOWN:
        return True
    return st.free - max(int(needed_bytes), 0) >= reserve


def _card(prefix: str, code: str, *, severity: Severity,
          actions: tuple = (), **fmt: object) -> SmartError:
    return SmartError(code, t(f"{prefix}.title"), t(f"{prefix}.summary", **fmt),
                      cause=t(f"{prefix}.cause"), steps=(t(f"{prefix}.step1"),),
                      actions=actions, severity=severity, source="storage")


def warning_for(path: PathLike) -> Optional[SmartError]:
    """Erken uyarı kartı. Yer yeterliyse ya da ölçülemiyorsa ``None``."""
    st = status(path)
    if st.level in (Level.OK, Level.UNKNOWN):
        return None
    action = SmartAction("open_settings", t("err.action.open_settings"), "open_settings",
                         {"section": "storage"})
    if st.level is Level.CRITICAL:
        return _card("storage.disk.critical", "disk.critical", severity=Severity.ERROR,
                     actions=(action,), free=human_size(st.free))
    return _card("storage.disk.low", "disk.low", severity=Severity.WARNING,
                 actions=(action,), free=human_size(st.free))


def insufficient_error(path: PathLike, needed_bytes: int) -> SmartError:
    """İş öncesi kontrol başarısızsa döner: ``needed_bytes`` istenir, mevcut boş alan gösterilir."""
    st = status(path)
    return _card("storage.disk.insufficient", "disk.insufficient", severity=Severity.ERROR,
                 needed=human_size(needed_bytes), free=human_size(st.free))
