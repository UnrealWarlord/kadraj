"""Uçtan uca: bağlam kablolaması, kasa ↔ sağlayıcı ↔ sysmon, kabuk (geri dönüş düğmesi), kapanış."""
from __future__ import annotations

import json
import socket

import httpx
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from kadraj.ai_engine.providers.base import default_registry
from kadraj.core.contracts import (AutosaveAPI, ContextAPI, JobControlAPI, JobQueueAPI, ProviderKind,
                                   ProviderRegistryAPI, SnapshotAPI, SysmonAPI, Topics, VaultAdminAPI, VaultAPI)
from kadraj.core.jobs import JobQueue
from kadraj.core.sysmon import SystemMonitor
from kadraj.storage.autosave import AutosaveService
from kadraj.storage.snapshots import SnapshotStore
from kadraj.storage.vault import Vault
from kadraj.ui.shell.main_window import MainWindow


def test_context_uses_the_real_implementations(ctx):
    assert isinstance(ctx.vault, Vault) and ctx.vault.is_placeholder is False
    assert isinstance(ctx.jobs, JobQueue) and isinstance(ctx.sysmon, SystemMonitor)
    assert isinstance(ctx.autosave, AutosaveService) and isinstance(ctx.snapshots, SnapshotStore)
    for obj, proto in ((ctx.vault, VaultAPI), (ctx.vault, VaultAdminAPI), (ctx.jobs, JobQueueAPI),
                       (ctx.jobs, JobControlAPI), (ctx.providers, ProviderRegistryAPI), (ctx.autosave, AutosaveAPI),
                       (ctx.snapshots, SnapshotAPI), (ctx.sysmon, SysmonAPI), (ctx, ContextAPI)):
        assert isinstance(obj, proto), proto


def test_startup_touches_neither_the_keyring_nor_the_vault_file(ctx):
    from kadraj import paths
    assert not paths.vault_file().exists()
    assert ctx.vault.status().state == "empty"
    assert not paths.vault_file().exists()                                  # okuma dosya oluşturmaz


def test_vault_provider_sysmon_chain(ctx):
    seen = {}

    def h(r):
        seen["key"] = r.headers.get("x-api-key")
        return httpx.Response(200)
    ctx.providers = default_registry(ctx.settings, ctx.jobs, transport=httpx.MockTransport(h))
    ctx.providers.on_ping = lambda _p, ms: ctx.sysmon.record_ping(ms)
    assert ctx.providers.test_connection("anthropic", ctx.vault).error.code == "provider.no_key"
    ctx.vault.set_secret("anthropic", "sk-ant-LIVE-1234567890")
    res = ctx.providers.test_connection("anthropic", ctx.vault)
    assert res.ok and seen["key"] == "sk-ant-LIVE-1234567890"
    assert ctx.sysmon.sample()["api_ping"] == res.latency_ms
    view = ctx.vault.view("is-profili")                                      # profil ad alanı: ayrı anahtar kümesi
    assert [p.id for p in ctx.providers.configured(view) if p.needs_key] == []        # profilde anahtar yok


def test_default_ctx_registry_pings_offline_without_crashing(ctx, no_network):
    ctx.vault.set_secret("anthropic", "sk-ant-LIVE-1234567890")
    res = ctx.providers.test_connection("anthropic", ctx.vault, timeout=1.0)
    assert not res.ok and res.error is not None and res.error.retryable
    assert "sk-ant-LIVE" not in res.error.technical
    local = ctx.providers.test_connection("ollama", ctx.vault, timeout=1.0)
    assert not local.ok and local.error.code == "provider.local_down" and no_network


def test_fallback_queues_a_deferred_job_through_the_real_queue(ctx, tmp_path):
    ctx.create_project("Kuyruk", tmp_path / "p")
    ctx.vault.set_secret("anthropic", "sk-ant-LIVE-1234567890")
    ctx.providers = default_registry(ctx.settings, ctx.jobs, transport=httpx.MockTransport(
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("offline", request=r))))
    res = ctx.providers.call_with_fallback(
        ProviderKind.LLM,
        lambda p: p.test_connection(ctx.vault) and (_ for _ in ()).throw(ConnectionError("offline")),
        vault=ctx.vault, queue_payload={"prompt": "merhaba"}, queue_title="Sonra")
    assert not res.ok and res.queued_job is not None and res.queued_job.kind == "ai.deferred"
    assert ctx.project.db.list_jobs()[0]["payload"]["payload"] == {"prompt": "merhaba"}


# ---- kabuk ---------------------------------------------------------------------------------------------------------------
@pytest.fixture()
def win(ctx, qtbot, tmp_path):
    ctx.create_project("Kabuk3", tmp_path / "p")
    w = MainWindow(ctx, ctx.theme)
    qtbot.addWidget(w)
    w.resize(1320, 860)
    w.show()
    return w


def drive_history(ctx):
    """Saati kaydırarak 14 dakikalık geçmiş üretir."""
    clock = type("C", (), {"t": 1_800_000_000.0})()
    ctx.autosave._clock = lambda: clock.t
    ctx.autosave.stop()
    svc = AutosaveService(ctx, clock=lambda: clock.t)
    ctx.autosave = svc
    ctx.project.close()
    return clock, svc


def test_rewind_button_rolls_the_project_back_and_the_menu_undoes_it(win, ctx, qtbot):
    root = ctx.project.root
    clock, svc = drive_history(ctx)
    ctx.open_project(root)
    for name in ["A", "B", "C", "D", "E", "F", "G"]:
        clock.t += 120
        ctx.project.data.name = name
        ctx.project.mark_dirty()
        svc.tick()
    win.topbar.rewind_btn.click()
    qtbot.waitUntil(lambda: ctx.project.data.name == "B", timeout=2000)
    qtbot.waitUntil(lambda: "10" in win.status.message() and "döndürüldü" in win.status.message(), timeout=2000)
    assert json.loads(ctx.project.file_path.read_text(encoding="utf-8"))["name"] == "B"
    win.actions_by_key["undo_rewind"].trigger()
    assert ctx.project.data.name == "G" and "geri alındı" in win.status.message()
    win.actions_by_key["undo_rewind"].trigger()
    assert "geri alınacak" in win.status.message().lower()


def test_autosave_indicator_follows_the_service(win, ctx, qtbot):
    ctx.project.data.name = "gösterge"
    ctx.project.mark_dirty()
    ctx.autosave.tick()
    qtbot.waitUntil(lambda: win.topbar.autosave.state == "saved", timeout=1000)
    assert not ctx.project.dirty and "kaydedildi" not in win.status.message().lower()   # sessiz kayıt


def test_system_panel_shows_gpu_vram_and_ping(win, ctx):
    class Fake:
        def sample(self, path=None):
            return {"cpu": 12.0, "ram_used": 4e9, "ram_total": 16e9, "gpu": 61.0, "vram_used": 7.8e9,
                    "vram_total": 12e9, "gpu_name": "X", "disk_free": 4e11, "disk_total": 1e12, "api_ping": 182.0}

        def record_ping(self, ms): pass
    ctx.sysmon = Fake()
    win.system_panel.sample_now()
    vals = {k: v.text() for k, v in win.system_panel._vals.items()}
    assert vals["gpu"] == "61%" and vals["vram"] == "7.8/12 GB" and vals["ping"] == "182 ms" and vals["cpu"] == "12%"


def test_jobs_panel_and_status_follow_real_jobs(win, ctx, qtbot):
    ctx.set_ui_mode("legend")
    ctx.jobs.register_handler("t", lambda j, jc: jc.report(0.5))
    job = ctx.jobs.submit("t", title="Panel işi")
    qtbot.waitUntil(lambda: job.finished and job.id in win.jobs_panel._rows, timeout=3000)
    qtbot.waitUntil(lambda: "bitti" in win.jobs_panel._rows[job.id][2].text(), timeout=2000)


def test_shutdown_saves_pending_changes_and_pauses_jobs(ctx, tmp_path, qtbot):
    import threading
    import time
    ctx.create_project("Kapanış", tmp_path / "p")
    path = ctx.project.file_path
    ctx.project.data.name = "son değişiklik"
    ctx.project.mark_dirty()
    started = threading.Event()

    def long(job, jc):
        started.set()
        while not jc.is_cancelled():
            time.sleep(0.01)
    ctx.jobs.register_handler("long", long)
    job = ctx.jobs.submit("long")
    assert started.wait(3)
    db_path = ctx.project.root / ".kadraj" / "proje.db"
    ctx.shutdown()
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "son değişiklik"
    import sqlite3
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT status FROM jobs WHERE id=?", (job.id,)).fetchone()[0] == "paused"
    con.close()


def test_whole_workflow_runs_with_the_network_blocked(win, ctx, no_network, qtbot):
    ctx.vault.set_secret("openai_compat", "sk-test-0000-1111-2222")
    for pid in ("anthropic", "openai_compat", "ollama", "comfyui", "pexels"):
        ctx.providers.test_connection(pid, ctx.vault, timeout=0.5)
    ctx.snapshots.create("ağsız")
    win.request_rewind(10)
    ctx.set_ui_mode("legend")
    ctx.set_ui_mode("simple")
    assert ctx.project.is_open
