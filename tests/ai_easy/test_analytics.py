"""AI kullanım analitiği: günlük maliyet, sağlayıcı/model tabloları, bütçe, token toplamları; db okuma."""
from __future__ import annotations

from datetime import datetime

from kadraj.ai_engine import analytics as A
from kadraj.storage.db import ProjectDB

NOW = datetime(2026, 11, 20, 12, 0).timestamp()
DAY = 86400.0


def _row(provider, model="", tin=0, tout=0, units=0.0, cost=0.0, created=NOW):
    return {"provider": provider, "model": model, "tokens_in": tin, "tokens_out": tout, "units": units,
            "cost_usd": cost, "created": created}


ROWS = [
    _row("anthropic", "claude-5", 1000, 400, cost=0.30, created=NOW),
    _row("anthropic", "claude-5", 500, 100, cost=0.10, created=NOW - 60),
    _row("pexels", "", units=1, cost=0.0, created=NOW - DAY),
    _row("elevenlabs", "v3", units=2000, cost=0.60, created=NOW - 3 * DAY),
    _row("anthropic", "claude-5", 10, 10, cost=0.02, created=NOW - 40 * DAY),   # 30 günün dışında
]


def test_daily_costs_fill_empty_days_and_split_by_provider():
    days = A.daily_costs(ROWS, days=4, now=NOW)
    assert [d.day for d in days] == [A.day_of(NOW - k * DAY) for k in (3, 2, 1, 0)]
    today = days[-1]
    assert abs(today.total - 0.40) < 1e-9 and abs(today.by_provider["anthropic"] - 0.40) < 1e-9
    assert days[0].total == 0.60 and days[1].total == 0.0 and isinstance(days[1].total, float)


def test_provider_breakdown_is_sorted_by_cost_with_shares():
    rows = A.provider_breakdown(ROWS)
    # elevenlabs 0.60 > anthropic 0.42 > pexels 0.00
    assert [r.provider for r in rows] == ["elevenlabs", "anthropic", "pexels"]
    anthropic = next(r for r in rows if r.provider == "anthropic")
    assert anthropic.calls == 3 and anthropic.tokens_in == 1000 + 500 + 10
    assert abs(sum(r.share for r in rows) - 1.0) < 1e-9


def test_model_table_gives_average_cost_per_call():
    table = {(r.provider, r.model): r for r in A.model_table(ROWS)}
    claude = table[("anthropic", "claude-5")]
    assert claude.calls == 3 and abs(claude.avg_cost - (0.30 + 0.10 + 0.02) / 3) < 1e-9


def test_token_totals_and_budget_levels():
    tokens = A.token_totals(ROWS)
    assert tokens.tokens_in == 1510 and tokens.tokens_out == 510 and tokens.total == 2020
    spent = 1.0
    assert A.budget_status(spent, None).level == "none"
    assert A.budget_status(spent, 0).level == "none"
    ok = A.budget_status(spent, 10.0)
    assert ok.level == "ok" and ok.percent == 10.0 and ok.remaining == 9.0
    assert A.budget_status(spent, 1.2).level == "warn"
    over = A.budget_status(spent, 0.5)
    assert over.level == "over" and over.remaining == 0.0


def test_chart_data_bundles_series_budget_and_tokens():
    data = A.chart_data(ROWS, days=4, budget_usd=5.0, now=NOW)
    assert len(data.days) == 4 and len(data.totals) == 4
    assert data.providers[0] == "elevenlabs"                # en pahalı önce
    assert len(data.series["anthropic"]) == 4
    assert data.budget["level"] == "ok" and data.tokens["total"] == 2020


def test_fetch_rows_reads_api_usage_from_the_project_db(tmp_path):
    db = ProjectDB(tmp_path / "proje.db")
    try:
        db.add_usage("anthropic", model="claude-5", tokens_in=120, tokens_out=30, cost_usd=0.02)
        db.add_usage("pexels", units=1)
        rows = A.fetch_rows(db)
        assert {r["provider"] for r in rows} == {"anthropic", "pexels"}
        since = A.fetch_rows(db, since=NOW + 10 * DAY)
        assert since == []
        totals = A.token_totals(rows)
        assert totals.tokens_in == 120 and totals.tokens_out == 30
    finally:
        db.close()
