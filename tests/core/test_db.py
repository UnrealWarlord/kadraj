"""SQLite proje veritabanı: şema (plan §3), CRUD, thread güvenliği, sürüm koruması."""
from __future__ import annotations

import sqlite3
import threading

import pytest

from kadraj.storage import db as dbmod
from kadraj.storage.db import ProjectDB


@pytest.fixture()
def pdb(tmp_path):
    d = ProjectDB(tmp_path / ".kadraj" / "proje.db")
    yield d
    d.close()


def test_schema_has_all_plan_tables_and_columns(pdb):
    assert set(pdb.tables()) >= {"media", "jobs", "snapshots", "prompts", "api_usage", "series_state"}
    conn = sqlite3.connect(pdb.path)
    cols = lambda t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
    assert cols("media") == ["id", "path", "kind", "duration", "width", "height", "fps", "codec",
                             "sample_rate", "tags", "created"]
    assert cols("jobs") == ["id", "kind", "status", "progress", "payload_json", "error", "created", "updated"]
    assert cols("snapshots") == ["id", "parent_id", "branch", "label", "path", "created", "auto"]
    assert cols("prompts") == ["id", "model", "idea", "prompt", "negative", "params_json", "favorite", "created"]
    assert cols("api_usage") == ["id", "provider", "model", "tokens_in", "tokens_out", "units", "cost_usd", "created"]
    assert cols("series_state") == ["id", "stage", "scene_index", "checkpoint_json", "updated"]
    conn.close()


def test_version_and_reopen_is_idempotent(tmp_path):
    p = tmp_path / "a" / "x.db"
    a = ProjectDB(p)
    assert a.version == dbmod.SCHEMA_VERSION
    a.add_media("/m/a.wav", "audio", duration=1.5, sample_rate=48000)
    a.close()
    b = ProjectDB(p)
    assert len(b.list_media()) == 1
    b.close()


def test_newer_schema_is_refused(tmp_path):
    p = tmp_path / "n.db"
    conn = sqlite3.connect(p)
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="daha yeni"):
        ProjectDB(p)


def test_media_upsert_and_filters(pdb):
    i = pdb.add_media("/m/a.mp4", "video", width=1920, height=1080, fps=30.0, codec="h264", tags="b-roll")
    j = pdb.add_media("/m/a.mp4", "video", width=3840, height=2160)           # aynı yol → güncelle
    assert i == j and pdb.get_media(i)["width"] == 3840
    pdb.add_media("/m/s.wav", "audio", sample_rate=96000)
    assert [m["kind"] for m in pdb.list_media("audio")] == ["audio"]
    pdb.remove_media(i)
    assert pdb.get_media(i) is None


def test_jobs_lifecycle(pdb):
    jid = pdb.add_job("download", payload={"url": "https://x/y", "ğ": "ü"})
    pdb.update_job(jid, status="running", progress=0.4)
    pdb.update_job(jid, progress=7)                                           # sınırlandırılır
    job = pdb.get_job(jid)
    assert job["status"] == "running" and job["progress"] == 1.0 and job["payload"]["ğ"] == "ü"
    pdb.update_job(jid, status="failed", error="Ağ yok")
    assert [j["id"] for j in pdb.list_jobs("failed")] == [jid] and pdb.list_jobs("running") == []


def test_snapshots_tree(pdb):
    root = pdb.add_snapshot("snapshots/0", label="ilk", auto=True)
    child = pdb.add_snapshot("snapshots/1", branch="alt", parent_id=root)
    snaps = pdb.list_snapshots()
    assert snaps[0]["auto"] is True and snaps[1]["parent_id"] == root and snaps[1]["branch"] == "alt"
    pdb.remove_snapshot(root)                                                 # ebeveyn silinince çocuk kalır
    assert pdb.list_snapshots("alt")[0]["parent_id"] is None


def test_prompts_and_usage(pdb):
    pid = pdb.add_prompt(model="flux", idea="kedi", prompt="a cat", params={"steps": 20})
    pdb.set_prompt_favorite(pid, True)
    assert pdb.list_prompts(favorites_only=True)[0]["params"] == {"steps": 20}
    pdb.add_usage("anthropic", model="x", tokens_in=100, tokens_out=50, cost_usd=0.25)
    pdb.add_usage("openai", tokens_in=10, units=3, cost_usd=0.05)
    s = pdb.usage_summary()
    assert s["tokens_in"] == 110 and s["tokens_out"] == 50 and round(s["cost_usd"], 2) == 0.30 and s["calls"] == 2
    assert pdb.usage_summary(since=9e12)["calls"] == 0


def test_series_state_checkpoint_resume(pdb):
    pdb.save_series_state("storyboard", 3, {"approved": [1, 2]})
    pdb.save_series_state("storyboard", 4, {"approved": [1, 2, 3]})           # aynı aşama güncellenir
    st = pdb.load_series_state("storyboard")
    assert st["scene_index"] == 4 and st["checkpoint"]["approved"] == [1, 2, 3]
    assert pdb.load_series_state()["stage"] == "storyboard"


def test_usable_from_other_threads(pdb):
    errors = []

    def work(n):
        try:
            for _ in range(20):
                pdb.add_job(f"k{n}")
        except Exception as exc:
            errors.append(exc)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors and len(pdb.list_jobs()) == 80


def test_closed_db_raises(tmp_path):
    d = ProjectDB(tmp_path / "c.db")
    d.close()
    with pytest.raises(sqlite3.ProgrammingError):
        d.list_media()
