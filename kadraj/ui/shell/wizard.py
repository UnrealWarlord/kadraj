"""İlk açılış sihirbazı — 3 soru (tuval: Onboarding): ne yapmak istiyorsun → kayıt yeri → API anahtarı.

Bu dosya **arayüz iskeletidir**; cevapları ``WizardAnswers`` olarak toplar. Veri tarafı (kök klasörü
oluşturma, boş alan, klasör iskeleti) Adım 2'de ``storage/folders.py``/``disk.py`` ile dolar:
sihirbaz yalnızca ``core.settings.apply_wizard_answers`` çağırır. API anahtarı **bu adımda
saklanmaz**; kasa arayüzü Adım 3/5'tedir ("yapıştır" cevabı yalnızca Ayarlar'a yönlendirir).

Telefonda (mobil 6. adım) pencere ekran kadar, sorular kayar, düğmeler alt satıra geçer. Android'de kayıt yeri sorusu
yalnız uygulamanın klasörünü sunar (masaüstü yok; "başka yer" seçicisi Android'in belge seçicisine bağlı değil).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget)

from ... import device
from ...core import i18n
from ...core.contracts import WIZARD_API, WIZARD_PLACES, WIZARD_TASKS, WizardAnswers
from ...core.settings import default_project_root
from .. import adaptive
from ..widgets.choice import ChoiceCard, ChoiceGroup
from ..widgets.lockup import Lockup
from ..widgets.squishy_button import SquishyButton
from ..widgets.stepper import Stepper

_TASK_ICONS = {"dl": "download", "rec": "mic", "edit": "film", "photo": "image", "film": "clapper",
               "unsure": "help"}
_PLACE_ICONS = {"videos": "folder", "desktop": "desktop", "custom": "folder-plus"}
_API_ICONS = {"yes": "key", "how": "book", "skip": "arrow-right"}


def free_space_text(path: Path) -> str:
    """"412 GB boş"; yol yoksa en yakın var olan üst klasörün diskine bakar; hata → boş metin."""
    probe = path
    for _ in range(8):
        if probe.exists():
            break
        probe = probe.parent
    try:
        gb = shutil.disk_usage(str(probe)).free / 1e9
    except OSError:
        return ""
    return i18n.t("wizard.free_gb", gb=f"{gb:.0f}")


class WizardDialog(QDialog):
    finishedWithAnswers = Signal(object)          # WizardAnswers

    def __init__(self, parent: Optional[QWidget] = None, initial: Optional[WizardAnswers] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.t("wizard.window_title"))
        self.setModal(True)
        adaptive.fit_dialog(self, 960, 720)
        self._step = 0
        self._custom_path = ""
        init = (initial or WizardAnswers()).normalized()

        phone = adaptive.is_phone()
        root = QVBoxLayout(self)
        root.setContentsMargins(*((14, 12, 14, 12) if phone else (36, 30, 36, 24)))
        root.setSpacing(adaptive.margin(18, 10))

        head = adaptive.hbox()
        head.addWidget(Lockup(wordmark_px=22 if phone else 34, show_mark=True), 0, Qt.AlignmentFlag.AlignLeft)
        head.addStretch(1)
        right = QVBoxLayout()
        self.step_label = QLabel("")
        self.step_label.setProperty("role", "muted")
        self.step_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.stepper = Stepper(3, 0)
        self.stepper.setFixedWidth(220)
        right.addWidget(self.step_label)
        right.addWidget(self.stepper)
        head.addLayout(right)
        root.addLayout(head)

        self.question = QLabel("")
        self.question.setProperty("role", "title")
        self.question.setWordWrap(True)
        self.hint = QLabel("")
        self.hint.setProperty("role", "muted")
        self.hint.setWordWrap(True)
        root.addWidget(self.question)
        root.addWidget(self.hint)

        self.stack = adaptive.stack()
        self.stack.addWidget(self._top_aligned(self._build_tasks(init)))
        self.stack.addWidget(self._top_aligned(self._build_places(init)))
        self.stack.addWidget(self._top_aligned(self._build_api(init)))
        if adaptive.is_mobile():                              # kartlar ekrana sığmazsa dikeyde kayar
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(self.stack)
            root.addWidget(scroll, 1)
        else:
            root.addWidget(self.stack, 1)

        foot = QFrame()
        foot.setProperty("role", "topbar")
        fl = adaptive.hbox(foot)
        fl.setContentsMargins(0, 14, 0, 0)
        self.footer_hint = QLabel(i18n.t("wizard.change_later"))
        self.footer_hint.setProperty("role", "muted")
        fl.addWidget(self.footer_hint, 1)
        self.back_btn = SquishyButton(i18n.t("wizard.back"), variant="secondary")
        self.next_btn = SquishyButton(i18n.t("wizard.next"), variant="primary")
        self.back_btn.clicked.connect(self.back)
        self.next_btn.clicked.connect(self.next)
        fl.addWidget(self.back_btn)
        fl.addWidget(self.next_btn)
        root.addWidget(foot)
        self._sync()

    # -- sayfalar ---------------------------------------------------------
    @staticmethod
    def _top_aligned(widget: QWidget) -> QWidget:
        """Kartlar sayfa yüksekliğine yayılmasın: üste hizala, altta boşluk bırak."""
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(widget)
        lay.addStretch(1)
        return host

    def _build_tasks(self, init: WizardAnswers) -> QWidget:
        cards = [ChoiceCard(tid, i18n.t(f"wizard.task.{tid}"), i18n.t(f"wizard.task.{tid}.sub"),
                            _TASK_ICONS[tid]) for tid in WIZARD_TASKS]
        self.tasks = ChoiceGroup(cards, exclusive=False, columns=3)
        self.tasks.set_selected(list(init.tasks))
        return self.tasks

    def _place_path(self, place: str) -> Path:
        return default_project_root(place, self._custom_path)

    def _build_places(self, init: WizardAnswers) -> QWidget:
        self._custom_path = init.root_path
        cards = []
        for pid in WIZARD_PLACES:
            sub = str(self._place_path(pid)) if pid != "custom" else (self._custom_path or i18n.t("wizard.place.custom.sub"))
            trailing = free_space_text(self._place_path(pid)) if pid != "custom" else i18n.t("wizard.place.pick")
            cards.append(ChoiceCard(pid, i18n.t(f"wizard.place.{pid}"), sub, _PLACE_ICONS[pid],
                                    trailing=trailing))
        self.places = ChoiceGroup(cards, exclusive=True, columns=1)
        place = init.place
        if device.is_android():                               # yalnız uygulamanın klasörü (masaüstü ve seçici yok)
            for card in cards:
                card.setVisible(card.choice_id == "videos")
            place = "videos"
        self.places.set_selected([place])
        self.places.cards()[2].clicked.connect(self._pick_custom)
        return self.places

    def _pick_custom(self) -> None:
        start = self._custom_path or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, i18n.t("wizard.place.pick_title"), start)
        if chosen:
            self._custom_path = chosen
            card = self.places.cards()[2]
            card.set_texts(i18n.t("wizard.place.custom"), chosen, free_space_text(Path(chosen)))
        elif not self._custom_path:
            self.places.set_selected(["videos"])

    def _build_api(self, init: WizardAnswers) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        cards = [ChoiceCard(aid, i18n.t(f"wizard.api.{aid}"), i18n.t(f"wizard.api.{aid}.sub"), _API_ICONS[aid])
                 for aid in WIZARD_API]
        self.api = ChoiceGroup(cards, exclusive=True, columns=3)
        self.api.set_selected([init.api])
        self.api.selectionChanged.connect(lambda _s: self._sync_api_note())
        lay.addWidget(self.api)
        self.api_note = QLabel("")
        self.api_note.setWordWrap(True)
        self.api_note.setProperty("role", "card")
        self.api_note.setContentsMargins(20, 16, 20, 16)
        lay.addWidget(self.api_note)
        lay.addStretch(1)
        self._sync_api_note()
        return wrap

    def _sync_api_note(self) -> None:
        sel = self.api.selected()
        key = {"yes": "wizard.api.note_yes", "how": "wizard.api.note_how"}.get(sel[0] if sel else "", "")
        self.api_note.setText(i18n.t(key) if key else "")
        self.api_note.setVisible(bool(key))

    # -- gezinti ----------------------------------------------------------
    @property
    def step(self) -> int:
        return self._step

    def _sync(self) -> None:
        n = self._step + 1
        self.question.setText(i18n.t(f"wizard.q{n}"))
        self.hint.setText(i18n.t(f"wizard.q{n}.hint"))
        self.step_label.setText(i18n.t("wizard.step_of", step=n, total=3, name=i18n.t(f"wizard.step{n}")))
        self.stepper.set_current(self._step)
        self.stack.setCurrentIndex(self._step)
        self.back_btn.setVisible(self._step > 0)
        self.next_btn.setText(i18n.t("wizard.finish") if self._step == 2 else i18n.t("wizard.next"))

    def back(self) -> None:
        if self._step > 0:
            self._step -= 1
            self._sync()

    def next(self) -> None:
        if self._step < 2:
            self._step += 1
            self._sync()
        else:
            self.finish()

    def answers(self) -> WizardAnswers:
        tasks: List[str] = self.tasks.selected()
        place = (self.places.selected() or ["videos"])[0]
        api = (self.api.selected() or ["skip"])[0]
        return WizardAnswers(tuple(tasks), place, self._custom_path if place == "custom" else "", api).normalized()

    def finish(self) -> None:
        self.finishedWithAnswers.emit(self.answers())
        self.accept()
