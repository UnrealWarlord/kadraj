"""Ayarlar — Legend Mode sayfası: sol menü ve bölümler (arayüz, API kasası, yerel çalışma zamanı,
fallback sırası, yollar, güncellemeler, profiller).

Sayfa yalnızca görünümdür. Tam anahtar hiçbir tabloda görünmez; yalnızca maskeli biçim (``masked``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFileDialog,
                               QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QProgressBar, QRadioButton,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...core import i18n
from ...core.contracts import SmartError
from ...storage.disk import human_size
from ...storage.profiles import ProfileError
from ...ui.theme.manager import ThemedMixin, current_tokens
from ...ui.widgets import SquishyButton
from ...ui.widgets.surface import page_surface_qss
from .guide import GUIDE_HOSTS, GuideView
from .keytest import KeyTestResult
from .service import WHISPER_MODELS, LocalRuntime, source_label
from .strings import K

_KINDS: Tuple[Tuple[str, str], ...] = (
    ("llm", K.KIND_LLM), ("image", K.KIND_IMAGE), ("video", K.KIND_VIDEO),
    ("tts", K.KIND_TTS), ("stt", K.KIND_STT), ("stock", K.KIND_STOCK),
)
_LED = {"ok": K.LED_OK, "fail": K.LED_FAIL, "idle": K.LED_IDLE, "checking": K.LED_CHECKING}


def _page() -> Tuple[QWidget, QVBoxLayout]:
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(10)
    return page, lay


def _caps(key: str) -> QLabel:
    label = QLabel(i18n.t(key).upper())
    label.setProperty("role", "caps")
    return label


def _mono() -> QFont:
    font = QFont()
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def _actions(*buttons: QWidget) -> QWidget:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(2, 0, 2, 0)
    for b in buttons:
        lay.addWidget(b)
    return box


def _table(headers: List[str], rows: int = 0) -> QTableWidget:
    table = QTableWidget(rows, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    return table


class SettingsLegendPage(ThemedMixin, QWidget):
    def __init__(self, ctx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("settings_legend")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)   # kök zemini QSS ile boyanır
        self.ctx = ctx
        service = ctx.service("settings")
        if service is None:
            raise RuntimeError("Ayarlar servisi kullanılamıyor")
        self.service = service
        self._dialogs: List[QWidget] = []
        self._build()
        service.changed.connect(self._on_changed)
        self._refresh_all()
        service.ensure_local()
        service.ensure_tools()
        self._bind_theme()
        self._on_theme()

    def _on_theme(self) -> None:
        """Basit ya da Legend tokenlarından zemin ve tablo/liste yüzeyleri (tema değişince yenilenir)."""
        self.setStyleSheet(page_surface_qss(self.tk, "settings_legend"))

    # -- yapı --------------------------------------------------------------
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setFixedWidth(220)
        self.stack = QStackedWidget()
        builders = (self._section_view, self._section_api, self._section_local, self._section_fallback,
                    self._section_paths, self._section_updates, self._section_profiles)
        keys = (K.SECTION_VIEW, K.SECTION_API, K.SECTION_LOCAL, K.SECTION_FALLBACK,
                K.SECTION_PATHS, K.SECTION_UPDATES, K.SECTION_PROFILES)
        self._builders = builders
        self._built: Set[int] = set()
        for key in keys:
            self.nav.addItem(i18n.t(key).upper())
            self.stack.addWidget(QWidget())         # bölüm ilk seçildiğinde kurulur (açılış maliyeti düşük)
        self.nav.currentRowChanged.connect(self._show_section)
        self.nav.setCurrentRow(0)
        root.addWidget(self.nav)
        right = QVBoxLayout()
        right.addWidget(self.stack, 1)
        self.status = QLabel("")
        self.status.setProperty("role", "hint")
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        root.addLayout(right, 1)

    def _section_view(self) -> QWidget:
        page, lay = _page()
        lay.addWidget(_caps(K.SECTION_VIEW))
        group = QButtonGroup(self)
        self.mode_radios: Dict[str, QRadioButton] = {}
        for mode, key in (("simple", K.MODE_SIMPLE), ("legend", K.MODE_LEGEND)):
            radio = QRadioButton(i18n.t(key))
            radio.toggled.connect(lambda checked, m=mode: checked and self.service.set_ui_mode(m))
            group.addButton(radio)
            self.mode_radios[mode] = radio
            lay.addWidget(radio)
        lay.addWidget(_caps(K.VIEW_TITLE))
        self.cb_large = QCheckBox(i18n.t(K.VIEW_LARGE))
        self.cb_contrast = QCheckBox(i18n.t(K.VIEW_CONTRAST))
        self.cb_motion = QCheckBox(i18n.t(K.VIEW_MOTION))
        self.cb_tips = QCheckBox(i18n.t(K.VIEW_TIPS))
        self.cb_large.toggled.connect(lambda v: self.service.set_a11y("large_text", v))
        self.cb_contrast.toggled.connect(lambda v: self.service.set_a11y("high_contrast", v))
        self.cb_motion.toggled.connect(lambda v: self.service.set_a11y("reduced_motion", v))
        self.cb_tips.toggled.connect(self.service.set_tips)
        for box in (self.cb_large, self.cb_contrast, self.cb_motion, self.cb_tips):
            lay.addWidget(box)
        lay.addStretch(1)
        return page

    def _section_api(self) -> QWidget:
        page, lay = _page()
        lay.addWidget(_caps(K.SECTION_API))
        self.api_state = QLabel("")
        self.api_state.setProperty("role", "hint")
        lay.addWidget(self.api_state)
        self.api_table = _table([i18n.t(K.TH_PROVIDER), i18n.t(K.TH_KEY), i18n.t(K.TH_MODEL),
                                 i18n.t(K.TH_STATE), i18n.t(K.TH_LATENCY), ""])
        lay.addWidget(self.api_table, 1)
        self.key_result = KeyTestResult(self.service, self.ctx)             # "Bağlantıyı test et" sonucu: sade Türkçe + adımlar
        lay.addWidget(self.key_result)
        footer = QHBoxLayout()
        footer.addStretch(1)
        test_all = SquishyButton(i18n.t(K.BTN_TEST_ALL), variant="secondary")
        test_all.clicked.connect(lambda: self.service.test_all(background=True))
        footer.addWidget(test_all)
        lay.addLayout(footer)
        self.drawer = QFrame()
        self.drawer.setProperty("role", "panel")
        self.drawer.setVisible(False)
        drawer_lay = QVBoxLayout(self.drawer)
        self.guide = GuideView(self._guide_providers(), self.drawer)
        drawer_lay.addWidget(self.guide)
        lay.addWidget(self.drawer)
        return page

    def _section_local(self) -> QWidget:
        page, lay = _page()
        lay.addWidget(_caps(K.SECTION_LOCAL))
        refresh = SquishyButton(i18n.t(K.BTN_REFRESH), variant="secondary")
        refresh.clicked.connect(lambda: self.service.refresh_local(background=True))
        lay.addWidget(refresh, 0)
        self.local_table = _table([i18n.t(K.TH_PROVIDER), i18n.t(K.TH_ENDPOINT), i18n.t(K.TH_MODELS),
                                   i18n.t(K.TH_STATE), ""])
        lay.addWidget(self.local_table, 1)
        return page

    def _section_fallback(self) -> QWidget:
        page, lay = _page()
        lay.addWidget(_caps(K.SECTION_FALLBACK))
        note = QLabel(i18n.t(K.FALLBACK_NOTE))
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.kind_combo = QComboBox()
        for kind, key in _KINDS:
            self.kind_combo.addItem(i18n.t(key), kind)
        self.kind_combo.currentIndexChanged.connect(lambda _i: self._load_chain())
        lay.addWidget(self.kind_combo)
        self.chain_list = QListWidget()
        lay.addWidget(self.chain_list, 1)
        row = QHBoxLayout()
        up = SquishyButton(i18n.t(K.BTN_UP), variant="ghost")
        down = SquishyButton(i18n.t(K.BTN_DOWN), variant="ghost")
        save = SquishyButton(i18n.t(K.BTN_SAVE), variant="primary")
        up.clicked.connect(lambda: self._move_chain(-1))
        down.clicked.connect(lambda: self._move_chain(1))
        save.clicked.connect(self._save_chain)
        for b in (up, down):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(save)
        lay.addLayout(row)
        return page

    def _section_paths(self) -> QWidget:
        page, lay = _page()
        lay.addWidget(_caps(K.SECTION_PATHS))
        form = QFormLayout()
        self.path_labels: Dict[str, QLabel] = {}
        for key, label_key in (("project", K.PATH_PROJECT), ("cache", K.PATH_CACHE),
                               ("render", K.PATH_RENDER), ("temp", K.PATH_TEMP),
                               ("comfyui", K.PATH_COMFYUI)):
            value = QLabel("")
            value.setFont(_mono())
            value.setWordWrap(True)
            self.path_labels[key] = value
            form.addRow(i18n.t(label_key), value)
        lay.addLayout(form)
        row = QHBoxLayout()
        pick_project = SquishyButton(i18n.t(K.BTN_PICK_FOLDER), variant="secondary")
        pick_project.clicked.connect(self._pick_project)
        pick_comfy = SquishyButton(i18n.t(K.BTN_PICK_FOLDER), variant="ghost")
        pick_comfy.clicked.connect(self._pick_comfy)
        row.addWidget(pick_project)
        row.addWidget(pick_comfy)
        row.addStretch(1)
        lay.addLayout(row)
        self.disk_bar = QProgressBar()
        self.disk_bar.setRange(0, 100)
        self.disk_bar.setTextVisible(False)
        lay.addWidget(self.disk_bar)
        self.disk_text = QLabel("")
        self.disk_text.setProperty("role", "hint")
        lay.addWidget(self.disk_text)
        self.clean_btn = SquishyButton("", variant="secondary")
        self.clean_btn.clicked.connect(self._clean)
        lay.addWidget(self.clean_btn, 0)
        lay.addStretch(1)
        return page

    def _section_updates(self) -> QWidget:
        page, lay = _page()
        lay.addWidget(_caps(K.SECTION_UPDATES))
        self.tools_table = _table([i18n.t(K.TH_PROVIDER), i18n.t(K.TH_VERSION), i18n.t(K.TH_STATE),
                                   i18n.t(K.TH_ACTION)], 3)
        lay.addWidget(self.tools_table)
        self.cb_auto_update = QCheckBox(i18n.t(K.AUTO_UPDATE))
        self.cb_auto_update.setChecked(self.service.auto_update())
        self.cb_auto_update.toggled.connect(self.service.set_auto_update)
        lay.addWidget(self.cb_auto_update)
        refresh = SquishyButton(i18n.t(K.BTN_REFRESH), variant="secondary")
        refresh.clicked.connect(lambda: self.service.refresh_tools(background=True))
        lay.addWidget(refresh, 0)
        note = QLabel(i18n.t(K.UPDATE_STARTUP_NOTE))
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        hint = QLabel(i18n.t(K.REPAIR_HINT))
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch(1)
        return page

    def _section_profiles(self) -> QWidget:
        page, lay = _page()
        lay.addWidget(_caps(K.SECTION_PROFILES))
        self.profile_table = _table([i18n.t(K.TH_NAME), i18n.t(K.TH_MODE), i18n.t(K.TH_NAMESPACE),
                                     i18n.t(K.PROFILE_ACTIVE)])
        self.profile_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.profile_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lay.addWidget(self.profile_table, 1)
        row = QHBoxLayout()
        new = SquishyButton(i18n.t(K.BTN_NEW_PROFILE), variant="secondary")
        switch = SquishyButton(i18n.t(K.BTN_SWITCH), variant="primary")
        delete = SquishyButton(i18n.t(K.BTN_DELETE_KEY), variant="ghost")
        new.clicked.connect(self._new_profile)
        switch.clicked.connect(self._switch_profile)
        delete.clicked.connect(self._delete_profile)
        for b in (new, switch, delete):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        return page

    # -- yenileme ----------------------------------------------------------
    def _guide_providers(self) -> Dict[str, str]:
        names = {p.id: p.display_name for p in self.service.key_providers()}
        return {pid: names[pid] for pid, _host in GUIDE_HOSTS if pid in names}

    _TOPIC_SECTION = {"view": 0, "keys": 1, "local": 2, "fallback": 3, "folder": 4, "tools": 5, "profiles": 6}

    def _on_changed(self, topic: str) -> None:
        index = self._TOPIC_SECTION.get(topic)
        if index is not None:
            self._refresh(index)

    def _refresh(self, index: int) -> None:
        """Yalnızca kurulmuş bölümü yeniler; kurulmamış bölümün widget'ları yoktur."""
        if index not in self._built:
            return
        refreshers = (self._refresh_view, self._refresh_api, self._refresh_local, self._load_chain,
                      self._refresh_paths, self._refresh_updates, self._refresh_profiles)
        refreshers[index]()

    def _refresh_all(self) -> None:
        for index in range(len(self._builders)):
            self._refresh(index)

    def _show_section(self, index: int) -> None:
        if index < 0:
            return
        if index not in self._built:
            placeholder = self.stack.widget(index)
            real = self._builders[index]()
            self.stack.insertWidget(index, real)
            self.stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self._built.add(index)
            self._refresh(index)
        self.stack.setCurrentIndex(index)

    def _refresh_view(self) -> None:
        current = self.service.ui_mode()
        for mode, radio in self.mode_radios.items():
            radio.blockSignals(True)
            radio.setChecked(mode == current)
            radio.blockSignals(False)
        for box, value in ((self.cb_large, self.service.a11y("large_text")),
                           (self.cb_contrast, self.service.a11y("high_contrast")),
                           (self.cb_motion, self.service.a11y("reduced_motion")),
                           (self.cb_tips, self.service.tips_enabled())):
            box.blockSignals(True)
            box.setChecked(bool(value))
            box.blockSignals(False)

    def _refresh_api(self) -> None:
        state, message = self.service.vault_state()
        self.api_state.setText("" if state == "ready" else message)
        tk = current_tokens()
        rows = self.service.key_rows()
        self.api_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self.api_table.setItem(r, 0, QTableWidgetItem(row.name))
            key_item = QTableWidgetItem(row.masked or i18n.t(K.NO_KEY))
            key_item.setFont(_mono())
            self.api_table.setItem(r, 1, key_item)
            self.api_table.setItem(r, 2, QTableWidgetItem(row.model or "—"))
            led = QTableWidgetItem("●  " + i18n.t(_LED.get(row.state, K.LED_IDLE)))
            led.setForeground(QBrush(QColor(_led_color(tk, row.state))))
            self.api_table.setItem(r, 3, led)
            latency = f"{int(row.latency_ms)} ms" if row.latency_ms else "—"
            self.api_table.setItem(r, 4, QTableWidgetItem(latency))
            test = SquishyButton(i18n.t(K.BTN_TEST), variant="secondary")
            test.clicked.connect(lambda _c=False, pid=row.provider_id: self.service.test_connection(pid))
            key = SquishyButton(i18n.t(K.BTN_CHANGE_KEY if row.has_key else K.BTN_ADD_KEY), variant="ghost")
            key.clicked.connect(lambda _c=False, pid=row.provider_id, nm=row.name: self._add_key(pid, nm))
            guide = SquishyButton(i18n.t(K.BTN_GUIDE), variant="ghost")
            guide.clicked.connect(lambda _c=False, pid=row.provider_id: self._open_drawer(pid))
            self.api_table.setCellWidget(r, 5, _actions(test, key, guide))
        self.key_result.refresh()

    def _refresh_local(self) -> None:
        runtimes = self.service.local_runtimes()
        self.local_table.setRowCount(len(runtimes))
        for r, rt in enumerate(runtimes):
            self.local_table.setItem(r, 0, QTableWidgetItem(rt.name))
            endpoint = QTableWidgetItem(rt.endpoint or "—")
            endpoint.setFont(_mono())
            self.local_table.setItem(r, 1, endpoint)
            models = ", ".join(rt.models) if rt.models else (rt.detail or "—")
            self.local_table.setItem(r, 2, QTableWidgetItem(models))
            self.local_table.setItem(r, 3, QTableWidgetItem(_local_state_text(rt)))
            buttons = []
            if rt.provider_id in ("ollama", "comfyui"):
                start = SquishyButton(i18n.t(K.BTN_START), variant="secondary")
                start.clicked.connect(lambda _c=False, pid=rt.provider_id: self._start_local(pid))
                buttons.append(start)
            if rt.provider_id in ("ollama", "whisper_local"):
                pick = SquishyButton(i18n.t(K.BTN_PICK_MODEL), variant="ghost")
                pick.clicked.connect(lambda _c=False, r_=rt: self._pick_model(r_))
                buttons.append(pick)
            self.local_table.setCellWidget(r, 4, _actions(*buttons))

    def _refresh_chain_kinds(self) -> None:
        self._load_chain()

    def _load_chain(self) -> None:
        kind = str(self.kind_combo.currentData() or "llm")
        names = {p.id: p.display_name for p in self.ctx.providers.list()}
        local = {p.id: bool(p.local) for p in self.ctx.providers.list()}
        self.chain_list.clear()
        for pid in self.service.chain_ids(kind):
            label = names.get(pid, pid) + ("  (yerel)" if local.get(pid) else "")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, pid)
            self.chain_list.addItem(item)

    def _move_chain(self, delta: int) -> None:
        row = self.chain_list.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < self.chain_list.count()):
            return
        item = self.chain_list.takeItem(row)
        self.chain_list.insertItem(target, item)
        self.chain_list.setCurrentRow(target)

    def _save_chain(self) -> None:
        kind = str(self.kind_combo.currentData() or "llm")
        ids = [self.chain_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.chain_list.count())]
        self.service.set_chain(kind, [str(i) for i in ids])
        self._say(i18n.t(K.FALLBACK_SAVED))

    def _refresh_paths(self) -> None:
        info = self.service.paths_info()
        for key, label in self.path_labels.items():
            label.setText(info.get(key, "—"))
        folder = self.service.folder_info()
        if folder is None:
            self.disk_bar.setValue(0)
            self.disk_text.setText("—")
        else:
            self.disk_bar.setValue(int(folder.used_fraction * 100))
            self.disk_text.setText(f"{human_size(folder.free)} / {human_size(folder.total)}")
        self.clean_btn.setText(i18n.t(K.CLEAN_BTN, size=human_size(self.service.cache_bytes())))

    def _refresh_updates(self) -> None:
        info = self.service.tool_info()
        src = info.sources
        rows = (
            (i18n.t(K.TOOL_YTDLP), info.ytdlp, source_label(src.get("yt-dlp", "")), "update"),
            (i18n.t(K.TOOL_FFMPEG), info.ffmpeg, source_label(src.get("ffmpeg", "")), None),
            (i18n.t(K.TOOL_DENO), info.deno, source_label(src.get("deno", "")), None),
        )
        self.tools_table.setRowCount(len(rows))
        for r, (name, version, state, action) in enumerate(rows):
            self.tools_table.setItem(r, 0, QTableWidgetItem(name))
            self.tools_table.setItem(r, 1, QTableWidgetItem(version or "—"))
            self.tools_table.setItem(r, 2, QTableWidgetItem(state))
            if action == "update":
                btn = SquishyButton(i18n.t(K.UPDATE_NOW), variant="secondary")
                btn.clicked.connect(self._update_ytdlp)
                repair = SquishyButton(i18n.t(K.BTN_REPAIR), variant="ghost")
                repair.clicked.connect(self._repair_components)
                self.tools_table.setCellWidget(r, 3, _actions(btn, repair))
            else:
                self.tools_table.setCellWidget(r, 3, _actions())

    def _refresh_profiles(self) -> None:
        profiles = self.service.profile_list()
        self.profile_table.setRowCount(len(profiles))
        for r, profile in enumerate(profiles):
            name = QTableWidgetItem(profile.name)
            name.setData(Qt.ItemDataRole.UserRole, profile.id)
            self.profile_table.setItem(r, 0, name)
            mode_key = K.MODE_LEGEND if profile.ui_mode == "legend" else K.MODE_SIMPLE
            self.profile_table.setItem(r, 1, QTableWidgetItem(i18n.t(mode_key)))
            ns = QTableWidgetItem(profile.vault_namespace)
            ns.setFont(_mono())
            self.profile_table.setItem(r, 2, ns)
            self.profile_table.setItem(r, 3, QTableWidgetItem(i18n.t(K.PROFILE_ACTIVE) if profile.active else ""))

    # -- eylemler ----------------------------------------------------------
    def _say(self, text: str) -> None:
        self.status.setText(text)

    def _report(self, exc: BaseException) -> None:
        err = self.ctx.report_error(exc, {"source": "settings"})
        self._say(err.summary if isinstance(err, SmartError) else str(exc))

    def _open_drawer(self, provider_id: str) -> None:
        if self.guide.select(provider_id):
            self.drawer.setVisible(True)

    def _add_key(self, provider_id: str, name: str) -> None:
        text, ok = QInputDialog.getText(self, i18n.t(K.VAULT_KEY_TITLE, name=name),
                                        i18n.t(K.VAULT_KEY_PROMPT), echo=_password_mode())
        if not ok:
            return
        try:
            self.service.save_key(provider_id, text)
            self._say(i18n.t(K.VAULT_SAVED))
        except ValueError as exc:
            self._say(str(exc))
        except Exception as exc:  # noqa: BLE001 — kasa kilitliyse SmartError
            self._report(exc)

    def _start_local(self, provider_id: str) -> None:
        code = self.service.start_local(provider_id)
        name = next((rt.name for rt in self.service.local_runtimes() if rt.provider_id == provider_id),
                    provider_id)
        message = {
            "started": (K.LOCAL_STARTED, {"name": name}),
            "missing": (K.LOCAL_NOT_FOUND, {"name": name}),
            "need_path": (K.LOCAL_NEED_PATH, {}),
            "installed": (K.LOCAL_INSTALLED, {}),
            "missing_module": (K.LOCAL_MISSING_MODULE, {}),
        }.get(code)
        if message is not None:
            self._say(i18n.t(message[0], **message[1]))
        self.service.refresh_local(background=True)

    def _pick_model(self, rt: LocalRuntime) -> None:
        choices = list(WHISPER_MODELS) if rt.provider_id == "whisper_local" else list(rt.models)
        if not choices:
            self._say(i18n.t(K.LOCAL_MODEL_NONE))
            return
        item, ok = QInputDialog.getItem(self, i18n.t(K.LOCAL_MODEL_TITLE), "", choices, 0, False)
        if not ok or not item:
            return
        if rt.provider_id == "whisper_local":
            self.service.set_whisper_model(item)
        else:
            self.service.set_model(rt.provider_id, item)

    def _pick_project(self) -> None:
        start = self.service.project_root() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, i18n.t(K.FOLDER_PICK_TITLE), start)
        if path:
            try:
                self.service.set_project_root(path)
            except ValueError as exc:
                self._say(str(exc))

    def _pick_comfy(self) -> None:
        path = QFileDialog.getExistingDirectory(self, i18n.t(K.PATH_COMFYUI), str(Path.home()))
        if path:
            try:
                self.service.set_comfyui_path(path)
            except ValueError as exc:
                self._say(str(exc))

    def _clean(self) -> None:
        size = self.service.cache_bytes()
        if size <= 0:
            self._say(i18n.t(K.CLEAN_NOTHING))
            return
        answer = QMessageBox.question(self, i18n.t(K.CLEAN_CONFIRM_TITLE),
                                      i18n.t(K.CLEAN_CONFIRM_TEXT, size=human_size(size)))
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            report = self.service.clean_cache()
            self._say(i18n.t(K.CLEAN_DONE, count=report.removed, size=human_size(report.freed)))
        except ValueError as exc:
            self._say(str(exc))
        except Exception as exc:  # noqa: BLE001
            self._report(exc)
        self._refresh_paths()

    def _update_ytdlp(self) -> None:
        try:
            self.service.update_ytdlp()
            self._say(i18n.t(K.UPDATE_QUEUED))
        except (ValueError, RuntimeError) as exc:
            self._say(str(exc))

    def _repair_components(self) -> None:
        try:
            self.service.repair_components()
            self._say(i18n.t(K.REPAIR_QUEUED))
        except (ValueError, RuntimeError) as exc:
            self._say(str(exc))

    def _selected_profile(self) -> Optional[str]:
        row = self.profile_table.currentRow()
        if row < 0:
            return None
        item = self.profile_table.item(row, 0)
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _new_profile(self) -> None:
        text, ok = QInputDialog.getText(self, i18n.t(K.BTN_NEW_PROFILE), i18n.t(K.PROFILE_NAME_PROMPT))
        if not ok:
            return
        try:
            self.service.create_profile(text)
        except (ProfileError, ValueError) as exc:
            self._say(str(exc))

    def _switch_profile(self) -> None:
        pid = self._selected_profile()
        if not pid:
            return
        try:
            profile = self.service.switch_profile(pid)
            self._say(i18n.t(K.PROFILE_SWITCHED, name=profile.name))
        except ProfileError as exc:
            self._say(str(exc))

    def _delete_profile(self) -> None:
        pid = self._selected_profile()
        if not pid:
            return
        answer = QMessageBox.question(self, i18n.t(K.BTN_DELETE_KEY), i18n.t(K.PROFILE_DELETE_CONFIRM))
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.delete_profile(pid)
        except ProfileError as exc:
            self._say(str(exc))


def _local_state_text(rt: LocalRuntime) -> str:
    if rt.provider_id == "whisper_local":
        return i18n.t(K.LOCAL_INSTALLED if rt.state == "up" else K.LOCAL_MISSING_MODULE)
    return i18n.t({"up": K.LED_UP, "down": K.LED_DOWN}.get(rt.state, K.LED_MISSING))


def _led_color(tk, state: str) -> str:
    return {"ok": tk.good, "fail": tk.bad, "checking": tk.warn}.get(state, tk.text2)


def _password_mode():
    from PySide6.QtWidgets import QLineEdit
    return QLineEdit.EchoMode.Password
