"""Ayarlar — Basit Mod sayfası. Teknik terim yok; ayrıntılar Legend'de.

Sayfa yalnızca görünümdür: durum ``SettingsService``'tedir. Göz düğmesi tam anahtarı hiçbir
zaman göstermez; yalnızca ``mask_preview`` çıktısını (ilk 6 + … + son 4) gösterir.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog,
                               QLabel, QLayout, QLineEdit, QMessageBox, QProgressBar, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from ...core import i18n
from ...core.contracts import Accent, SmartError
from ...storage.disk import human_size
from ...storage.profiles import ProfileError
from ...ui.theme.manager import ThemedMixin, current_tokens
from ...ui.theme.tokens import resolve
from ...ui.widgets import Lockup, SquishyButton
from ...ui.widgets.surface import page_surface_qss
from .guide import GUIDE_HOSTS, GuideDialog
from .service import WHISPER_MODELS, KeyRow, LocalRuntime, initials
from .strings import K

_KIND_ACCENT = {"llm": Accent.PEACH, "image": Accent.LAVENDER, "video": Accent.MINT, "tts": Accent.BUTTER,
                "stt": Accent.PINK, "stock": Accent.NEUTRAL, "local": Accent.NEUTRAL}


def _clear(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


def _card(title_key: Optional[str] = None) -> tuple:
    frame = QFrame()
    frame.setProperty("role", "card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(10)
    if title_key:
        head = QLabel(i18n.t(title_key))
        head.setProperty("role", "h2")
        lay.addWidget(head)
    return frame, lay


class _Preview(QWidget):
    """Modun küçük önizlemesi: o modun kendi tokenlarıyla çizilir (koyu/pastel, keskin/yuvarlak)."""

    def __init__(self, mode: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._mode = mode
        self._selected = False
        self.setMinimumHeight(120)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        tk = resolve(self._mode)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QPen(QColor(tk.primary if self._selected else tk.border), 3 if self._selected else 1))
        p.setBrush(QColor(tk.bg))
        p.drawRoundedRect(r, tk.radius_card, tk.radius_card)
        card = QRectF(r.left() + 12, r.top() + 12, r.width() * 0.56, r.height() - 24)
        p.setPen(QPen(QColor(tk.border), 1))
        p.setBrush(QColor(tk.card))
        p.drawRoundedRect(card, tk.radius_card, tk.radius_card)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(tk.primary))
        p.drawRoundedRect(QRectF(card.left() + 10, card.top() + 10, card.width() * 0.5, 14), tk.radius_ctl,
                          tk.radius_ctl)
        p.setBrush(QColor(tk.text2))
        for i in range(3):
            p.drawRoundedRect(QRectF(card.left() + 10, card.top() + 36 + i * 16, card.width() - 20, 7),
                              3, 3)
        side = QRectF(card.right() + 10, r.top() + 12, r.right() - card.right() - 22, r.height() - 24)
        p.setBrush(QColor(tk.surface2))
        p.drawRoundedRect(side, tk.radius_card, tk.radius_card)
        p.end()


class _ModeCard(QFrame):
    chosen = Signal(str)

    def __init__(self, mode: str, title: str, sub: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.mode = mode
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        lay = QVBoxLayout(self)
        self.preview = _Preview(mode)
        lay.addWidget(self.preview)
        self.title = QLabel(title)
        self.title.setProperty("role", "h2")
        lay.addWidget(self.title)
        self.sub = QLabel(sub)
        self.sub.setProperty("role", "muted")
        self.sub.setWordWrap(True)
        lay.addWidget(self.sub)
        self.marker = QLabel("")
        self.marker.setProperty("role", "hint")
        lay.addWidget(self.marker)

    def set_current(self, current: bool, current_text: str, choose_text: str) -> None:
        self.preview.set_selected(current)
        self.marker.setText(current_text if current else choose_text)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.chosen.emit(self.mode)
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.chosen.emit(self.mode)
            return
        super().keyPressEvent(event)


class SettingsSimplePage(ThemedMixin, QWidget):
    def __init__(self, ctx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("settings_simple")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)   # kök zemini QSS ile boyanır
        self.ctx = ctx
        service = ctx.service("settings")
        if service is None:
            raise RuntimeError("Ayarlar servisi kullanılamıyor")
        self.service = service
        self._revealed: Set[str] = set()
        self._dialogs: List[QWidget] = []
        self._build()
        service.changed.connect(self._on_changed)
        self._refresh_all()
        service.ensure_local()
        service.ensure_tools()
        self._bind_theme()
        self._on_theme()

    def _on_theme(self) -> None:
        """Basit tokenlarından zemin (tema değişince yenilenir)."""
        self.setStyleSheet(page_surface_qss(self.tk, "settings_simple"))

    # -- yapı --------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        col = QVBoxLayout(body)
        col.setSpacing(16)
        scroll.setWidget(body)
        root.addWidget(scroll)

        title = QHBoxLayout()
        before = QLabel(i18n.t(K.TITLE_BEFORE))
        before.setProperty("role", "title")
        after = QLabel(i18n.t(K.TITLE_AFTER))
        after.setProperty("role", "title")
        title.addWidget(before)
        title.addWidget(Lockup(34))
        title.addWidget(after)
        title.addStretch(1)
        col.addLayout(title)

        col.addWidget(self._mode_card())
        col.addWidget(self._vault_card())
        col.addWidget(self._local_card())
        col.addWidget(self._folder_card())
        col.addWidget(self._view_card())
        col.addWidget(self._profile_card())
        col.addWidget(self._tools_card())
        self.status = QLabel("")
        self.status.setProperty("role", "hint")
        self.status.setWordWrap(True)
        col.addWidget(self.status)
        col.addStretch(1)

    def _mode_card(self) -> QFrame:
        frame, lay = _card()
        row = QHBoxLayout()
        self.mode_cards: Dict[str, _ModeCard] = {}
        for mode, name_key, sub_key in (("simple", K.MODE_SIMPLE, K.MODE_SIMPLE_SUB),
                                        ("legend", K.MODE_LEGEND, K.MODE_LEGEND_SUB)):
            card = _ModeCard(mode, i18n.t(name_key), i18n.t(sub_key))
            card.chosen.connect(self._choose_mode)
            self.mode_cards[mode] = card
            row.addWidget(card, 1)
        lay.addLayout(row)
        return frame

    def _vault_card(self) -> QFrame:
        frame, lay = _card(K.VAULT_TITLE)
        note = QLabel(i18n.t(K.VAULT_NOTE))
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.vault_banner = QLabel("")
        self.vault_banner.setProperty("role", "hint")
        self.vault_banner.setWordWrap(True)
        lay.addWidget(self.vault_banner)
        self.key_box = QVBoxLayout()
        lay.addLayout(self.key_box)
        footer = QHBoxLayout()
        footer.addStretch(1)
        test_all = SquishyButton(i18n.t(K.BTN_TEST_ALL), variant="secondary")
        test_all.clicked.connect(lambda: self.service.test_all(background=True))
        footer.addWidget(test_all)
        lay.addLayout(footer)
        return frame

    def _local_card(self) -> QFrame:
        frame, lay = _card(K.LOCAL_TITLE)
        self.local_box = QVBoxLayout()
        lay.addLayout(self.local_box)
        return frame

    def _folder_card(self) -> QFrame:
        frame, lay = _card(K.FOLDER_TITLE)
        row = QHBoxLayout()
        self.folder_path = QLabel("")
        self.folder_path.setFont(_mono())
        self.folder_path.setProperty("role", "pill")
        self.folder_path.setWordWrap(True)
        pick = SquishyButton(i18n.t(K.BTN_PICK_FOLDER), variant="secondary")
        pick.clicked.connect(self._pick_folder)
        row.addWidget(self.folder_path, 1)
        row.addWidget(pick)
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
        return frame

    def _view_card(self) -> QFrame:
        frame, lay = _card(K.VIEW_TITLE)
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
        return frame

    def _profile_card(self) -> QFrame:
        frame, lay = _card(K.PROFILE_TITLE)
        row = QHBoxLayout()
        self.profile_combo = QComboBox()
        switch = SquishyButton(i18n.t(K.BTN_SWITCH), variant="secondary")
        switch.clicked.connect(self._switch_profile)
        new = SquishyButton(i18n.t(K.BTN_NEW_PROFILE), variant="ghost")
        new.clicked.connect(self._new_profile)
        row.addWidget(self.profile_combo, 1)
        row.addWidget(switch)
        row.addWidget(new)
        lay.addLayout(row)
        return frame

    def _tools_card(self) -> QFrame:
        frame, lay = _card(K.TOOLS_TITLE)
        row = QHBoxLayout()
        self.ytdlp_label = QLabel("")
        self.ytdlp_update = SquishyButton(i18n.t(K.BTN_UPDATE), variant="secondary")
        self.ytdlp_update.clicked.connect(self._update_ytdlp)
        row.addWidget(self.ytdlp_label, 1)
        row.addWidget(self.ytdlp_update)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        self.ffmpeg_label = QLabel("")
        how = SquishyButton(i18n.t(K.BTN_HOW_INSTALL), variant="ghost")
        how.clicked.connect(self._show_install)
        row2.addWidget(self.ffmpeg_label, 1)
        row2.addWidget(how)
        lay.addLayout(row2)
        note = QLabel(i18n.t(K.UPDATE_NOTE))
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        return frame

    # -- yenileme ----------------------------------------------------------
    def _on_changed(self, topic: str) -> None:
        if topic == "keys":
            self._refresh_keys()
            self._refresh_banner()
        elif topic == "local":
            self._refresh_local()
        elif topic == "tools":
            self._refresh_tools()
        elif topic == "folder":
            self._refresh_folder()
        elif topic == "view":
            self._refresh_view()
            self._refresh_mode()
        elif topic == "profiles":
            self._refresh_profiles()

    def _refresh_all(self) -> None:
        self._refresh_mode()
        self._refresh_banner()
        self._refresh_keys()
        self._refresh_local()
        self._refresh_folder()
        self._refresh_view()
        self._refresh_profiles()
        self._refresh_tools()

    def _refresh_mode(self) -> None:
        current = self.service.ui_mode()
        for mode, card in self.mode_cards.items():
            card.set_current(mode == current, i18n.t(K.MODE_CURRENT), i18n.t(K.MODE_CHOOSE))

    def _refresh_banner(self) -> None:
        state, message = self.service.vault_state()
        self.vault_banner.setText("" if state == "ready" else message)

    def _refresh_keys(self) -> None:
        _clear(self.key_box)
        for row in self.service.key_rows():
            self.key_box.addWidget(self._key_row(row))

    def _reveal_text(self, row: KeyRow) -> str:
        if row.provider_id in self._revealed and row.has_key:
            return self.service.key_preview(row.provider_id) or row.masked
        return row.masked or i18n.t(K.NO_KEY)

    def _key_row(self, row: KeyRow) -> QFrame:
        tk = current_tokens()
        frame = QFrame()
        hl = QHBoxLayout(frame)
        hl.setContentsMargins(0, 4, 0, 4)
        accent = tk.accent(_KIND_ACCENT.get(row.kind, Accent.NEUTRAL))
        avatar = QLabel(initials(row.name))
        avatar.setFixedSize(38, 38)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(f"background:{accent.tone}; color:{accent.ink}; border-radius:19px; font-weight:600;")
        hl.addWidget(avatar)
        names = QVBoxLayout()
        name = QLabel(row.name)
        name.setProperty("role", "h2")
        usage = QLabel(row.usage)
        usage.setProperty("role", "muted")
        names.addWidget(name)
        names.addWidget(usage)
        hl.addLayout(names, 1)
        pill = QLabel(self._reveal_text(row))
        pill.setFont(_mono())
        pill.setProperty("role", "pill")
        hl.addWidget(pill)
        eye = QPushButton(i18n.t(K.BTN_HIDE if row.provider_id in self._revealed else K.BTN_REVEAL))
        eye.setProperty("role", "topbtn")
        eye.setEnabled(row.has_key)
        eye.setAccessibleName(i18n.t(K.BTN_REVEAL))
        eye.clicked.connect(lambda _c=False, pid=row.provider_id: self._toggle_reveal(pid))
        hl.addWidget(eye)
        led = QLabel("●  " + i18n.t(_LED_KEYS.get(row.state, K.LED_IDLE)))
        led.setStyleSheet(f"color:{_led_color(tk, row.state)};")
        hl.addWidget(led)
        if row.latency_ms:
            hl.addWidget(QLabel(i18n.t(K.LATENCY, ms=int(row.latency_ms))))
        test = SquishyButton(i18n.t(K.BTN_TEST), variant="secondary")
        test.clicked.connect(lambda _c=False, pid=row.provider_id: self.service.test_connection(pid))
        hl.addWidget(test)
        key_btn = SquishyButton(i18n.t(K.BTN_CHANGE_KEY if row.has_key else K.BTN_ADD_KEY), variant="ghost")
        key_btn.clicked.connect(lambda _c=False, pid=row.provider_id, nm=row.name: self._add_key(pid, nm))
        hl.addWidget(key_btn)
        if row.has_key:
            delete = SquishyButton(i18n.t(K.BTN_DELETE_KEY), variant="ghost")
            delete.clicked.connect(lambda _c=False, pid=row.provider_id, nm=row.name: self._delete_key(pid, nm))
            hl.addWidget(delete)
        guide = SquishyButton(i18n.t(K.BTN_GUIDE), variant="ghost")
        guide.clicked.connect(lambda _c=False, pid=row.provider_id: self._open_guide(pid))
        hl.addWidget(guide)
        return frame

    def _refresh_local(self) -> None:
        _clear(self.local_box)
        runtimes = self.service.local_runtimes()
        if not runtimes:
            hint = QLabel(i18n.t(K.LED_CHECKING))
            hint.setProperty("role", "muted")
            self.local_box.addWidget(hint)
            return
        for rt in runtimes:
            self.local_box.addWidget(self._local_row(rt))

    def _local_row(self, rt: LocalRuntime) -> QFrame:
        tk = current_tokens()
        frame = QFrame()
        hl = QHBoxLayout(frame)
        hl.setContentsMargins(0, 4, 0, 4)
        names = QVBoxLayout()
        name = QLabel(rt.name)
        name.setProperty("role", "h2")
        detail_text = ", ".join(rt.models[:4]) if rt.models else (rt.endpoint or rt.detail)
        detail = QLabel(detail_text or "—")
        detail.setProperty("role", "muted")
        names.addWidget(name)
        names.addWidget(detail)
        hl.addLayout(names, 1)
        if rt.provider_id == "whisper_local":
            state_key = K.LOCAL_INSTALLED if rt.state == "up" else K.LOCAL_MISSING_MODULE
            state_color = tk.good if rt.state == "up" else tk.warn
        else:
            state_key = {"up": K.LED_UP, "down": K.LED_DOWN}.get(rt.state, K.LED_MISSING)
            state_color = tk.good if rt.state == "up" else (tk.warn if rt.state == "down" else tk.bad)
        state = QLabel("●  " + i18n.t(state_key))
        state.setStyleSheet(f"color:{state_color};")
        hl.addWidget(state)
        if rt.provider_id in ("ollama", "comfyui"):
            start = SquishyButton(i18n.t(K.BTN_START), variant="secondary")
            start.clicked.connect(lambda _c=False, pid=rt.provider_id: self._start_local(pid))
            hl.addWidget(start)
        if rt.provider_id in ("ollama", "whisper_local"):
            pick = SquishyButton(i18n.t(K.BTN_PICK_MODEL), variant="ghost")
            pick.clicked.connect(lambda _c=False, r=rt: self._pick_model(r))
            hl.addWidget(pick)
        return frame

    def _refresh_folder(self) -> None:
        self.folder_path.setText(self.service.project_root() or str(_home()))
        info = self.service.folder_info()
        if info is None:
            self.disk_bar.setValue(0)
            self.disk_text.setText("—")
        else:
            self.disk_bar.setValue(int(info.used_fraction * 100))
            self.disk_text.setText(i18n.t(K.FOLDER_FREE, free=human_size(info.free),
                                          total=human_size(info.total)))
        size = self.service.cache_bytes()
        self.clean_btn.setText(i18n.t(K.CLEAN_BTN, size=human_size(size)))

    def _refresh_view(self) -> None:
        boxes = ((self.cb_large, self.service.a11y("large_text")),
                 (self.cb_contrast, self.service.a11y("high_contrast")),
                 (self.cb_motion, self.service.a11y("reduced_motion")),
                 (self.cb_tips, self.service.tips_enabled()))
        for box, value in boxes:
            box.blockSignals(True)
            box.setChecked(bool(value))
            box.blockSignals(False)

    def _refresh_profiles(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        active_index = 0
        for i, profile in enumerate(self.service.profile_list()):
            label = profile.name + (" ✓" if profile.active else "")
            self.profile_combo.addItem(label, profile.id)
            if profile.active:
                active_index = i
        self.profile_combo.setCurrentIndex(active_index)
        self.profile_combo.blockSignals(False)

    def _refresh_tools(self) -> None:
        info = self.service.tool_info()
        self.ytdlp_label.setText(f"{i18n.t(K.TOOL_YTDLP)}: {info.ytdlp or i18n.t(K.TOOL_MISSING)}")
        self.ffmpeg_label.setText(f"{i18n.t(K.TOOL_FFMPEG)}: {info.ffmpeg or i18n.t(K.TOOL_MISSING)}")

    # -- eylemler ----------------------------------------------------------
    def _say(self, text: str) -> None:
        self.status.setText(text)

    def _report(self, exc: BaseException) -> None:
        err = self.ctx.report_error(exc, {"source": "settings"})
        self._say(err.summary if isinstance(err, SmartError) else str(exc))

    def _choose_mode(self, mode: str) -> None:
        self.service.set_ui_mode(mode)

    def _toggle_reveal(self, provider_id: str) -> None:
        if provider_id in self._revealed:
            self._revealed.discard(provider_id)
        else:
            self._revealed.add(provider_id)
        self._refresh_keys()

    def _add_key(self, provider_id: str, name: str) -> None:
        text, ok = QInputDialog.getText(self, i18n.t(K.VAULT_KEY_TITLE, name=name),
                                        i18n.t(K.VAULT_KEY_PROMPT), QLineEdit.EchoMode.Password)
        if not ok:
            return
        try:
            self.service.save_key(provider_id, text)
            self._say(i18n.t(K.VAULT_SAVED))
        except ValueError as exc:
            self._say(str(exc))
        except Exception as exc:  # noqa: BLE001 — kasa kilitliyse SmartError kartla gösterilir
            self._report(exc)

    def _delete_key(self, provider_id: str, name: str) -> None:
        answer = QMessageBox.question(self, i18n.t(K.BTN_DELETE_KEY), i18n.t(K.VAULT_DELETE_CONFIRM, name=name))
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if self.service.delete_key(provider_id):
                self._revealed.discard(provider_id)
                self._say(i18n.t(K.VAULT_DELETED))
        except Exception as exc:  # noqa: BLE001
            self._report(exc)

    def _open_guide(self, provider_id: str) -> None:
        names = {row.provider_id: row.name for row in self.service.key_rows()}
        providers = {pid: names[pid] for pid, _host in GUIDE_HOSTS if pid in names}
        dialog = GuideDialog(providers, self, start=provider_id)
        self._dialogs.append(dialog)
        dialog.open()

    def _start_local(self, provider_id: str) -> None:
        code = self.service.start_local(provider_id)
        names = {rt.provider_id: rt.name for rt in self.service.local_runtimes()}
        name = names.get(provider_id, provider_id)
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
        if rt.provider_id == "whisper_local":
            choices = list(WHISPER_MODELS)
        else:
            choices = list(rt.models)
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

    def _pick_folder(self) -> None:
        start = self.service.project_root() or str(_home())
        path = QFileDialog.getExistingDirectory(self, i18n.t(K.FOLDER_PICK_TITLE), start)
        if not path:
            return
        try:
            self.service.set_project_root(path)
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
        self._refresh_folder()

    def _switch_profile(self) -> None:
        pid = self.profile_combo.currentData()
        if not pid:
            return
        try:
            profile = self.service.switch_profile(str(pid))
            self._say(i18n.t(K.PROFILE_SWITCHED, name=profile.name))
        except ProfileError as exc:
            self._say(str(exc))

    def _new_profile(self) -> None:
        text, ok = QInputDialog.getText(self, i18n.t(K.BTN_NEW_PROFILE), i18n.t(K.PROFILE_NAME_PROMPT))
        if not ok:
            return
        try:
            self.service.create_profile(text)
        except (ProfileError, ValueError) as exc:
            self._say(str(exc))

    def _update_ytdlp(self) -> None:
        try:
            self.service.update_ytdlp()
            self._say(i18n.t(K.UPDATE_QUEUED))
        except (ValueError, RuntimeError) as exc:
            self._say(str(exc))

    def _show_install(self) -> None:
        lines = [i18n.t(K.INSTALL_NOTE), ""]
        lines += [f"{label}: {cmd}" for label, cmd in self.service.ffmpeg_hints()]
        QMessageBox.information(self, i18n.t(K.INSTALL_TITLE), "\n".join(lines))


_LED_KEYS = {"ok": K.LED_OK, "fail": K.LED_FAIL, "idle": K.LED_IDLE, "checking": K.LED_CHECKING}


def _led_color(tk, state: str) -> str:
    return {"ok": tk.good, "fail": tk.bad, "checking": tk.warn}.get(state, tk.text2)


def _mono() -> QFont:
    font = QFont()
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def _home():
    from pathlib import Path
    return Path.home()
