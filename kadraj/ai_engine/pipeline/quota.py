"""Plan / kota tahmincisi: "Planının %X'i kullanılacak" (tuval: Daha önce %38 · Bu proje %18 · Kalan %44).

Plan, ``settings["plan.budget_usd"]`` (aylık USD) ile tanımlanır; **bu ay** ``api_usage``'a yazılan harcama "daha önce"dir. Tahmin,
``pricing`` tablosu + ayarlardaki geçersiz kılmalarla yapılır (gerçek fatura sağlayıcıdadır). Plan tanımsızsa yüzde yerine dolar gösterilir.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from .. import analytics, pricing


@dataclass(frozen=True)
class Estimate:
    cost_usd: float
    images: int = 0
    video_seconds: float = 0.0
    tts_chars: int = 0
    sfx_seconds: float = 0.0
    llm_tokens: int = 0
    breakdown: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanShare:
    budget: Optional[float]
    before_pct: float
    project_pct: float
    remaining_pct: float
    level: str                       # none | ok | tight | over
    text: str = ""                   # "Planının %18'i kullanılacak"

    @property
    def fits(self) -> bool:
        return self.level in ("none", "ok", "tight")


def _setting(settings: Any, key: str, default: Any) -> Any:
    if settings is None:
        return default
    try:
        v = settings.get(key)
    except Exception:
        return default
    return default if v in (None, "") else v


def unit_prices(settings: Any = None) -> Dict[str, float]:
    return {"image": float(_setting(settings, "pricing.image", 0.04)),
            "video_second": float(_setting(settings, "providers.video.cost_per_second", pricing.VIDEO_PER_SECOND)),
            "tts_char": float(_setting(settings, "pricing.tts_char", pricing.TTS_PER_CHAR)),
            "sfx_second": float(_setting(settings, "pricing.sfx_second", pricing.SFX_PER_SECOND)),
            "llm_mtok": float(_setting(settings, "pricing.llm_mtok", 12.0))}      # karışık girdi/çıktı ortalaması


def estimate(*, images: int = 0, video_seconds: float = 0.0, tts_chars: int = 0, sfx_seconds: float = 0.0, llm_tokens: int = 0,
             settings: Any = None) -> Estimate:
    p = unit_prices(settings)
    parts = {"image": images * p["image"], "video": video_seconds * p["video_second"], "tts": tts_chars * p["tts_char"],
             "sfx": sfx_seconds * p["sfx_second"], "llm": llm_tokens / 1e6 * p["llm_mtok"]}
    return Estimate(round(sum(parts.values()), 4), images, video_seconds, tts_chars, sfx_seconds, llm_tokens,
                    {k: round(v, 4) for k, v in parts.items()})


def month_start(now: Optional[float] = None) -> float:
    t = time.localtime(now if now is not None else time.time())
    return time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def spent_this_month(db: Any, now: Optional[float] = None) -> float:
    if db is None:
        return 0.0
    try:
        rows = analytics.fetch_rows(db, since=month_start(now))
    except Exception:
        return 0.0
    return round(sum(float(r.get("cost_usd") or 0) for r in rows), 6)


def plan_share(est: Estimate, *, spent: float = 0.0, settings: Any = None) -> PlanShare:
    budget = float(_setting(settings, "plan.budget_usd", 0) or 0)
    if budget <= 0:
        return PlanShare(None, 0.0, 0.0, 0.0, "none", f"~${est.cost_usd:.2f}")
    before = spent / budget * 100
    proj = est.cost_usd / budget * 100
    remaining = 100 - before - proj
    level = "over" if remaining < 0 else "tight" if remaining < 10 else "ok"
    from ...core import i18n
    text = i18n.t("ai.quota.share", pct=f"{proj:.0f}") if i18n.has("ai.quota.share") else f"Planının %{proj:.0f}'i kullanılacak"
    return PlanShare(budget, round(before, 1), round(proj, 1), round(remaining, 1), level, text)
