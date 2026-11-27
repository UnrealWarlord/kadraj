"""``AppContext`` — uygulamanın tek durum/servis merkezi (``contracts.ContextAPI``).

Widget'lar durum tutmaz (plan §0.5): seçim, açık proje, geri alma geçmişi, iş kuyruğu, kasa
burada yaşar. Basit ↔ Legend geçişi yalnızca ``set_ui_mode`` çağırır; bağlam ve içindeki her
şey aynen kalır.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoStack

from .. import device
from ..ai_engine.providers.base import default_registry
from ..storage.autosave import AutosaveService
from ..storage.project import ProjectSession
from ..storage.snapshots import SnapshotStore
from ..storage.vault import Vault
from . import i18n
from .contracts import (ErrorContext, ModuleSpec, Service, SmartError, Topics, UiMode)
from .errors import translate
from .events import EventBus
from .jobs import JobQueue
from .registry import ModuleRegistry
from .settings import SettingsStore
from .stubs import make_recent
from .sysmon import SystemMonitor


def _allowed_mode(mode: UiMode) -> UiMode:
    """Telefonda yalnız Basit Mod (M1); tablet ve masaüstünde istenen mod."""
    return UiMode.SIMPLE if mode is UiMode.LEGEND and not device.legend_allowed() else mode


class AppContext(QObject):
    modeChanged = Signal(str)
    languageChanged = Signal(str)

    def __init__(self, settings: Optional[SettingsStore] = None,
                 registry: Optional[ModuleRegistry] = None, *, discover: bool = True,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.settings: SettingsStore = settings or SettingsStore()
        lang = self.settings.get("language") or i18n.system_language()
        try:
            i18n.set_language(lang)
        except ValueError:
            i18n.set_language(i18n.DEFAULT_LANGUAGE)

        self.bus = EventBus(self)
        self.project = ProjectSession(self)
        self.undo = QUndoStack(self)
        self.vault: Any = Vault()                   # Fernet + OS keyring; ilk kullanıma dek diske/keyring'e dokunmaz
        self.sysmon: Any = SystemMonitor()
        self.recent = make_recent()
        self.theme: Any = None                      # bootstrap ThemeManager'ı bağlar
        self.current_module: Optional[str] = None   # kabuğun açık modülü (gezinti; mod geçişinde korunur)

        self.registry = registry or ModuleRegistry()
        if discover:
            self.registry.discover()

        # Adım 3 servisleri (proje/bus/ayarlar hazır olduktan sonra)
        self.jobs: Any = JobQueue(self)
        self.providers = default_registry(self.settings, self.jobs)
        self.providers.on_ping = lambda _pid, ms: self.sysmon.record_ping(ms)
        self.snapshots = SnapshotStore(self.project, self.bus, self.undo)
        self.autosave = AutosaveService(self)

        self._services: Dict[str, Optional[Service]] = {}
        self._mode = _allowed_mode(UiMode.coerce(self.settings.get("ui_mode")))
        i18n.set_ui_mode(self._mode.value)

        self.project.opened.connect(lambda p: self.bus.emit(Topics.PROJECT_OPENED, p))
        self.project.closed.connect(lambda: self.bus.emit(Topics.PROJECT_CLOSED, None))
        self.project.dirtyChanged.connect(lambda d: self.bus.emit(Topics.PROJECT_DIRTY, d))
        self.project.saved.connect(lambda p: self.bus.emit(Topics.PROJECT_SAVED, p))

    # -- mod --------------------------------------------------------------
    @property
    def ui_mode(self) -> UiMode:
        return self._mode

    def set_ui_mode(self, mode: "UiMode | str") -> None:
        """Yalnızca sayfaları/temayı değiştirir; proje, seçim, geri alma geçmişi aynen korunur.
        Telefonda Legend Mod yoktur (M1): istek Basit Mod'da kalır."""
        new = _allowed_mode(UiMode.coerce(mode))
        if new is self._mode:
            return
        self._mode = new
        i18n.set_ui_mode(new.value)
        if self.theme is not None:                  # önce tema (çapraz geçiş anlık görüntüsü eski görünümü yakalar)
            self.theme.apply(new)
        self.settings.set("ui_mode", new.value)
        self.project.set_ui_mode_last(new)
        self.bus.emit(Topics.MODE_CHANGED, new.value)
        self.modeChanged.emit(new.value)

    # -- dil --------------------------------------------------------------
    def set_language(self, code: str) -> None:
        i18n.set_language(code)
        self.settings.set("language", code)
        self.bus.emit(Topics.LANGUAGE_CHANGED, code)
        self.languageChanged.emit(code)

    # -- servisler --------------------------------------------------------
    def service(self, module_id: str) -> Optional[Service]:
        if module_id in self._services:
            return self._services[module_id]
        spec: Optional[ModuleSpec] = self.registry.get(module_id)
        svc: Optional[Service] = None
        if spec is not None and spec.service_factory is not None:
            try:
                svc = spec.service_factory(self)
                svc.start(self)
            except Exception as exc:
                self.report_error(exc, {"source": module_id})
                svc = None
        self._services[module_id] = svc
        return svc

    def shutdown(self) -> None:
        for svc in list(self._services.values()):
            if svc is not None:
                try:
                    svc.stop()
                except Exception:
                    pass
        self._services.clear()
        try:
            self.jobs.shutdown()                     # çalışan işler duraklatılır (DB hâlâ açıkken)
        except AttributeError:
            pass
        self.project.close()                         # autosave son kaydı burada alır (aboutToClose)
        self.autosave.stop()

    # -- hata -------------------------------------------------------------
    def report_error(self, exc: BaseException,
                     context: Optional[ErrorContext] = None) -> SmartError:
        err = translate(exc, context)
        self.bus.emit(Topics.ERROR, err)
        return err

    # -- proje kolaylıkları -------------------------------------------------
    def open_project(self, path: "str | Path") -> Optional[Path]:
        """Projeyi açar, son projelere ekler. Hata → ``SmartError`` yayınlanır, ``None`` döner."""
        try:
            root = self.project.open(Path(path))
        except Exception as exc:
            self.report_error(exc, {"source": "project", "path": str(path)})
            return None
        self.recent.add_recent(str(root), self.project.name)
        self.settings.set("last_project", str(root))
        return root

    def create_project(self, name: str, parent_dir: "str | Path | None" = None) -> Optional[Path]:
        parent = Path(parent_dir) if parent_dir else Path(self.settings.get("project_root") or "")
        if not str(parent):
            parent = Path.home() / "KADRAJ_Projeler"
        try:
            root = self.project.create(parent, name, ui_mode=self._mode)
        except Exception as exc:
            self.report_error(exc, {"source": "project", "path": str(parent)})
            return None
        self.recent.add_recent(str(root), self.project.name)
        self.settings.set("last_project", str(root))
        return root

    # -- sonraki adımların bağlayacağı gerçek uygulamalar --------------------
    def install_vault(self, vault: Any) -> None:
        self.vault = vault

    def install_jobs(self, jobs: Any) -> None:
        self.jobs = jobs

    def install_sysmon(self, sysmon: Any) -> None:
        self.sysmon = sysmon
