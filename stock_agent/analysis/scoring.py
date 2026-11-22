"""
The quantitative screener: gives every stock a 0-100 score.

Why not let the AI do this for all ~250 stocks?
  * Cost and speed: rules run in milliseconds and cost nothing.
  * Consistency: the same numbers always produce the same score.
The AI agents then spend their (expensive) reasoning only on the shortlist.
This "cheap filter first, smart analysis second" pattern is very common in
real agentic systems.

Score = 5 pillars, each built from a few metrics:
    Quality 25 | Growth 25 | Valuation 20 | Financial health 15 | Momentum 15
"""

from statistics import median

PILLAR_WEIGHTS = {"quality": 25, "growth": 25, "valuation": 20, "health": 15, "momentum": 15}
FINANCIAL_SECTORS = {"Financial Services"}


def scale(value, bad, good):
    """Map a value onto 0..1 linearly. Works whether 'good' is above or below 'bad'.

    scale(0.20, bad=0.05, good=0.25) -> 0.75   (higher is better)
    scale(30,  bad=50,  good=15)    -> ~0.57   (lower is better, e.g. P/E)
    """
    if value is None:
        return None
    fraction = (value - bad) / (good - bad)
    return max(0.0, min(1.0, fraction))


def _rsi_score(rsi):
    """Best when RSI is in a healthy 40-65 band; penalise overbought / oversold."""
    if rsi is None:
        return None
    if 40 <= rsi <= 65:
        return 1.0
    if rsi > 65:
        return scale(rsi, 80, 65)
    return scale(rsi, 25, 40)


def sector_pe_medians(snapshots: dict[str, dict]) -> dict[str, float]:
    by_sector: dict[str, list[float]] = {}
    for s in snapshots.values():
        if s.get("pe") and 0 < s["pe"] < 300:
            by_sector.setdefault(s["sector"], []).append(s["pe"])
    return {sector: median(pes) for sector, pes in by_sector.items() if len(pes) >= 3}


def _fresh_quarters(s: dict) -> dict:
    """Quarterly signals, or {} when Yahoo's quarterly data is out of date."""
    q = s.get("quarterly_summary", {})
    return {} if q.get("stale") else q


def _metric_scores(s: dict, sector_pe: float | None) -> dict[str, dict[str, float | None]]:
    """Return {pillar: {metric_name: 0..1 score or None-if-missing}}."""
    t = s.get("technicals", {})
    q = _fresh_quarters(s)
    is_financial = s["sector"] in FINANCIAL_SECTORS

    pe = s.get("pe")
    relative_pe = pe / sector_pe if pe and pe > 0 and sector_pe else None
    loss_making = (s.get("eps_ttm") or 0) < 0

    metrics = {
        "quality": {
            "roe": scale(s.get("roe"), 0.08, 0.25),
            "profit_margin": scale(s.get("profit_margin"), 0.10, 0.30) if is_financial
            else scale(s.get("profit_margin"), 0.03, 0.20),
        },
        "growth": {
            "revenue_growth": scale(s.get("revenue_growth"), 0.0, 0.20),
            "earnings_growth": scale(s.get("earnings_growth"), 0.0, 0.25),
            "quarterly_eps_yoy": scale(q.get("eps_yoy"), 0.0, 0.25),
            "eps_growth_streak": scale(q.get("eps_growth_streak"), 0, 3),
        },
        "valuation": {
            "pe_vs_sector": 0.0 if loss_making else scale(relative_pe, 1.6, 0.7),
            "peg": scale(s.get("peg"), 3.0, 1.0) if s.get("peg") and s["peg"] > 0 else None,
        },
        "health": {},
        "momentum": {
            "vs_200dma": scale(t.get("pct_vs_sma200"), -0.15, 0.10),
            "return_6m": scale(t.get("return_6m"), -0.15, 0.25),
            "rsi": _rsi_score(t.get("rsi14")),
        },
    }
    if is_financial:
        # Banks run on borrowed money by design - debt/equity is meaningless.
        metrics["health"]["roa"] = scale(s.get("roa"), 0.005, 0.02)
        metrics["valuation"]["price_to_book"] = scale(s.get("pb"), 5.0, 1.5)
    else:
        metrics["quality"]["operating_margin"] = scale(q.get("operating_margin_latest"), 0.05, 0.25)
        metrics["health"]["debt_to_equity"] = scale(s.get("debt_to_equity"), 1.5, 0.1)
        metrics["health"]["current_ratio"] = scale(s.get("current_ratio"), 0.8, 2.0)
        fcf = s.get("free_cash_flow")
        metrics["health"]["positive_fcf"] = None if fcf is None else (1.0 if fcf > 0 else 0.0)
    return metrics


def _red_flags(s: dict, sector_pe: float | None) -> list[str]:
    t, q = s.get("technicals", {}), _fresh_quarters(s)
    flags = []
    if s.get("quarterly_summary", {}).get("stale"):
        flags.append("Quarterly data is stale (latest quarter "
                     f"{s['quarterly_summary']['latest_period']}) - verify results manually")
    if (s.get("eps_ttm") or 0) < 0:
        flags.append("Loss-making (negative TTM EPS)")
    if q.get("eps_yoy") is not None and q["eps_yoy"] < -0.15:
        flags.append(f"Latest quarter EPS down {abs(q['eps_yoy']):.0%} YoY")
    if q.get("operating_margin_change_yoy") is not None and q["operating_margin_change_yoy"] < -0.04:
        flags.append("Operating margin shrank >4 pts YoY")
    if s["sector"] not in FINANCIAL_SECTORS and (s.get("debt_to_equity") or 0) > 2:
        flags.append(f"High debt (D/E {s['debt_to_equity']:.1f}x)")
    if s.get("pe") and sector_pe and s["pe"] > 2 * sector_pe:
        flags.append(f"P/E {s['pe']:.0f} is >2x sector median ({sector_pe:.0f})")
    if t.get("pct_vs_sma200") is not None and t["pct_vs_sma200"] < -0.15:
        flags.append("Trading >15% below 200-day average (downtrend)")
    if (t.get("rsi14") or 0) > 78:
        flags.append("RSI above 78 (overheated short term)")
    return flags


def rating_for(score: float) -> str:
    if score >= 75:
        return "Strong"
    if score >= 60:
        return "Good"
    if score >= 45:
        return "Neutral"
    return "Weak"


def score_stock(s: dict, sector_pe: float | None) -> dict:
    metrics = _metric_scores(s, sector_pe)
    pillars, missing = {}, []
    for pillar, values in metrics.items():
        filled = []
        for name, value in values.items():
            if value is None:
                missing.append(name)
                filled.append(0.5)  # unknown -> neutral, so missing data neither helps nor kills
            else:
                filled.append(value)
        average = sum(filled) / len(filled) if filled else 0.5
        pillars[pillar] = round(average * PILLAR_WEIGHTS[pillar], 1)

    total = round(sum(pillars.values()), 1)
    flags = _red_flags(s, sector_pe)
    penalised = [f for f in flags if not f.startswith("Quarterly data is stale")]
    total = max(0.0, total - 3 * len(penalised))  # each real red flag costs 3 points
    total_metrics = sum(len(v) for v in metrics.values())
    return {
        "symbol": s["symbol"],
        "name": s["name"],
        "sector": s["sector"],
        "segment": s["segment"],
        "price": s.get("price"),
        "pe": s.get("pe"),
        "sector_pe": sector_pe,
        "eps_ttm": s.get("eps_ttm"),
        "roe": s.get("roe"),
        "score": total,
        "rating": rating_for(total),
        "pillars": pillars,
        "red_flags": flags,
        "missing_metrics": missing,
        "data_completeness": round(1 - len(missing) / total_metrics, 2),
    }


def score_universe(snapshots: dict[str, dict]) -> list[dict]:
    """Score every stock; returns a list sorted best-first."""
    medians = sector_pe_medians(snapshots)
    scores = [score_stock(s, medians.get(s["sector"])) for s in snapshots.values()]
    return sorted(scores, key=lambda r: r["score"], reverse=True)
