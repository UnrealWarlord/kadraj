"""Legend proje merkezi: KPI kutuları ve maliyet analitiği gerçek proje verisine bağlı (sahte kayıtlar, ağ yok)."""
from __future__ import annotations

import socket
import time
from datetime import datetime

import pytest

from kadraj.ai_engine import analytics as A
from kadraj.modules.editor.service import JOB_RENDER
from kadraj.ui.shell.legend import ProjectHub

NOW = time.time()
MONTH = A.month_start(NOW)
LAST_MONTH = MONTH - 3600                 # önceki ayın içinde
LONG_AGO = NOW - 90 * 86400               # 30 gün penceresinin dışında ve önceki aylarda


def _usage(db, created, *, cost=0.0, tin=0, tout=0, provider="anthropic"):
    db._conn().execute(
        "INSERT INTO api_usage(provider, model, tokens_in, tokens_out, units, cost_usd, created)"
        " VALUES (?,?,?,?,?,?,?)", (provider, "model", tin, tout, 0.0, cost, created))


def _job(db, updated, *, status="done", kind=JOB_RENDER):
    job_id = db.add_job(kind, "queued", {})
    db._conn().execute("UPDATE jobs SET status = ?, updated = ? WHERE id = ?", (status, updated, job_id))
    return job_id


@pytest.fixture()
def project(ctx, tmp_path):
    root = ctx.create_project("Deneme", parent_dir=tmp_path)
    assert root is not None and ctx.project.is_open
    return ctx.project.db


def _kpi(hub, key):
    return hub.kpis[key].value.text()


def test_month_start_is_the_local_first_day_at_midnight():
    assert A.month_start(datetime(2026, 11, 25, 10, 30).timestamp()) == datetime(2026, 11, 1).timestamp()


def test_render_kind_is_the_editor_job_kind():
    assert A.RENDER_KIND == JOB_RENDER


def test_without_a_project_kpis_are_dashes_and_analytics_is_empty(ctx, qtbot):
    hub = ProjectHub(ctx)
    qtbot.addWidget(hub)
    assert [_kpi(hub, k) for k in ("render", "cost", "tokens")] == ["—", "—", "—"]
    assert hub.analytics.table.rowCount() == 0
    assert hub.analytics.chart.data is not None and not any(hub.analytics.chart.data.totals)


def test_disk_kpi_uses_the_configured_root_without_a_project(ctx, qtbot, tmp_path):
    ctx.settings.set("project_root", str(tmp_path))
    hub = ProjectHub(ctx)
    qtbot.addWidget(hub)
    assert "GB" in _kpi(hub, "disk")


def test_month_kpis_come_from_this_months_records_only(ctx, project, qtbot):
    _usage(project, NOW, cost=0.30, tin=1000, tout=200)
    _usage(project, NOW - 60, cost=0.12, tin=200, tout=100)
    _usage(project, LAST_MONTH, cost=9.0, tin=5000, tout=5000)      # önceki ay: sayılmaz
    _job(project, NOW)                                              # bu ay, tamamlandı
    _job(project, NOW - 1)                                          # bu ay, tamamlandı
    _job(project, LAST_MONTH)                                       # önceki ay: sayılmaz
    _job(project, NOW, status="queued")                             # tamamlanmadı: sayılmaz
    _job(project, NOW, kind="downloader.download")                  # render değil: sayılmaz
    hub = ProjectHub(ctx)
    qtbot.addWidget(hub)
    assert _kpi(hub, "render") == "2"
    assert _kpi(hub, "cost") == "$0.42"
    assert _kpi(hub, "tokens") == "1.5K"                            # (1000+200) + (200+100)
    assert hub.kpis["tokens"].sub.text() == "1,200 / 300"


def test_budget_percentage_is_shown_when_a_monthly_budget_is_set(ctx, project, qtbot):
    ctx.settings.set("plan.budget_usd", 2.0)
    _usage(project, NOW, cost=0.42)
    hub = ProjectHub(ctx)
    qtbot.addWidget(hub)
    assert hub.kpis["cost"].sub.text() == "21% / $2"


def test_no_budget_means_no_percentage(ctx, project, qtbot):
    _usage(project, NOW, cost=0.42)
    hub = ProjectHub(ctx)
    qtbot.addWidget(hub)
    assert _kpi(hub, "cost") == "$0.42" and hub.kpis["cost"].sub.text() == ""


def test_analytics_panel_shows_the_last_thirty_days(ctx, project, qtbot):
    _usage(project, NOW, cost=0.42, tin=10, tout=5)
    _usage(project, LONG_AGO, cost=5.0)                             # 30 günün dışında
    hub = ProjectHub(ctx)
    qtbot.addWidget(hub)
    assert hub.analytics.table.rowCount() == 1
    assert hub.analytics.table.item(0, 0).text() == "anthropic"
    assert hub.analytics.table.item(0, 3).text() == "$0.420"
    assert hub.analytics.chart.data.totals[-1] == pytest.approx(0.42)


def test_empty_project_shows_zeros_and_a_calm_chart(ctx, project, qtbot):
    hub = ProjectHub(ctx)
    qtbot.addWidget(hub)
    assert [_kpi(hub, k) for k in ("render", "cost", "tokens")] == ["0", "$0.00", "0"]
    assert hub.analytics.table.rowCount() == 0
    assert hub.analytics.chart.data is not None and not any(hub.analytics.chart.data.totals)


def test_refresh_picks_up_new_records(ctx, project, qtbot):
    hub = ProjectHub(ctx)
    qtbot.addWidget(hub)
    assert _kpi(hub, "cost") == "$0.00"
    _usage(project, NOW, cost=0.25)
    _job(project, NOW)
    hub.refresh()
    assert _kpi(hub, "cost") == "$0.25" and _kpi(hub, "render") == "1"


def test_analytics_makes_no_network_calls(ctx, project, qtbot, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("ağ çağrısı yapılmamalı")

    monkeypatch.setattr(socket, "create_connection", refuse)
    _usage(project, NOW, cost=0.1)
    hub = ProjectHub(ctx)
    qtbot.addWidget(hub)
    assert _kpi(hub, "cost") == "$0.10"
