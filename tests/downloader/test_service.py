"""Servis: çözümleme (arka plan), hata çevirisi, işleyici, iptal, kuyruk entegrasyonu, taslak."""
from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from kadraj.core.contracts import SmartError
from kadraj.modules.downloader import service as S

from .fakes import PLAYLIST_INFO, SAMPLE_INFO, FakeJC, FakeJob, FakeYDL


def _collect(signal):
    got = []
    signal.connect(got.append)
    return got


# -- çözümleme ---------------------------------------------------------------
def test_probe_success_runs_in_background_and_emits(svc, qtbot):
    svc.draft = S.Draft(url="https://www.youtube.com/watch?v=abc123")
    with qtbot.waitSignal(svc.probed, timeout=5000) as blocker:
        svc.probe()
    info = blocker.args[0]
    assert info.title == "Örnek Klip" and info.duration == 225
    assert svc.info is info


def test_probe_offline_becomes_smart_error_not_crash(svc, qtbot):
    FakeYDL.scenario["error"] = urllib.error.URLError(OSError("ağ yok"))
    with qtbot.waitSignal(svc.probeFailed, timeout=5000) as blocker:
        svc.probe("https://www.youtube.com/watch?v=abc123")
    err = blocker.args[0]
    assert isinstance(err, SmartError) and err.code == "net.offline"
    assert err.title and err.summary


def test_probe_ytdlp_error_is_translated(svc, qtbot):
    FakeYDL.scenario["error"] = DownloadError("ERROR: [youtube] abc: Video unavailable")
    with qtbot.waitSignal(svc.probeFailed, timeout=5000) as blocker:
        svc.probe("https://www.youtube.com/watch?v=abc123")
    assert blocker.args[0].code == "ytdlp.unavailable"


def test_invalid_url_fails_synchronously(svc):
    errors = _collect(svc.probeFailed)
    svc.probe("merhaba dünya")
    assert len(errors) == 1 and errors[0].code == "input.url_invalid"


def test_stale_probe_result_is_ignored(svc):
    got = _collect(svc.probed)
    url = "https://www.youtube.com/watch?v=abc123"
    stale = svc._token
    svc.set_url(url)                      # kullanıcı bağlantıyı değiştirdi → token ilerledi
    svc._probe_worker(url, False, stale)  # eski sonuç gelirse yok sayılmalı
    assert got == [] and svc.info is None


def test_playlist_probe_lists_entries(svc):
    FakeYDL.scenario["info"] = dict(PLAYLIST_INFO)
    info = svc.fetch_info("https://www.youtube.com/playlist?list=x", playlist=True)
    assert info.is_playlist and [e.url for e in info.entries] == [
        "https://www.youtube.com/watch?v=a1", "https://www.youtube.com/watch?v=b2"]


def test_probe_options_skip_download_and_flat_playlists(svc):
    svc.fetch_info("https://www.youtube.com/watch?v=abc123")
    opts = FakeYDL.opts_seen[-1]
    assert opts["skip_download"] is True and opts["noplaylist"] is True
    assert opts["extract_flat"] == "in_playlist" and opts["socket_timeout"] == S.SOCKET_TIMEOUT


# -- işleyici -----------------------------------------------------------------
def test_handler_downloads_reports_and_returns_path(svc, tmp_path):
    req = S.DownloadRequest(url="https://www.youtube.com/watch?v=abc123", output_dir=str(tmp_path))
    jc = FakeJC()
    result = svc._handle_download(FakeJob(req.to_payload()), jc)
    assert result["path"].endswith("[abc123].mp4") and Path(result["path"]).exists()
    assert result["files"] == [result["path"]]
    progress = [p for p, _m in jc.reports]
    assert 0.0 < progress[0] <= 0.95 and progress[-1] == 1.0


def test_handler_cancellation_returns_quietly(svc, tmp_path):
    req = S.DownloadRequest(url="https://www.youtube.com/watch?v=abc123", output_dir=str(tmp_path))
    result = svc._handle_download(FakeJob(req.to_payload()), FakeJC(cancelled=True))
    assert result == {}
    assert not list(tmp_path.glob("*.mp4"))


def test_handler_error_becomes_smart_error(svc, tmp_path):
    def _fail(_url, _opts):
        raise DownloadError("ERROR: [youtube] abc: Sign in to confirm your age")

    FakeYDL.scenario["download"] = _fail
    req = S.DownloadRequest(url="https://www.youtube.com/watch?v=abc123", output_dir=str(tmp_path))
    with pytest.raises(SmartError) as exc:
        svc._handle_download(FakeJob(req.to_payload()), FakeJC())
    assert exc.value.code == "ytdlp.age_restricted"


def test_handler_refuses_when_disk_is_critical(svc, tmp_path, monkeypatch):
    critical = SmartError("disk.critical", "Diskte çok az yer kaldı", "x")
    monkeypatch.setattr(S.disk, "warning_for", lambda _p: critical)
    req = S.DownloadRequest(url="https://www.youtube.com/watch?v=abc123", output_dir=str(tmp_path))
    with pytest.raises(SmartError) as exc:
        svc._handle_download(FakeJob(req.to_payload()), FakeJC())
    assert exc.value.code == "disk.critical"
    assert FakeYDL.opts_seen == []


def test_handler_passes_range_and_options_to_ytdlp(svc, tmp_path):
    req = S.DownloadRequest(url="https://www.youtube.com/watch?v=abc123", start=80.0, end=225.0,
                            output_dir=str(tmp_path), options=S.Options(subtitles=True))
    svc._handle_download(FakeJob(req.to_payload()), FakeJC())
    opts = FakeYDL.opts_seen[-1]
    assert callable(opts["download_ranges"]) and opts["writesubtitles"] is True


# -- kuyruk entegrasyonu (gerçek JobQueue, sahte yt-dlp) ----------------------------
def test_submit_runs_through_job_queue(svc, ctx, tmp_path):
    req = S.DownloadRequest(url="https://www.youtube.com/watch?v=abc123", output_dir=str(tmp_path))
    job = svc.submit(req, title="Örnek Klip")
    assert ctx.jobs.wait_idle(10000)
    done = ctx.jobs.get(job.id)
    assert done.status.value == "done"
    assert Path(done.payload["_result"]["path"]).exists()
    rows = svc.recent(limit=5)
    assert rows and rows[0]["path"] == done.payload["_result"]["path"]
    assert rows[0]["status"].value == "done"


def test_playlist_submits_one_job_per_entry(svc, ctx, tmp_path):
    FakeYDL.scenario["info"] = dict(PLAYLIST_INFO)
    info = svc.fetch_info("https://www.youtube.com/playlist?list=x", playlist=True)
    req = S.DownloadRequest(url=info.url, output_dir=str(tmp_path), options=S.Options(playlist=True))
    jobs = svc.submit_playlist(info.entries, req)
    assert len(jobs) == 2
    assert ctx.jobs.wait_idle(10000)
    assert all(ctx.jobs.get(j.id).status.value == "done" for j in jobs)


def test_cancel_queued_job_is_safe(svc, ctx, tmp_path):
    req = S.DownloadRequest(url="https://www.youtube.com/watch?v=abc123", output_dir=str(tmp_path))
    job = svc.submit(req, title="iptal")
    svc.cancel(job.id)                    # bitmiş ya da çalışıyor olabilir; çökmemeli
    assert ctx.jobs.wait_idle(10000)


# -- taslak ve çıktı klasörü --------------------------------------------------------
def test_draft_survives_page_rebuilds_and_builds_requests(svc):
    svc.set_url("https://www.youtube.com/watch?v=abc123")
    svc.set_mode(S.MODE_AUDIO)
    svc.set_quality(S.QUALITY_BEST)
    svc.set_format_ids(["140"])
    svc.set_partial(True)
    svc.set_range(80.0, 225.0)
    svc.set_options(S.Options(playlist=False, rate_limit="5M"))
    req = svc.build_request(duration=300.0, with_output=False)     # 225 < 300: bitiş "sona kadar" olmaz
    assert (req.mode, req.quality, req.format_ids) == ("audio", "best", ("140",))
    assert (req.start, req.end) == (80.0, 225.0)
    assert req.options.rate_limit == "5M" and req.output_dir == ""


def test_build_request_drops_end_at_clip_end_and_rejects_bad_range(svc):
    svc.set_url("https://www.youtube.com/watch?v=abc123")
    svc.set_partial(True)
    svc.set_range(0.0, 224.8)                       # sona çok yakın → sona kadar
    assert svc.build_request(duration=225.0, with_output=False).end is None
    svc.set_range(100.0, 101.0)                     # 1 saniyeden kısa → geçersiz
    with pytest.raises(ValueError):
        svc.build_request(duration=225.0, with_output=False)


def test_default_output_dir_follows_settings_without_project(svc, ctx, tmp_path):
    ctx.settings.set("project_root", str(tmp_path))
    assert svc.default_output_dir() == tmp_path / "01_Downloads"


def test_request_payload_round_trips_and_is_json_safe(svc):
    import json
    req = S.DownloadRequest(url="u", mode=S.MODE_VIDEO, quality=S.QUALITY_FAST, format_ids=("137",),
                            start=1.5, end=9.0, options=S.Options(subtitles=True, sub_langs=("tr", "en")),
                            output_dir="/tmp/x")
    payload = req.to_payload()
    json.dumps(payload)
    assert S.DownloadRequest.from_payload(payload) == req
