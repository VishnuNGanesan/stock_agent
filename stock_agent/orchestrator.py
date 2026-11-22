"""
The Orchestrator coordinates the whole daily pipeline.

    1. Universe      - which stocks?                  (plain Python)
    2. Data          - download fundamentals/prices   (plain Python, parallel)
    3. Screener      - score all ~250 stocks 0-100     (rules, no AI)
    4. Shortlist     - top scorers + your holdings + big score movers
    5. Analyst agent - deep-dives each shortlisted stock with tools     (AI)
    6. Risk agent    - challenges each verdict, sets the final call     (AI)
    7. Strategist    - writes the executive brief                       (AI)
    8. Report + email

This is an "orchestrator-workers" design: deterministic code controls the
overall flow, and agents handle the parts that need judgement. Production
agentic systems are usually built this way, not as one agent doing everything.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from statistics import median

from stock_agent import config
from stock_agent.agents import llm
from stock_agent.agents.research_agent import build_research_agent, research_task
from stock_agent.agents.risk_agent import build_risk_agent, review_task
from stock_agent.agents.strategist_agent import build_strategist_agent, strategist_task
from stock_agent.agents.tools import MarketToolbox
from stock_agent.analysis.scoring import score_universe
from stock_agent.data.market_data import collect_all
from stock_agent.data.universe import load_universe
from stock_agent.storage import db

log = logging.getLogger(__name__)


# ----------------------------------------------------------------- shortlist
def select_candidates(scores: list[dict], previous: dict[str, float], top_n: int,
                      only_symbols: list[str] | None = None) -> list[dict]:
    """Choose which stocks deserve (costly) agent research. Returns [{symbol, reason}]."""
    by_symbol = {s["symbol"]: s for s in scores}
    if only_symbols:
        return [{"symbol": s, "reason": "Requested by you"} for s in only_symbols if s in by_symbol]

    chosen: dict[str, str] = {}
    for symbol in config.MY_HOLDINGS:
        if symbol in by_symbol:
            chosen[symbol] = "You own this stock"

    per_sector: dict[str, int] = {}
    for s in scores:  # already sorted best-first
        if len([c for c in chosen.values() if c.startswith("Top")]) >= top_n:
            break
        if s["symbol"] in chosen or s["data_completeness"] < 0.7:
            continue
        if any(flag.startswith("Loss-making") for flag in s["red_flags"]):
            continue
        if per_sector.get(s["sector"], 0) >= 3:  # keep the shortlist diversified
            continue
        per_sector[s["sector"]] = per_sector.get(s["sector"], 0) + 1
        rank = scores.index(s) + 1
        chosen[s["symbol"]] = f"Top screener score ({s['score']}/100, rank #{rank})"

    movers = [
        (s, s["score"] - previous[s["symbol"]]) for s in scores
        if s["symbol"] in previous and s["symbol"] not in chosen
        and abs(s["score"] - previous[s["symbol"]]) >= 8
    ]
    movers.sort(key=lambda pair: abs(pair[1]), reverse=True)
    for s, change in movers[: config.MAX_BIG_MOVERS]:
        chosen[s["symbol"]] = f"Big score change since last run ({change:+.1f} pts, now {s['score']})"

    return [{"symbol": sym, "reason": reason} for sym, reason in chosen.items()]


# ------------------------------------------------------------------- breadth
def market_breadth(snapshots: dict[str, dict], scores: list[dict]) -> dict:
    tech = [s.get("technicals", {}) for s in snapshots.values()]
    above = [t["above_sma200"] for t in tech if t.get("above_sma200") is not None]
    r6 = [t["return_6m"] for t in tech if t.get("return_6m") is not None]
    r1 = [t["return_1m"] for t in tech if t.get("return_1m") is not None]
    day = [t["day_change"] for t in tech if t.get("day_change") is not None]

    sector_scores: dict[str, list[float]] = {}
    for s in scores:
        sector_scores.setdefault(s["sector"], []).append(s["score"])
    sector_avg = sorted(((sec, round(sum(v) / len(v), 1)) for sec, v in sector_scores.items()
                         if len(v) >= 3), key=lambda x: x[1], reverse=True)
    ratings = {r: sum(1 for s in scores if s["rating"] == r) for r in ("Strong", "Good", "Neutral", "Weak")}
    return {
        "stocks_analysed": len(scores),
        "pct_above_200dma": round(100 * sum(above) / len(above), 1) if above else None,
        "median_return_1m_pct": round(100 * median(r1), 1) if r1 else None,
        "median_return_6m_pct": round(100 * median(r6), 1) if r6 else None,
        "advancers_today": sum(1 for d in day if d > 0),
        "decliners_today": sum(1 for d in day if d < 0),
        "rating_counts": ratings,
        "strongest_sectors_by_avg_score": sector_avg[:5],
        "weakest_sectors_by_avg_score": sector_avg[-3:],
    }


# ------------------------------------------------------------ verdict logic
def _clamp(n) -> int:
    return max(1, min(10, int(n)))


def rule_based_verdict(score: dict) -> dict:
    """Used with --no-llm: a simple decision from the screener alone (free, for testing)."""
    sc = score["score"]
    decision = ("BUY" if sc >= 75 else "ACCUMULATE" if sc >= 65 else "HOLD" if sc >= 50 else "AVOID")
    return {
        "decision": decision, "conviction": _clamp(sc / 10), "valuation_view": "FAIRLY_VALUED",
        "earnings_momentum": "STEADY",
        "investment_thesis": f"Rule-based: screener score {sc}/100 ({score['rating']}). "
                             f"Pillars: {score['pillars']}.",
        "key_strengths": [k for k, v in score["pillars"].items() if v >= 0.7 * _MAX[k]],
        "key_risks": score["red_flags"] or ["None flagged by the screener"],
        "entry_strategy": "Stagger purchases over several weeks.",
        "what_would_change_my_mind": "Run with AI agents for a real analysis.",
    }


_MAX = {"quality": 25, "growth": 25, "valuation": 20, "health": 15, "momentum": 15}


def analyse_one(candidate: dict, toolbox: MarketToolbox, use_llm: bool) -> dict | None:
    symbol = candidate["symbol"]
    snap, score = toolbox.snapshots[symbol], toolbox.scores[symbol]
    base = {"symbol": symbol, "name": snap["name"], "sector": snap["sector"],
            "segment": snap["segment"], "price": snap.get("price"), "pe": snap.get("pe"),
            "screen_score": score["score"], "selection_reason": candidate["reason"],
            "is_holding": symbol in config.MY_HOLDINGS, "red_flags": score["red_flags"]}

    if not use_llm:
        verdict = rule_based_verdict(score)
        return {**base, **verdict, "analyst_decision": verdict["decision"],
                "final_decision": verdict["decision"], "final_conviction": verdict["conviction"],
                "agrees_with_analyst": True, "challenges": [], "review_summary": "Not reviewed (no-LLM mode)."}

    log.info("🔎 Researching %s - %s", symbol, candidate["reason"])
    analyst = build_research_agent(toolbox).run(
        research_task(symbol, snap["name"], base["is_holding"], candidate["reason"]), label=symbol)
    if not analyst.output:
        log.warning("Analyst gave no verdict for %s (%s)", symbol, analyst.stop_reason)
        return None
    verdict = analyst.output
    verdict["conviction"] = _clamp(verdict["conviction"])
    log.info("   📝 Analyst: %s %s (conviction %s) after %d turns, tools: %s", symbol,
             verdict["decision"], verdict["conviction"], analyst.turns,
             ", ".join(analyst.tool_calls[:-1]))

    reviewer = build_risk_agent(toolbox).run(review_task(symbol, snap["name"], verdict),
                                             label=symbol)
    review = reviewer.output or {
        "agrees_with_analyst": True, "final_decision": verdict["decision"],
        "final_conviction": verdict["conviction"], "challenges": [],
        "review_summary": f"Review unavailable ({reviewer.stop_reason})."}
    review["final_conviction"] = _clamp(review["final_conviction"])
    log.info("   🛡️  Reviewer: %s -> %s (conviction %s)%s", symbol, review["final_decision"],
             review["final_conviction"], "" if review["agrees_with_analyst"] else "  [changed]")

    return {**base, **verdict, "analyst_decision": verdict["decision"], **review}


def _rule_based_brief(verdicts: list[dict], breadth: dict) -> dict:
    ranked = sorted(verdicts, key=lambda v: (v["final_conviction"], v["screen_score"]), reverse=True)
    buys = [v for v in ranked if v["final_decision"] in ("STRONG_BUY", "BUY", "ACCUMULATE")]
    return {
        "headline": "Screener-only report (AI agents were not used)",
        "market_mood": f"{breadth['pct_above_200dma']}% of stocks trade above their 200-day average.",
        "executive_summary": "This report was generated without the AI agents. Run without "
                             "--no-llm for the full agentic analysis.",
        "top_picks": [{"symbol": v["symbol"], "decision": v["final_decision"],
                       "reason": f"Score {v['screen_score']}"} for v in buys[:5]],
        "holdings_actions": [{"symbol": v["symbol"], "decision": v["final_decision"],
                              "reason": f"Score {v['screen_score']}"} for v in verdicts if v["is_holding"]],
        "watchlist": [], "portfolio_guidance": "", "changes_since_last_run": "",
    }


# ----------------------------------------------------------------- pipeline
def run_pipeline(use_llm: bool = True, top_n: int | None = None, universe_limit: int | None = None,
                 only_symbols: list[str] | None = None) -> dict:
    started = datetime.now()
    run_date = db.today()
    top_n = config.TOP_N_TO_RESEARCH if top_n is None else top_n

    log.info("① Loading Nifty 100 + Midcap 150 constituents")
    universe = load_universe()
    if universe_limit:
        keep = set(config.MY_HOLDINGS) | set(only_symbols or [])
        universe = [s for s in universe if s["symbol"] in keep] + \
                   [s for s in universe if s["symbol"] not in keep][:universe_limit]
    log.info("   %d stocks to monitor", len(universe))

    log.info("② Collecting market data (cached per day)")
    snapshots = collect_all(universe)

    log.info("③ Scoring %d stocks", len(snapshots))
    scores = score_universe(snapshots)
    previous = db.previous_scores(run_date)
    for s in scores:
        s["score_change"] = round(s["score"] - previous[s["symbol"]], 1) if s["symbol"] in previous else None
    db.save_scores(scores, run_date)
    breadth = market_breadth(snapshots, scores)

    candidates = select_candidates(scores, previous, top_n, only_symbols)
    log.info("④ Shortlisted %d stocks for agent research: %s", len(candidates),
             ", ".join(c["symbol"] for c in candidates))

    log.info("⑤⑥ Running %s", "Research Analyst + Risk Reviewer agents" if use_llm else "rule-based verdicts")
    toolbox = MarketToolbox(snapshots, scores)
    # Several stocks are researched at the same time; each agent loop is independent.
    with ThreadPoolExecutor(max_workers=config.AGENT_WORKERS if use_llm else 1) as pool:
        results = list(pool.map(lambda c: _safe_analyse(c, toolbox, use_llm), candidates))
    verdicts = [v for v in results if v]

    decision_changes = []
    for v in verdicts:
        past = db.last_verdicts(v["symbol"], limit=1, before=run_date)
        v["previous_decision"] = past[0]["decision"] if past else None
        if past and past[0]["decision"] != v["final_decision"]:
            decision_changes.append(f"{v['symbol']}: {past[0]['decision']} -> {v['final_decision']} "
                                    f"(was {past[0]['run_date']})")
        if use_llm:  # rule-based test verdicts are not worth remembering
            db.save_verdict(v, run_date)

    log.info("⑦ Writing the strategist brief")
    brief = None
    if use_llm and verdicts:
        result = build_strategist_agent().run(
            strategist_task(breadth, verdicts, config.MY_HOLDINGS, decision_changes), label="brief")
        brief = result.output
    brief = brief or _rule_based_brief(verdicts, breadth)

    return {
        "run_date": run_date, "started": started, "finished": datetime.now(),
        "used_llm": use_llm, "breadth": breadth, "scores": scores, "verdicts": verdicts,
        "brief": brief, "decision_changes": decision_changes,
        "failed": [c["symbol"] for c, r in zip(candidates, results) if not r],
        "usage": llm.usage, "model": config.ANTHROPIC_MODEL,
    }


def _safe_analyse(candidate, toolbox, use_llm):
    try:
        return analyse_one(candidate, toolbox, use_llm)
    except SystemExit:
        raise
    except Exception:
        log.exception("Analysis of %s failed", candidate["symbol"])
        return None
