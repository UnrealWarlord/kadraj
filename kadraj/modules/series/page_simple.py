"""Basit "Dizi/Film Yap" (tuval: V2Studio · Dizi/Film Yap): üstte aşama çubuğu + "Kaldığın yerden devam edelim mi?" bandı + "Plan & kota"
(Planının %X'i kullanılacak · Daha önce · Bu proje · Kalan) + karakter defteri + sahne panosu (kanban). Her aşama kendi paneliyle gelir;
**onay vermeden bir sonrakine geçilmez**; hiçbir üretim kendiliğinden başlamaz."""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from ...ai_engine.pipeline.machine import RESOLUTIONS, STAGE_ORDER, Stage
from ...core import i18n
from ...core.contracts import Topics
from ...ui.widgets.help_bubble import HelpBubble
from ...ui.widgets.squishy_button import SquishyButton
from .page_base import SeriesPageBase
from .widgets import KanbanBoard, StageBar


def _card() -> tuple:
    f = QFrame()
    f.setProperty("role", "card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(8)
    return f, lay


def _h2(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setProperty("role", "h2")
    lb.setWordWrap(True)
    return lb


def _hint(text: str = "") -> QLabel:
    lb = QLabel(text)
    lb.setProperty("role", "hint")
    lb.setWordWrap(True)
    return lb


class SeriesSimplePage(SeriesPageBase):
    def __init__(self, ctx: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(ctx, parent)
        self.setObjectName("series_simple")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 8, 12)
        lay.setSpacing(12)
        head = QHBoxLayout()
        self.title_label = _h2("")
        self.state_label = _hint()
        head.addWidget(self.title_label)
        head.addWidget(HelpBubble(i18n.t("series.help")))
        head.addStretch(1)
        head.addWidget(self.busy_label)
        lay.addLayout(head)
        self.stage_bar = StageBar()
        lay.addWidget(self.stage_bar)
        lay.addLayout(self.error_slot)
        self.resume_card, rl = _card()
        self.resume_text = _h2("")
        self.resume_btn = SquishyButton(i18n.t("series.resume"), variant="primary", accent="pink")
        rl.addWidget(self.resume_text)
        rl.addWidget(_hint(i18n.t("series.resume.sub")))
        rl.addWidget(self.resume_btn)
        lay.addWidget(self.resume_card)
        self.stack = QStackedWidget()
        self.panels: Dict[Stage, QWidget] = {}
        for st, builder in ((Stage.IDEA, self._p_idea), (Stage.SCREENPLAY, self._p_screenplay), (Stage.FORMAT, self._p_format),
                            (Stage.BIBLE, self._p_bible), (Stage.STORYBOARD, self._p_storyboard), (Stage.PLAN, self._p_plan),
                            (Stage.PRODUCTION, self._p_production), (Stage.ASSEMBLY, self._p_assembly), (Stage.DONE, self._p_assembly)):
            w = builder() if st is not Stage.DONE else self.panels[Stage.ASSEMBLY]
            self.panels[st] = w
            if st is not Stage.DONE:
                self.stack.addWidget(w)
        lay.addWidget(self.stack)
        lay.addWidget(self.state_label)
        lay.addStretch(1)
        self.connect_base()
        self.svc.changed.connect(self.refresh)
        self.svc.stageChanged.connect(lambda _s: self.refresh())
        self.svc.ideasReady.connect(self._show_ideas)
        self.svc.jobChanged.connect(self._on_job)
        self.stage_bar.stageRequested.connect(lambda s: self.svc.goto(Stage(s)))
        self.resume_btn.clicked.connect(self.svc.resume)
        self.refresh()

    # -- paneller -----------------------------------------------------------------------------------------------
    def _p_idea(self) -> QWidget:
        card, l = _card()
        l.addWidget(_h2(i18n.t("series.idea.title")))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText(i18n.t("series.idea.name"))
        self.idea_edit = QPlainTextEdit()
        self.idea_edit.setPlaceholderText(i18n.t("series.idea.example"))
        self.idea_edit.setFixedHeight(90)
        self.brain_btn = SquishyButton(i18n.t("series.idea.brainstorm"), variant="secondary")
        self.idea_list = QListWidget()
        self.idea_list.setMaximumHeight(120)
        self.idea_next = SquishyButton(i18n.t("series.next"), variant="primary", accent="pink")
        for w in (self.title_edit, self.idea_edit, self.brain_btn, self.idea_list, self.idea_next):
            l.addWidget(w)
        self.brain_btn.clicked.connect(self._brainstorm)
        self.idea_list.itemClicked.connect(self._choose_idea)
        self.idea_next.clicked.connect(self._idea_next)
        self._ideas: list = []
        return card

    def _p_screenplay(self) -> QWidget:
        card, l = _card()
        l.addWidget(_h2(i18n.t("series.script.title")))
        l.addWidget(_hint(i18n.t("series.script.sub")))
        self.script_edit = QPlainTextEdit()
        self.script_edit.setMinimumHeight(220)
        row = QHBoxLayout()
        self.write_btn = SquishyButton(i18n.t("series.script.write"), variant="secondary")
        self.export_btn = QPushButton(i18n.t("series.script.export"))
        self.script_ok = SquishyButton(i18n.t("series.approve_next"), variant="primary", accent="pink")
        for w in (self.write_btn, self.export_btn, self.script_ok):
            row.addWidget(w)
        l.addWidget(self.script_edit)
        l.addLayout(row)
        self.write_btn.clicked.connect(self._write_script)
        self.export_btn.clicked.connect(lambda: (self._save_script(), self.svc.export_script()))
        self.script_ok.clicked.connect(lambda: (self._save_script(), self.svc.approve() and self.svc.advance()))
        return card

    def _p_format(self) -> QWidget:
        card, l = _card()
        l.addWidget(_h2(i18n.t("series.format.title")))
        row = QHBoxLayout()
        self.kind_box = QComboBox()
        for k in ("film", "dizi"):
            self.kind_box.addItem(i18n.t(f"series.kind.{k}"), k)
        self.minutes = QDoubleSpinBox()
        self.minutes.setRange(0.5, 240)
        self.minutes.setValue(5)
        self.minutes.setSuffix(i18n.t("series.min"))
        self.episodes = QSpinBox()
        self.episodes.setRange(1, 40)
        self.episodes.setSuffix(i18n.t("series.eps"))
        self.res_box = QComboBox()
        for r in RESOLUTIONS:
            self.res_box.addItem(r.upper() if r != "1080p" else "1080p", r)
        for w in (self.kind_box, self.minutes, self.episodes, self.res_box):
            row.addWidget(w)
        self.format_next = SquishyButton(i18n.t("series.next"), variant="primary", accent="pink")
        l.addLayout(row)
        l.addWidget(self.format_next)
        self.format_next.clicked.connect(self._format_next)
        return card

    def _p_bible(self) -> QWidget:
        card, l = _card()
        l.addWidget(_h2(i18n.t("series.bible.title")))
        l.addWidget(_hint(i18n.t("series.bible.sub")))
        self.bible_btn = SquishyButton(i18n.t("series.bible.write"), variant="secondary")
        l.addWidget(self.bible_btn)
        l.addWidget(_h2(i18n.t("series.chars.title")))
        l.addWidget(_hint(i18n.t("series.chars.sub")))
        self.char_list = QListWidget()
        self.char_list.setMaximumHeight(130)
        row = QHBoxLayout()
        self.char_name, self.char_desc, self.char_voice = QLineEdit(), QLineEdit(), QLineEdit()
        self.char_name.setPlaceholderText(i18n.t("series.chars.name"))
        self.char_desc.setPlaceholderText(i18n.t("series.chars.desc"))
        self.char_voice.setPlaceholderText(i18n.t("series.chars.voice"))
        self.char_add = QPushButton("+ " + i18n.t("series.chars.new"))
        for w in (self.char_name, self.char_desc, self.char_voice, self.char_add):
            row.addWidget(w)
        self.scene_list = QListWidget()
        self.scene_list.setMaximumHeight(150)
        self.bible_ok = SquishyButton(i18n.t("series.approve_next"), variant="primary", accent="pink")
        for w in (self.char_list,):
            l.addWidget(w)
        l.addLayout(row)
        l.addWidget(self.scene_list)
        l.addWidget(self.bible_ok)
        self.bible_btn.clicked.connect(self.svc.write_bible)
        self.char_add.clicked.connect(self._add_char)
        self.bible_ok.clicked.connect(lambda: self.svc.approve() and self.svc.advance())
        return card

    def _p_storyboard(self) -> QWidget:
        card, l = _card()
        self.board_title = _h2("")
        l.addWidget(self.board_title)
        l.addWidget(_hint(i18n.t("series.board.sub")))
        self.keyframes_btn = SquishyButton(i18n.t("series.board.make"), variant="secondary")
        self.board = KanbanBoard()
        self.board.setMinimumHeight(260)
        self.board_ok = SquishyButton(i18n.t("series.approve_next"), variant="primary", accent="pink")
        self.approve_all = QPushButton(i18n.t("series.board.approve_all"))
        row = QHBoxLayout()
        row.addWidget(self.keyframes_btn)
        row.addWidget(self.approve_all)
        l.addLayout(row)
        l.addWidget(self.board, 1)
        l.addWidget(self.board_ok)
        self.keyframes_btn.clicked.connect(lambda: self.svc.generate_keyframes())
        self.approve_all.clicked.connect(self._approve_all)
        self.board.cardMoved.connect(self.svc.move_card)
        self.board.angleChanged.connect(self.svc.set_angle)
        self.board.regenerateRequested.connect(self.svc.regenerate_scene)
        self.board.selected.connect(lambda sid: self.board.set_selected_angle(self.svc.project.scene(sid).angle if self.svc.project.scene(sid) else "medium"))
        self.board_ok.clicked.connect(lambda: self.svc.approve() and self.svc.advance())
        return card

    def _p_plan(self) -> QWidget:
        card, l = _card()
        l.addWidget(_h2(i18n.t("series.plan.title")))
        self.plan_big = _h2("")
        self.plan_line = QLabel("")
        self.plan_line.setProperty("role", "mono")
        self.plan_detail = _hint()
        row = QHBoxLayout()
        self.dub_edit = QLineEdit()
        self.dub_edit.setPlaceholderText(i18n.t("series.plan.dub"))
        self.dub_btn = QPushButton(i18n.t("series.plan.dub_go"))
        self.foley_btn = QPushButton(i18n.t("series.plan.foley"))
        for w in (self.dub_edit, self.dub_btn, self.foley_btn):
            row.addWidget(w)
        self.plan_ok = SquishyButton(i18n.t("series.plan.start"), variant="primary", accent="pink")
        for w in (self.plan_big, self.plan_line, self.plan_detail):
            l.addWidget(w)
        l.addLayout(row)
        l.addWidget(self.plan_ok)
        self.dub_btn.clicked.connect(self._dub)
        self.foley_btn.clicked.connect(self.svc.suggest_foley)
        self.plan_ok.clicked.connect(lambda: self.svc.approve() and self.svc.advance())
        return card

    def _p_production(self) -> QWidget:
        card, l = _card()
        l.addWidget(_h2(i18n.t("series.prod.title")))
        self.prod_bar = QProgressBar()
        self.prod_bar.setRange(0, 100)
        self.prod_list = QListWidget()
        self.prod_list.setMaximumHeight(200)
        row = QHBoxLayout()
        self.prod_btn = SquishyButton(i18n.t("series.prod.start"), variant="primary", accent="pink")
        self.prod_stop = QPushButton(i18n.t("series.prod.stop"))
        row.addWidget(self.prod_btn)
        row.addWidget(self.prod_stop)
        self.prod_note = _hint(i18n.t("series.prod.note"))
        for w in (self.prod_bar, self.prod_list, self.prod_note):
            l.addWidget(w)
        l.addLayout(row)
        self.prod_btn.clicked.connect(self.svc.produce)
        self.prod_stop.clicked.connect(self.svc.cancel_production)
        return card

    def _p_assembly(self) -> QWidget:
        card, l = _card()
        l.addWidget(_h2(i18n.t("series.assembly.title")))
        l.addWidget(_hint(i18n.t("series.assembly.sub")))
        self.asm_btn = SquishyButton(i18n.t("series.assembly.go"), variant="primary", accent="pink")
        self.asm_label = _hint()
        self.open_editor = QPushButton(i18n.t("series.assembly.open"))
        for w in (self.asm_btn, self.asm_label, self.open_editor):
            l.addWidget(w)
        self.asm_btn.clicked.connect(self._assemble)
        self.open_editor.clicked.connect(lambda: self.ctx.bus.emit(Topics.NAVIGATE, {"module": "editor"}))
        self.svc.assembled.connect(lambda s: self.asm_label.setText(i18n.t("series.assembly.done", scenes=s.get("scenes", 0), tracks=s.get("voice_tracks", 0))))
        return card

    # -- eylemler -------------------------------------------------------------------------------------------------
    def _brainstorm(self) -> None:
        self.clear_error()
        self.svc.set_idea(self.idea_edit.toPlainText(), self.title_edit.text())
        self.svc.brainstorm()

    def _show_ideas(self, ideas: list) -> None:
        self._ideas = ideas
        self.idea_list.clear()
        for i in ideas:
            self.idea_list.addItem(f"{i['title']} — {i['logline']}")

    def _choose_idea(self, item) -> None:
        i = self.idea_list.row(item)
        if 0 <= i < len(self._ideas):
            self.svc.choose_idea(self._ideas[i])
            self.title_edit.setText(self.svc.project.title)

    def _idea_next(self) -> None:
        self.clear_error()
        self.svc.set_idea(self.idea_edit.toPlainText() or self.svc.project.idea, self.title_edit.text())
        self.svc.advance()

    def _write_script(self) -> None:
        self.clear_error()
        self.svc.write_screenplay()

    def _save_script(self) -> None:
        if self.script_edit.toPlainText() != self.svc.project.screenplay:
            self.svc.set_screenplay(self.script_edit.toPlainText())

    def _format_next(self) -> None:
        self.svc.set_format(str(self.kind_box.currentData()), self.minutes.value(), self.episodes.value(), str(self.res_box.currentData()))
        self.svc.advance()

    def _add_char(self) -> None:
        if self.char_name.text().strip():
            self.svc.add_character(self.char_name.text(), self.char_desc.text(), self.char_voice.text())
            for w in (self.char_name, self.char_desc, self.char_voice):
                w.clear()

    def _approve_all(self) -> None:
        for sc in list(self.svc.project.scenes):
            self.svc.move_card(sc.id, "approved")

    def _dub(self) -> None:
        langs = [x.strip() for x in self.dub_edit.text().replace(";", ",").split(",") if x.strip()]
        if langs:
            self.clear_error()
            self.svc.prepare_dubbing(langs)

    def _assemble(self) -> None:
        self.clear_error()
        self.svc.assemble()

    def _on_job(self, job) -> None:
        self.prod_bar.setValue(int(job.progress * 100))
        self.refresh()

    # -- çizim ------------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        p = self.svc.project
        st = self.svc.stage
        self.title_label.setText(p.title or i18n.t("series.untitled"))
        self.stage_bar.set_stage(st, p.approvals)
        panel = self.panels.get(st, self.panels[Stage.ASSEMBLY])
        self.stack.setCurrentWidget(panel)
        done, total = p.progress()
        partial = bool(p.scenes) and 0 < done < total or bool(p.paused)
        self.resume_card.setVisible(partial and st is Stage.PRODUCTION)
        self.resume_text.setText(i18n.t("series.resume.title", done=done, total=total) if not p.paused else
                                 i18n.t("series.resume.paused", scene=p.paused.get("scene", ""), done=done, total=total))
        if self.title_edit.text() != p.title and not self.title_edit.hasFocus():
            self.title_edit.setText(p.title)
        if not self.idea_edit.hasFocus() and self.idea_edit.toPlainText() != p.idea:
            self.idea_edit.setPlainText(p.idea)
        if not self.script_edit.hasFocus() and self.script_edit.toPlainText() != p.screenplay:
            self.script_edit.setPlainText(p.screenplay)
        self.char_list.clear()
        for c in p.characters:
            self.char_list.addItem(f"{c.name} — {c.desc or '…'}  {'🔒' if c.locked else ''}")
        self.scene_list.clear()
        for sc in p.scenes:
            self.scene_list.addItem(f"{sc.id} · {sc.title} · {sc.seconds:.0f} sn")
        self.board.set_scenes(self.svc.columns())
        self.board_title.setText(i18n.t("series.board.title", episode=(p.scenes[0].episode if p.scenes else 1)))
        share = self.svc.plan_share()
        est = self.svc.estimate()
        self.plan_big.setText(self.share_text())
        self.plan_line.setText(i18n.t("series.plan.line", before=f"{share.before_pct:.0f}", project=f"{share.project_pct:.0f}", remaining=f"{max(0.0, share.remaining_pct):.0f}")
                               if share.budget else "")
        self.plan_detail.setText(i18n.t("series.plan.detail", video=f"{est.video_seconds:.0f}", images=est.images, tts=est.tts_chars,
                                        verdict=i18n.t(f"series.plan.{share.level}")))
        self.prod_list.clear()
        for sc in p.scenes:
            mark = {"done": "✓", "running": "…", "paused": "⏸", "failed": "✗", "pending": "○"}.get(sc.prod, "○")
            self.prod_list.addItem(f"{mark} {sc.id} {sc.title}" + (f"  ({sc.error})" if sc.error else ""))
        if total:
            self.prod_bar.setValue(int(done / total * 100))
        self.prod_btn.setText(i18n.t("series.prod.resume" if (done or p.paused) else "series.prod.start"))
        self.state_label.setText(i18n.t("series.state", done=done, total=total, stage=i18n.t(f"series.stage.{st.value}")) if total else "")
