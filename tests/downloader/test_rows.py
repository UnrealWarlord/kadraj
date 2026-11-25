"""İndirici arayüzü: başarısız iş → hata kartı (başlık, neden, numaralı adımlar, düğme); biten iş → "Oynat",
"Klasörde Göster", "Düzenlemeye gönder"; bu videoda olmayan kalite pasif ve "Bu videoda yok (en yüksek: 1080p)".

Motor ve servis gerçek değil: iş kaydı ve düzenleyici sahte nesnelerle taklit edilir.
"""
from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QAbstractButton, QLabel

from kadraj.core import errors, i18n
from kadraj.core.contracts import JobStatus, SmartAction, SmartError
from kadraj.modules.downloader.page_legend import DownloaderLegendPage
from kadraj.modules.downloader.page_simple import DownloaderSimplePage
from kadraj.modules.downloader.rows import RecentDownloadRow, failed_rows, quality_top
from kadraj.modules.downloader.service import QUALITY_BALANCED, QUALITY_BEST, FormatRow, VideoInfo
from kadraj.modules.downloader.strings import K
from kadraj.ui.widgets import SmartErrorCard

URL = "https://www.youtube.com/watch?v=abc123"
UNAVAILABLE_1080 = "Bu videoda yok (en yüksek: 1080p)"


class FakeJobs:
    def __init__(self, jobs=None):
        self.jobs = dict(jobs or {})
        self.retried = []

    def get(self, job_id):
        return self.jobs.get(job_id)

    def retry(self, job_id):
        self.retried.append(job_id)
        return True


class FakeBus:
    def __init__(self):
        self.sent = []

    def emit(self, topic, payload=None):
        self.sent.append((topic, payload))


class FakeEditor:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def add_media(self, path):
        self.calls.append(path)
        return object() if self.ok else None


class FakeCtx:
    def __init__(self, jobs=None, editor=None):
        self.jobs = FakeJobs(jobs)
        self.bus = FakeBus()
        self._editor = editor

    def service(self, name):
        return self._editor if name == "editor" else None


def _row(status, *, job_id=7, path="", error="", title="Örnek video"):
    return {"id": job_id, "title": title, "status": status, "progress": 0.0, "path": path, "error": error}


def _info(max_height=1080):
    return VideoInfo(url=URL, title="Örnek", extractor="youtube", duration=225.0, max_height=max_height,
                     subtitles=(), is_playlist=False, entries=(), formats=())


ENGINE_ERROR = SmartError(
    "downloader.js", "Video çözücü bileşen eksik", "Bu videonun kaliteleri okunamadı.",
    cause="YouTube için küçük bir yardımcı program gerekiyor.",
    steps=("Bileşenleri güncelle.", "Bağlantıyı yeniden yapıştır.", "Ayarlar → Araçlar'a bak."),
    actions=(SmartAction("retry", i18n.t("err.action.retry"), "retry"),),
    source="downloader",
)


def _buttons(widget):
    return [b for b in widget.findChildren(QAbstractButton)]


def _texts(widget):
    return [label.text() for label in widget.findChildren(QLabel)]


# ----------------------------------------------------------------------------------------- başarısız iş
def test_failed_download_shows_title_cause_numbered_steps_and_retry(ctx, qtbot):
    fake = FakeCtx(jobs={7: SimpleNamespace(payload={"_error": ENGINE_ERROR.to_dict()})})
    row = RecentDownloadRow(fake, _row(JobStatus.FAILED))
    qtbot.addWidget(row)
    card = row.error_card
    assert isinstance(card, SmartErrorCard)
    assert card.error.title == "Video çözücü bileşen eksik"
    texts = _texts(card)
    assert "Bu videonun kaliteleri okunamadı." in texts
    assert "YouTube için küçük bir yardımcı program gerekiyor." in " ".join(texts)
    assert "1. Bileşenleri güncelle." in texts
    assert "2. Bağlantıyı yeniden yapıştır." in texts
    assert "3. Ayarlar → Araçlar'a bak." in texts
    retry = [b for b in _buttons(card) if b.text() == i18n.t("err.action.retry")]
    assert retry and not retry[0].isHidden()
    retry[0].click()
    assert fake.jobs.retried == [7]


def test_failure_card_stays_open_and_hides_only_the_dismiss_button(ctx, qtbot):
    fake = FakeCtx(jobs={7: SimpleNamespace(payload={"_error": ENGINE_ERROR.to_dict()})})
    row = RecentDownloadRow(fake, _row(JobStatus.FAILED))
    qtbot.addWidget(row)
    dismiss = [b for b in _buttons(row.error_card) if b.text() == i18n.t("errcard.dismiss")]
    assert dismiss and all(b.isHidden() for b in dismiss)
    assert not row.error_card.isHidden()


def test_plain_text_failure_gets_generic_steps(ctx, qtbot):
    row = RecentDownloadRow(FakeCtx(), _row(JobStatus.FAILED, error="Ağ bağlantısı koptu"))
    qtbot.addWidget(row)
    err = row.error_card.error
    assert err.summary == "Ağ bağlantısı koptu"
    assert err.steps == (i18n.t(K.FAILED_STEP1), i18n.t(K.FAILED_STEP2), i18n.t(K.FAILED_STEP3))
    assert err.title == i18n.t(K.FAILED_TITLE)


def test_failure_without_any_message_still_explains_itself(ctx, qtbot):
    row = RecentDownloadRow(FakeCtx(), _row(JobStatus.FAILED, error=""))
    qtbot.addWidget(row)
    assert row.error_card.error.summary == i18n.t(K.UNKNOWN_ERROR)
    assert len(row.error_card.error.steps) == 3


# ----------------------------------------------------------------------------------------- biten iş
def test_finished_download_offers_play_show_and_send_to_editor(ctx, qtbot, tmp_path):
    clip = tmp_path / "Klip.mp4"
    clip.write_bytes(b"x")
    row = RecentDownloadRow(FakeCtx(editor=FakeEditor()), _row(JobStatus.DONE, path=str(clip)))
    qtbot.addWidget(row)
    texts = [b.text() for b in _buttons(row)]
    assert "Oynat" in texts and "Klasörde Göster" in texts and "Düzenlemeye gönder" in texts
    assert row.error_card is None


def test_send_to_editor_adds_the_file_through_the_editor_service(ctx, qtbot, tmp_path):
    clip = tmp_path / "Klip.mp4"
    clip.write_bytes(b"x")
    editor = FakeEditor()
    row = RecentDownloadRow(FakeCtx(editor=editor), _row(JobStatus.DONE, path=str(clip)))
    qtbot.addWidget(row)
    next(b for b in _buttons(row) if b.text() == "Düzenlemeye gönder").click()
    assert editor.calls == [str(clip)]
    assert row.note.text() == "Düzenleyiciye eklendi: Klip.mp4"


def test_send_to_editor_says_so_when_the_editor_is_missing(ctx, qtbot, tmp_path):
    clip = tmp_path / "Klip.mp4"
    clip.write_bytes(b"x")
    row = RecentDownloadRow(FakeCtx(editor=None), _row(JobStatus.DONE, path=str(clip)))
    qtbot.addWidget(row)
    next(b for b in _buttons(row) if b.text() == "Düzenlemeye gönder").click()
    assert row.note.text() == i18n.t(K.EDITOR_UNAVAILABLE)


def test_send_to_editor_failure_is_explained(ctx, qtbot, tmp_path):
    clip = tmp_path / "Klip.mp4"
    clip.write_bytes(b"x")
    row = RecentDownloadRow(FakeCtx(editor=FakeEditor(ok=False)), _row(JobStatus.DONE, path=str(clip)))
    qtbot.addWidget(row)
    next(b for b in _buttons(row) if b.text() == "Düzenlemeye gönder").click()
    assert row.note.text() == i18n.t(K.EDITOR_FAILED)


# ----------------------------------------------------------------------------------------- kalite
def test_simple_page_disables_a_quality_the_video_does_not_have(ctx, svc, qtbot):
    page = DownloaderSimplePage(ctx)
    qtbot.addWidget(page)
    svc.set_quality(QUALITY_BEST)
    page._on_probed(_info(1080))
    best = page.quality_cards[2]
    assert not best.isEnabled()
    assert best.accessibleDescription() == UNAVAILABLE_1080
    assert page.quality_cards[1].isEnabled()
    assert svc.draft.quality == QUALITY_BALANCED                # seçili kalite yoksa en yükseği seçer


def test_legend_page_disables_the_unavailable_quality_item(ctx, svc, qtbot):
    page = DownloaderLegendPage(ctx)
    qtbot.addWidget(page)
    page._on_probed(_info(1080))
    model = page.quality.model()
    best = page.quality.findData(QUALITY_BEST)
    assert model.item(best).isEnabled() is False
    assert UNAVAILABLE_1080 in page.quality.itemText(best)
    assert model.item(page.quality.findData(QUALITY_BALANCED)).isEnabled()


def test_quality_top_uses_the_service_rule_and_reports_the_highest_height():
    assert quality_top(_info(1080), QUALITY_BEST) == 1080          # biçim listesi yok: en yüksek = max_height
    assert quality_top(_info(1080), QUALITY_BALANCED) is None
    assert quality_top(None, QUALITY_BEST) is None
    with_formats = VideoInfo(url=URL, title="x", extractor="youtube", duration=60.0, max_height=720,
                             subtitles=(), is_playlist=False, entries=(),
                             formats=(FormatRow("22", "mp4", "1280x720", 720, 30.0, "avc1", "mp4a", None, 0.0, 0.0,
                                                ""),))
    assert quality_top(with_formats, QUALITY_BEST) == 720
    assert quality_top(with_formats, QUALITY_BALANCED) == 720


def test_quality_top_is_none_for_playlists():
    playlist = VideoInfo(url=URL, title="liste", extractor="youtube", duration=0.0, max_height=0,
                         subtitles=(), is_playlist=True, entries=(), formats=())
    assert quality_top(playlist, QUALITY_BEST) is None


def test_failed_rows_helper_keeps_only_failures_and_caps_the_count():
    rows = [_row(JobStatus.DONE), _row(JobStatus.FAILED, job_id=1), _row(JobStatus.FAILED, job_id=2),
            _row(JobStatus.FAILED, job_id=3), _row(JobStatus.FAILED, job_id=4)]
    got = failed_rows(rows)
    assert [r["id"] for r in got] == [1, 2, 3]


# ----------------------------------------------------------------------------------------- sözlük yedeği
def test_unknown_downloader_error_gets_a_card_with_steps_not_just_a_status(ctx):
    err = errors.translate(RuntimeError("beklenmeyen"), {"source": "downloader", "stderr": "odd"})
    assert err.code == "downloader.generic"
    assert err.title == i18n.t("err.cat.downloader.generic.title")
    assert err.steps and err.actions


def test_network_error_from_the_downloader_still_reaches_the_offline_card(ctx):
    err = errors.translate(ConnectionError("bağlantı yok"), {"source": "downloader"})
    assert err.code == "net.offline"
