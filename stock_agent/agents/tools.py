"""
Tools = Python functions an agent may call.

Each Tool has:
  * name         - what Claude writes when it wants the tool
  * description  - how Claude decides WHEN to use it (write these carefully!)
  * input_schema - JSON Schema of the arguments (Claude fills these in)
  * function     - the Python code we actually run

Claude never runs code itself. It only *asks*; base_agent.py runs the function
and returns the result as JSON text.
"""

from dataclasses import dataclass
from typing import Any, Callable

from stock_agent.data.market_data import recent_news
from stock_agent.storage import db

CRORE = 1e7  # 1 crore = 10 million rupees, the unit Indian financials are quoted in


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    function: Callable[..., Any] | None = None   # None for "finish" tools

    def to_api(self) -> dict:
        """The dict format the Claude API expects in its `tools` list."""
        # strict=True makes the API guarantee Claude's arguments match the schema exactly.
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema, "strict": True}


def object_schema(properties: dict) -> dict:
    """Strict-mode schemas need every property listed as required and no extras."""
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


SYMBOL_ONLY = object_schema({"symbol": {"type": "string", "description": "NSE symbol, e.g. TCS"}})


def _tidy(value):
    """Round floats so tool results stay short (fewer tokens = cheaper, clearer)."""
    if isinstance(value, float):
        return round(value, 4) if abs(value) < 100 else round(value, 1)
    if isinstance(value, dict):
        return {k: _tidy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_tidy(v) for v in value]
    return value


def _cr(value):
    return None if value is None else round(value / CRORE, 1)


class MarketToolbox:
    """Holds today's data and builds the tools that read it.

    The tools are methods, so they can see `self.snapshots` and `self.scores`
    without global variables.
    """

    def __init__(self, snapshots: dict[str, dict], scores: list[dict]):
        self.snapshots = snapshots
        self.scores = {s["symbol"]: s for s in scores}
        self.rank = {s["symbol"]: i + 1 for i, s in enumerate(scores)}

    def _snap(self, symbol: str) -> dict:
        symbol = symbol.upper().removesuffix(".NS")
        if symbol not in self.snapshots:
            raise ValueError(f"{symbol} is not in today's monitored universe")
        return self.snapshots[symbol]

    # ------------------------------------------------------------ tool code
    def get_fundamentals(self, symbol: str) -> dict:
        s = self._snap(symbol)
        keys = ["name", "sector", "segment", "price", "pe", "forward_pe", "eps_ttm", "eps_forward",
                "pb", "peg", "roe", "roa", "profit_margin", "operating_margin", "revenue_growth",
                "earnings_growth", "debt_to_equity", "current_ratio", "dividend_yield",
                "promoter_holding", "institutional_holding", "beta"]
        data = {k: s.get(k) for k in keys}
        data["market_cap_cr"] = _cr(s.get("market_cap"))
        data["free_cash_flow_cr"] = _cr(s.get("free_cash_flow"))
        data["sector_median_pe"] = self.scores.get(s["symbol"], {}).get("sector_pe")
        data["units"] = ("ratios are decimals (0.18 = 18%); dividend_yield is already in %; "
                         "debt_to_equity is a ratio (0.5 = 0.5x); promoter_holding is Yahoo's "
                         "insider-holding estimate")
        return _tidy(data)

    def get_quarterly_results(self, symbol: str) -> dict:
        s = self._snap(symbol)
        quarters = [
            {"quarter_end": q["period"], "revenue_cr": _cr(q["revenue"]),
             "operating_profit_cr": _cr(q["operating_income"]),
             "net_profit_cr": _cr(q["net_income"]), "eps": q["eps"],
             "operating_margin": q["operating_margin"]}
            for q in s.get("quarters", [])
        ]
        result = {"amounts_in": f"crore {s.get('financial_currency', 'INR')}",
                  "quarters_newest_first": quarters, "summary": s.get("quarterly_summary", {})}
        warnings = []
        if s.get("financial_currency", "INR") != "INR":
            warnings.append(f"Results are reported in {s['financial_currency']}, not INR; compare "
                            "growth rates, not absolute amounts, with INR-based figures like price.")
        if result["summary"].get("stale"):
            warnings.append("Latest quarter is more than 200 days old: the data source has not "
                            "updated. Treat the quarterly trend as unreliable.")
        if warnings:
            result["data_warnings"] = warnings
        return _tidy(result)

    def get_price_technicals(self, symbol: str) -> dict:
        return _tidy(self._snap(symbol).get("technicals", {}))

    def get_screening_score(self, symbol: str) -> dict:
        s = self._snap(symbol)
        record = dict(self.scores[s["symbol"]])
        record["rank_in_universe"] = f"{self.rank[s['symbol']]} of {len(self.rank)}"
        record["pillar_max_points"] = {"quality": 25, "growth": 25, "valuation": 20,
                                       "health": 15, "momentum": 15}
        return _tidy(record)

    def compare_with_sector_peers(self, symbol: str) -> dict:
        s = self._snap(symbol)
        peers = [p for p in self.snapshots.values() if p["sector"] == s["sector"]]
        peers.sort(key=lambda p: p.get("market_cap") or 0, reverse=True)
        rows = [
            {"symbol": p["symbol"], "name": p["name"], "market_cap_cr": _cr(p.get("market_cap")),
             "pe": p.get("pe"), "roe": p.get("roe"), "profit_margin": p.get("profit_margin"),
             "revenue_growth": p.get("revenue_growth"),
             "eps_yoy_latest_q": p.get("quarterly_summary", {}).get("eps_yoy"),
             "return_6m": p.get("technicals", {}).get("return_6m"),
             "screen_score": self.scores.get(p["symbol"], {}).get("score")}
            for p in peers[:10]
        ]
        return _tidy({"sector": s["sector"], "peers_by_market_cap": rows})

    def get_history_from_memory(self, symbol: str) -> dict:
        s = self._snap(symbol)
        return _tidy({
            "score_history_newest_first": db.score_history(s["symbol"], limit=20),
            "previous_verdicts": db.last_verdicts(s["symbol"]),
        })

    def get_recent_news(self, symbol: str) -> dict:
        s = self._snap(symbol)
        return {"note": "Yahoo headlines; may include loosely related articles",
                "news": recent_news(s["symbol"])}

    # --------------------------------------------------------- tool catalog
    def research_tools(self) -> list[Tool]:
        return [
            Tool("get_fundamentals",
                 "Valuation and quality metrics for a stock: P/E, EPS (TTM and forward), P/B, PEG, "
                 "ROE, ROA, margins, growth, debt/equity, current ratio, dividend yield, market cap "
                 "in Rs crore, promoter/institutional holding, and the sector median P/E.",
                 SYMBOL_ONLY, self.get_fundamentals),
            Tool("get_quarterly_results",
                 "Last ~5 quarterly results (revenue, operating profit, net profit in Rs crore, "
                 "EPS, operating margin) plus YoY / QoQ growth and EPS growth streak. Use this to "
                 "judge whether earnings are accelerating or slowing.",
                 SYMBOL_ONLY, self.get_quarterly_results),
            Tool("get_price_technicals",
                 "Price trend: current price, 50/200-day averages, RSI(14), 52-week high/low, "
                 "1m/3m/6m/1y returns, volatility. Use for entry timing, not for the long-term thesis.",
                 SYMBOL_ONLY, self.get_price_technicals),
            Tool("get_screening_score",
                 "Today's quantitative screener result: 0-100 score, pillar points, red flags, rank "
                 "among all monitored Nifty large & mid caps, and which metrics were missing.",
                 SYMBOL_ONLY, self.get_screening_score),
            Tool("compare_with_sector_peers",
                 "Up to 10 largest companies in the same sector with P/E, ROE, margins, growth, "
                 "6-month return and screen score. Use to judge relative valuation and quality.",
                 SYMBOL_ONLY, self.compare_with_sector_peers),
            Tool("get_history_from_memory",
                 "This system's own memory: past daily screen scores and previous agent verdicts "
                 "for the stock. Use to explain what changed since earlier recommendations.",
                 SYMBOL_ONLY, self.get_history_from_memory),
            Tool("get_recent_news",
                 "Recent news headlines about the company. Check for governance issues, regulatory "
                 "action, big orders, management change or other events numbers do not show.",
                 SYMBOL_ONLY, self.get_recent_news),
        ]
