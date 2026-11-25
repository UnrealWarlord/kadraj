"""AI kullanım analitiği: ``api_usage`` satırlarından günlük maliyet, sağlayıcı ve model tabloları,
bütçe yüzdesi ve token toplamları. Legend grafikleri için ``chart_data`` hazır diziler döner.

Saf fonksiyonlar satır sözlüklerini (``provider``, ``model``, ``tokens_in``, ``tokens_out``, ``units``,
``cost_usd``, ``created``) alır. ``fetch_rows`` veritabanından okur; ``ProjectDB``'nin genel bir okuma
yöntemi olmadığı için ``_rows`` yardımcısını kullanır (Karar: db.py değiştirilmedi).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

WARN_PERCENT = 80.0
OVER_PERCENT = 100.0
#: Tamamlanan render işinin türü; ``kadraj/modules/editor/service.py`` içindeki ``JOB_RENDER`` ile aynıdır
#: (``tests/core/test_legend_analytics.py`` bu eşitliği doğrular).
RENDER_KIND = "editor.render"


@dataclass(frozen=True)
class DayCost:
    day: str                                  # YYYY-MM-DD (yerel saat)
    total: float
    by_provider: Dict[str, float]


@dataclass(frozen=True)
class ProviderTotal:
    provider: str
    calls: int
    tokens_in: int
    tokens_out: int
    units: float
    cost_usd: float
    share: float                              # toplam maliyetin oranı, 0..1


@dataclass(frozen=True)
class ModelRow:
    provider: str
    model: str
    calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    avg_cost: float


@dataclass(frozen=True)
class Budget:
    spent: float
    budget: Optional[float]
    percent: Optional[float]
    remaining: Optional[float]
    level: str                                # none | ok | warn | over


@dataclass(frozen=True)
class TokenTotals:
    tokens_in: int
    tokens_out: int
    total: int


def fetch_rows(db: Any, *, since: Optional[float] = None, until: Optional[float] = None) -> List[Dict[str, Any]]:
    """``api_usage`` satırları (eskiden yeniye). ``since`` dahil, ``until`` hariç."""
    where: List[str] = []
    params: List[Any] = []
    if since is not None:
        where.append("created >= ?")
        params.append(float(since))
    if until is not None:
        where.append("created < ?")
        params.append(float(until))
    sql = ("SELECT provider, model, tokens_in, tokens_out, units, cost_usd, created FROM api_usage"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created")
    return db._rows(sql, params)


def _f(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _i(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def day_of(created: Any) -> str:
    return date.fromtimestamp(_f(created)).isoformat()


def daily_costs(rows: Iterable[Mapping[str, Any]], *, days: int = 30, now: Optional[float] = None) -> List[DayCost]:
    """Son ``days`` günün (bugün dahil) her biri için toplam ve sağlayıcı kırılımı. Boş günler sıfırdır."""
    end = date.fromtimestamp(time.time() if now is None else now)
    span = [(end - timedelta(days=offset)).isoformat() for offset in range(max(days, 1) - 1, -1, -1)]
    buckets: Dict[str, Dict[str, float]] = {day: {} for day in span}
    for row in rows:
        day = day_of(row.get("created"))
        if day in buckets:
            provider = str(row.get("provider") or "bilinmiyor")
            buckets[day][provider] = buckets[day].get(provider, 0.0) + _f(row.get("cost_usd"))
    return [DayCost(day, round(float(sum(parts.values())), 6), {k: round(v, 6) for k, v in parts.items()})
            for day, parts in buckets.items()]


def provider_breakdown(rows: Iterable[Mapping[str, Any]]) -> List[ProviderTotal]:
    """Sağlayıcı başına toplamlar, maliyete göre azalan sırada."""
    acc: Dict[str, Dict[str, float]] = {}
    for row in rows:
        name = str(row.get("provider") or "bilinmiyor")
        slot = acc.setdefault(name, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "units": 0.0, "cost": 0.0})
        slot["calls"] += 1
        slot["tokens_in"] += _i(row.get("tokens_in"))
        slot["tokens_out"] += _i(row.get("tokens_out"))
        slot["units"] += _f(row.get("units"))
        slot["cost"] += _f(row.get("cost_usd"))
    grand = sum(s["cost"] for s in acc.values())
    out = [ProviderTotal(name, int(s["calls"]), int(s["tokens_in"]), int(s["tokens_out"]), s["units"],
                         round(s["cost"], 6), (s["cost"] / grand) if grand else 0.0)
           for name, s in acc.items()]
    return sorted(out, key=lambda t: (-t.cost_usd, t.provider))


def model_table(rows: Iterable[Mapping[str, Any]]) -> List[ModelRow]:
    """Sağlayıcı + model çiftine göre tablo; ortalama maliyet çağrı başına."""
    acc: Dict[tuple, Dict[str, float]] = {}
    for row in rows:
        key = (str(row.get("provider") or "bilinmiyor"), str(row.get("model") or ""))
        slot = acc.setdefault(key, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0})
        slot["calls"] += 1
        slot["tokens_in"] += _i(row.get("tokens_in"))
        slot["tokens_out"] += _i(row.get("tokens_out"))
        slot["cost"] += _f(row.get("cost_usd"))
    out = [ModelRow(p, m, int(s["calls"]), int(s["tokens_in"]), int(s["tokens_out"]), round(s["cost"], 6),
                    round(s["cost"] / s["calls"], 6) if s["calls"] else 0.0)
           for (p, m), s in acc.items()]
    return sorted(out, key=lambda r: (-r.cost_usd, r.provider, r.model))


def token_totals(rows: Iterable[Mapping[str, Any]]) -> TokenTotals:
    tin = tout = 0
    for row in rows:
        tin += _i(row.get("tokens_in"))
        tout += _i(row.get("tokens_out"))
    return TokenTotals(tin, tout, tin + tout)


def budget_status(spent: float, budget_usd: Optional[float]) -> Budget:
    """Bütçe yüzdesi. Bütçe tanımsız ya da sıfır/negatifse ``level="none"``."""
    if budget_usd is None or budget_usd <= 0:
        return Budget(round(spent, 6), None, None, None, "none")
    percent = spent / budget_usd * 100.0
    level = "over" if percent >= OVER_PERCENT else ("warn" if percent >= WARN_PERCENT else "ok")
    return Budget(round(spent, 6), float(budget_usd), round(percent, 2),
                  round(max(budget_usd - spent, 0.0), 6), level)


@dataclass(frozen=True)
class ChartData:
    days: List[str]
    totals: List[float]
    providers: List[str]
    series: Dict[str, List[float]]
    budget: Dict[str, Any] = field(default_factory=dict)
    tokens: Dict[str, int] = field(default_factory=dict)


def chart_data(rows: Sequence[Mapping[str, Any]], *, days: int = 30, budget_usd: Optional[float] = None,
               now: Optional[float] = None) -> ChartData:
    """Legend grafikleri için hazır diziler: günlük toplamlar, sağlayıcı serileri, bütçe ve token özeti."""
    series_days = daily_costs(rows, days=days, now=now)
    providers = [p.provider for p in provider_breakdown(rows)]
    series = {p: [d.by_provider.get(p, 0.0) for d in series_days] for p in providers}
    spent = sum(_f(r.get("cost_usd")) for r in rows)
    tokens = token_totals(rows)
    return ChartData(
        days=[d.day for d in series_days],
        totals=[d.total for d in series_days],
        providers=providers,
        series=series,
        budget=asdict(budget_status(spent, budget_usd)),
        tokens={"tokens_in": tokens.tokens_in, "tokens_out": tokens.tokens_out, "total": tokens.total},
    )


# ---------------------------------------------------------------------------
# Bu ay KPI'ları (Legend proje merkezi): takvim ayı, yerel saat
# ---------------------------------------------------------------------------
def month_start(now: Optional[float] = None) -> float:
    """İçinde bulunulan ayın ilk gününün yerel gece yarısı (epoch saniye)."""
    current = datetime.fromtimestamp(time.time() if now is None else now)
    return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def render_count(db: Any, *, now: Optional[float] = None) -> int:
    """Bu ay tamamlanan render işi sayısı (proje veritabanındaki ``jobs`` tablosundan)."""
    since = month_start(now)
    return sum(1 for job in db.list_jobs("done")
               if job.get("kind") == RENDER_KIND and _f(job.get("updated")) >= since)


@dataclass(frozen=True)
class MonthKpi:
    renders: int
    cost_usd: float
    tokens_in: int
    tokens_out: int
    budget: Budget


def month_kpi(db: Any, *, budget_usd: Optional[float] = None, now: Optional[float] = None) -> MonthKpi:
    """Bu ayın render sayısı, API maliyeti, token toplamları ve bütçe durumu."""
    since = month_start(now)
    rows = fetch_rows(db, since=since)
    spent = sum(_f(r.get("cost_usd")) for r in rows)
    tokens = token_totals(rows)
    return MonthKpi(render_count(db, now=now), round(spent, 6), tokens.tokens_in, tokens.tokens_out,
                    budget_status(spent, budget_usd))
