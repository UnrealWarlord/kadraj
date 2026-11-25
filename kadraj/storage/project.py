"""``.kadraj`` proje dosyası ve açık proje oturumu.

İskeletin ``project_file.py`` mantığı (``format`` + ``version`` alanlı JSON, bozuk /
yabancı dosyada sade hata, eski sürümü yükleme) KADRAJ için yeniden yazıldı:

* ``<Proje>/proje.kadraj``  — JSON, plan §3. Bilinmeyen alanlar **korunur** (ileri uyumluluk).
* ``<Proje>/.kadraj/proje.db`` — SQLite (``storage/db.py``).
* Klasör iskeleti: ``01_Downloads`` … ``07_Exports`` + gizli ``.kadraj/{autosave,snapshots,cache}``.

``ProjectSession`` uygulamanın **tek** açık proje durumudur (``ctx.project``); widget'lar durum
tutmaz. Zaman çizelgesi (``timeline``) ve dizi (``series``) bölümleri bu adımda ham sözlüktür;
biçimlerini Adım 7 / 8 tanımlar.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from ..core.contracts import (DB_RELATIVE, HIDDEN_DIR, HIDDEN_SUBDIRS, PROJECT_FILE_NAME,
                              PROJECT_FORMAT, PROJECT_FORMAT_VERSION, PROJECT_SUBFOLDERS,
                              Severity, SmartError, UiMode)
from .db import ProjectDB

_KNOWN_KEYS = {"format", "version", "name", "created", "ui_mode_last", "settings", "timeline",
               "markers", "series"}


def default_settings() -> Dict[str, Any]:
    return {"fps": 30, "resolution": "1920x1080", "sample_rate": 48000}


@dataclass
class ProjectData:
    """Proje dosyasının bellekteki karşılığı."""
    name: str
    created: float = field(default_factory=time.time)
    ui_mode_last: str = UiMode.SIMPLE.value
    settings: Dict[str, Any] = field(default_factory=default_settings)
    timeline: Dict[str, Any] = field(default_factory=lambda: {"tracks": []})
    markers: List[Dict[str, Any]] = field(default_factory=list)
    series: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)        # tanınmayan üst düzey alanlar

    def digest(self) -> str:
        """İçeriğin kararlı özeti (autosave "değişiklik var mı?" kararı için)."""
        blob = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = copy.deepcopy(self.extra)
        out.update({
            "format": PROJECT_FORMAT,
            "version": PROJECT_FORMAT_VERSION,
            "name": self.name,
            "created": self.created,
            "ui_mode_last": self.ui_mode_last,
            "settings": copy.deepcopy(self.settings),
            "timeline": copy.deepcopy(self.timeline),
            "markers": copy.deepcopy(self.markers),
            "series": copy.deepcopy(self.series),
        })
        return out

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ProjectData":
        settings = default_settings()
        if isinstance(raw.get("settings"), dict):
            settings.update(raw["settings"])
        timeline = raw.get("timeline") if isinstance(raw.get("timeline"), dict) else {}
        timeline.setdefault("tracks", [])
        markers = raw.get("markers") if isinstance(raw.get("markers"), list) else []
        series = raw.get("series") if isinstance(raw.get("series"), dict) else {}
        mode = UiMode.coerce(raw.get("ui_mode_last")).value
        created = raw.get("created")
        return cls(
            name=str(raw.get("name") or "Adsız Proje"),
            created=float(created) if isinstance(created, (int, float)) else time.time(),
            ui_mode_last=mode, settings=settings, timeline=timeline, markers=markers, series=series,
            extra={k: v for k, v in raw.items() if k not in _KNOWN_KEYS})


# ---------------------------------------------------------------------------
# Dosya G/Ç
# ---------------------------------------------------------------------------
def _err(code: str, title: str, summary: str, path: Path, exc: Optional[BaseException] = None,
         steps: tuple = ()) -> SmartError:
    return SmartError(code, title, summary, steps=steps, severity=Severity.ERROR,
                      technical=f"{path}\n{type(exc).__name__}: {exc}" if exc else str(path),
                      source="project")


def migrate(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Eski sürüm sözlüğünü güncel şemaya taşır. Şimdilik yalnızca sürüm 1 vardır;
    ``version`` 1'den küçükse (elle düzenlenmiş) 1'e çekilir."""
    version = raw.get("version")
    if not isinstance(version, int) or version < 1:
        raw["version"] = 1
    return raw


def read_project_file(path: Path) -> ProjectData:
    """``proje.kadraj`` okur. Sorunlar ``SmartError`` olarak atılır (sade Türkçe)."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise _err("project.missing", "Proje dosyası bulunamadı",
                   f"'{path.name}' dosyası artık orada değil.", path, exc,
                   ("Klasörün taşınıp taşınmadığına bakın.",)) from exc
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise _err("project.unreadable", "Proje dosyası okunamadı",
                   f"'{path.name}' bozuk ya da okunamıyor.", path, exc,
                   ("Otomatik kayıtlardan (.kadraj/autosave) dönmeyi deneyin.",)) from exc
    if not isinstance(raw, dict) or raw.get("format") != PROJECT_FORMAT:
        raise _err("project.foreign", "Bu bir proje dosyası değil",
                   f"'{path.name}' tanınan bir proje dosyası değil.", path)
    version = raw.get("version")
    if isinstance(version, int) and version > PROJECT_FORMAT_VERSION:
        raise _err("project.too_new", "Proje daha yeni bir sürümle kaydedilmiş",
                   f"'{path.name}' sürüm {version} biçiminde; bu uygulama en çok "
                   f"{PROJECT_FORMAT_VERSION} sürümünü açar.", path,
                   steps=("Uygulamayı güncelleyin.",))
    return ProjectData.from_dict(migrate(raw))


def write_project_file(path: Path, data: ProjectData) -> None:
    """Atomik yazma: geçici dosya + ``os.replace`` (yarım yazılmış proje dosyası olmaz)."""
    path = Path(path)
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".proje-", suffix=".tmp", dir=str(path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data.to_dict(), fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_folder_name(name: str) -> str:
    cleaned = _BAD_CHARS.sub(" ", name).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:80] or "Yeni Proje"


def unique_dir(parent: Path, name: str) -> Path:
    base = parent / safe_folder_name(name)
    candidate, n = base, 2
    while candidate.exists():
        candidate = base.with_name(f"{base.name} ({n})")
        n += 1
    return candidate


def ensure_folders(project_dir: Path) -> List[Path]:
    """Klasör iskeletini oluşturur. Haiku'nun ``storage.folders.ensure_project_folders``'ı
    varsa (Adım 2) onu kullanır; yoksa yerleşik asgari uygulamaya düşer."""
    try:
        from . import folders  # type: ignore[attr-defined]
        fn = getattr(folders, "ensure_project_folders", None)
        if callable(fn):
            return [Path(p) for p in fn(project_dir)]
    except ImportError:
        pass
    created: List[Path] = []
    for name in PROJECT_SUBFOLDERS:
        (project_dir / name).mkdir(parents=True, exist_ok=True)
        created.append(project_dir / name)
    for sub in HIDDEN_SUBDIRS:
        (project_dir / HIDDEN_DIR / sub).mkdir(parents=True, exist_ok=True)
    return created


# ---------------------------------------------------------------------------
# Oturum
# ---------------------------------------------------------------------------
class ProjectSession(QObject):
    """Açık proje (``ProjectAPI``). Proje yokken ``is_open`` False'tur."""

    opened = Signal(str)          # proje klasörü
    aboutToClose = Signal()       # kapanmadan önce (DB hâlâ açık): autosave son kaydı, iş kuyruğu duraklatmayı yapar
    closed = Signal()
    dataReplaced = Signal(str)    # sebep: "rewind" | "snapshot" | "undo_rewind" — arayüz/servisler yeniden okur
    dirtyChanged = Signal(bool)
    saved = Signal(str)           # proje dosyası yolu

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._root: Optional[Path] = None
        self._data: Optional[ProjectData] = None
        self._db: Optional[ProjectDB] = None
        self._dirty = False
        self._meta_dirty = False        # yalnızca ui_mode_last değişti: kullanıcıyı "kaydet?" diye rahatsız etmez

    # -- durum ------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._root is not None

    @property
    def root(self) -> Optional[Path]:
        return self._root

    @property
    def data(self) -> Optional[ProjectData]:
        return self._data

    @property
    def name(self) -> str:
        return self._data.name if self._data else ""

    @property
    def file_path(self) -> Optional[Path]:
        return self._root / PROJECT_FILE_NAME if self._root else None

    @property
    def db(self) -> ProjectDB:
        if self._db is None:
            raise RuntimeError("Açık proje yok")
        return self._db

    @property
    def dirty(self) -> bool:
        return self._dirty

    def subfolder(self, name: str) -> Path:
        if self._root is None:
            raise RuntimeError("Açık proje yok")
        if name not in PROJECT_SUBFOLDERS:
            raise ValueError(f"Bilinmeyen proje klasörü: {name!r}")
        path = self._root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- yaşam döngüsü ----------------------------------------------------
    def create(self, parent_dir: Path, name: str, *, ui_mode: "UiMode | str" = UiMode.SIMPLE,
               settings: Optional[Dict[str, Any]] = None) -> Path:
        """Yeni proje klasörü + dosya + veritabanı oluşturur ve açar."""
        parent_dir = Path(parent_dir)
        parent_dir.mkdir(parents=True, exist_ok=True)
        root = unique_dir(parent_dir, name)
        root.mkdir(parents=True)
        ensure_folders(root)
        data = ProjectData(name=name.strip() or "Yeni Proje", ui_mode_last=UiMode.coerce(ui_mode).value)
        if settings:
            data.settings.update(settings)
        write_project_file(root / PROJECT_FILE_NAME, data)
        self._attach(root, data)
        return root

    def open(self, path: Path) -> Path:
        """Proje klasörünü ya da doğrudan ``proje.kadraj`` dosyasını açar."""
        path = Path(path)
        root = path.parent if path.is_file() else path
        data = read_project_file(root / PROJECT_FILE_NAME)
        ensure_folders(root)
        self._attach(root, data)
        return root

    def _attach(self, root: Path, data: ProjectData) -> None:
        self.close()
        self._root, self._data = root, data
        self._db = ProjectDB(root / DB_RELATIVE)
        self._set_dirty(False)
        self.opened.emit(str(root))

    def close(self) -> None:
        if self._root is None:
            return
        self.aboutToClose.emit()
        if self._meta_dirty and not self._dirty and self._data is not None:
            try:                         # sessiz meta kaydı (son kullanılan mod)
                write_project_file(self._root / PROJECT_FILE_NAME, self._data)
            except OSError:
                pass
        self._meta_dirty = False
        if self._db is not None:
            self._db.close()
        self._root = self._data = self._db = None
        self._set_dirty(False)
        self.closed.emit()

    # -- kayıt ------------------------------------------------------------
    def mark_dirty(self) -> None:
        if self.is_open:
            self._set_dirty(True)

    def _set_dirty(self, value: bool) -> None:
        if self._dirty != value:
            self._dirty = value
            self.dirtyChanged.emit(value)

    def save(self, *, silent: bool = False) -> bool:
        """Proje dosyasını atomik yazar. Başarılıysa ``saved`` yayınlanır (``silent=True`` → yayınlanmaz;
        otomatik kayıt kullanıcıyı "kaydedildi" iletisiyle rahatsız etmesin)."""
        if self._root is None or self._data is None:
            return False
        write_project_file(self._root / PROJECT_FILE_NAME, self._data)
        self._meta_dirty = False
        self._set_dirty(False)
        if not silent:
            self.saved.emit(str(self._root / PROJECT_FILE_NAME))
        return True

    def replace_data(self, data: "ProjectData", reason: str = "restore") -> None:
        """Bellekteki proje verisini değiştirir (geri dönüş/snapshot geri yükleme). Kirli işaretlenir ve
        ``dataReplaced`` yayınlanır; kalıcılaştırmak çağıranın işidir (``save``)."""
        if self._root is None:
            raise RuntimeError("Açık proje yok")
        self._data = data
        self._set_dirty(True)
        self.dataReplaced.emit(reason)

    def set_ui_mode_last(self, mode: "UiMode | str") -> None:
        """Son kullanılan modu projeye yazar (sessiz; yalnızca proje açıkken ve değişince kirletir)."""
        if self._data is None:
            return
        value = UiMode.coerce(mode).value
        if self._data.ui_mode_last != value:
            self._data.ui_mode_last = value
            self._meta_dirty = True
