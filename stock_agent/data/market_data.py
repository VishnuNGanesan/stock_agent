"""
Downloads market data for one stock from Yahoo Finance (via the `yfinance`
library) and turns it into a plain Python dict we call a "snapshot".

NSE symbols on Yahoo end with ".NS" (TCS -> TCS.NS).

Every snapshot is cached as JSON in data_cache/<date>/<SYMBOL>.json, so:
  * re-running the app on the same day is fast, and
  * the AI agents' tools read the same numbers the screener used.
"""

import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf

from stock_agent import config
from stock_agent.analysis.metrics import compute_technicals, summarise_quarters

log = logging.getLogger(__name__)

# Fields we keep from yfinance's large `.info` dictionary (Yahoo name -> our name).
INFO_FIELDS = {
    "shortName": "name",
    "sector": "sector",
    "industry": "yahoo_industry",
    "currentPrice": "price",
    "marketCap": "market_cap",
    "sharesOutstanding": "shares_outstanding",
    "trailingPE": "pe",
    "forwardPE": "forward_pe",
    "trailingEps": "eps_ttm",
    "forwardEps": "eps_forward",
    "priceToBook": "pb",
    "trailingPegRatio": "peg",
    "returnOnEquity": "roe",
    "returnOnAssets": "roa",
    "profitMargins": "profit_margin",
    "operatingMargins": "operating_margin",
    "revenueGrowth": "revenue_growth",
    "earningsGrowth": "earnings_growth",
    "debtToEquity": "debt_to_equity",
    "currentRatio": "current_ratio",
    "freeCashflow": "free_cash_flow",
    "dividendYield": "dividend_yield",
    "heldPercentInsiders": "promoter_holding",
    "heldPercentInstitutions": "institutional_holding",
    "beta": "beta",
}

# Yahoo uses different row names for different kinds of companies (banks!).
QUARTER_ROWS = {
    "revenue": ["Total Revenue", "Operating Revenue", "Net Interest Income"],
    "operating_income": ["Operating Income", "EBIT", "Pretax Income"],
    "net_income": ["Net Income", "Net Income Common Stockholders"],
    "eps": ["Diluted EPS", "Basic EPS"],
}


def _clean(value):
    """Turn NaN / numpy numbers into JSON-friendly Python floats or None."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return value
    return None if math.isnan(value) or math.isinf(value) else value


def _first_row(df, candidates, column):
    for row in candidates:
        if row in df.index:
            value = _clean(df.at[row, column])
            if value is not None:
                return value
    return None


def _quarterly_results(ticker: yf.Ticker) -> list[dict]:
    """Last ~5 quarters, newest first."""
    df = ticker.quarterly_income_stmt
    if df is None or df.empty:
        return []
    quarters = []
    for column in df.columns:  # columns are quarter-end dates, newest first
        q = {"period": column.strftime("%Y-%m-%d")}
        for field, rows in QUARTER_ROWS.items():
            q[field] = _first_row(df, rows, column)
        if q["revenue"] and q["operating_income"] is not None:
            q["operating_margin"] = q["operating_income"] / q["revenue"]
        else:
            q["operating_margin"] = None
        quarters.append(q)
    return quarters


def _download_snapshot(stock: dict) -> dict:
    ticker = yf.Ticker(f"{stock['symbol']}.NS")
    info = ticker.info or {}
    snapshot = {our: _clean(info.get(yahoo)) for yahoo, our in INFO_FIELDS.items()}
    snapshot.update(
        symbol=stock["symbol"],
        name=stock["name"],
        nse_industry=stock["industry"],
        segment=stock["segment"],
        sector=info.get("sector") or stock["industry"],
        # Some companies (e.g. Infosys) file results in USD on Yahoo; prices stay in INR.
        financial_currency=info.get("financialCurrency") or "INR",
    )

    history = ticker.history(period="1y", auto_adjust=True)
    closes = [round(float(c), 2) for c in history["Close"].dropna()] if not history.empty else []
    snapshot["technicals"] = compute_technicals(closes)
    snapshot["price"] = snapshot["price"] or snapshot["technicals"].get("price")

    if not snapshot["market_cap"] and snapshot["price"] and snapshot["shares_outstanding"]:
        snapshot["market_cap"] = snapshot["price"] * snapshot["shares_outstanding"]
    # Yahoo reports debt/equity in percent (45.0 means 0.45x). Normalise to a ratio.
    if snapshot["debt_to_equity"] is not None:
        snapshot["debt_to_equity"] = snapshot["debt_to_equity"] / 100
    # dividendYield arrives already in percent (2.02 means 2.02%).

    quarters = _quarterly_results(ticker)
    snapshot["quarters"] = quarters
    snapshot["quarterly_summary"] = summarise_quarters(quarters)
    snapshot["data_date"] = config.today_ist()
    return snapshot


def get_snapshot(stock: dict, retries: int = 2) -> dict | None:
    """Return today's snapshot for one stock, from cache or freshly downloaded."""
    day_dir = config.DATA_DIR / config.today_ist()
    day_dir.mkdir(exist_ok=True)
    cache_file = day_dir / f"{stock['symbol']}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())

    for attempt in range(retries + 1):
        try:
            snapshot = _download_snapshot(stock)
            if snapshot["price"] is None:
                raise ValueError("no price returned")
            cache_file.write_text(json.dumps(snapshot, indent=1))
            return snapshot
        except Exception as exc:
            if attempt == retries:
                log.warning("Skipping %s: %s", stock["symbol"], exc)
                return None
            time.sleep(2 * (attempt + 1))  # back off a little, Yahoo rate-limits


def collect_all(universe: list[dict], workers: int = 6) -> dict[str, dict]:
    """Download snapshots for every stock in parallel. Returns {symbol: snapshot}."""
    snapshots: dict[str, dict] = {}
    # A thread pool runs several downloads at once - most time is spent waiting
    # on the network, so threads give a big speed-up here.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(get_snapshot, stock): stock["symbol"] for stock in universe}
        for i, future in enumerate(as_completed(futures), 1):
            snap = future.result()
            if snap:
                snapshots[futures[future]] = snap
            if i % 25 == 0 or i == len(futures):
                log.info("  data collected for %d/%d stocks", i, len(futures))
    return snapshots


def load_cached_snapshot(symbol: str) -> dict | None:
    """Used by agent tools: read today's cached snapshot without network access."""
    cache_file = config.DATA_DIR / config.today_ist() / f"{symbol}.json"
    return json.loads(cache_file.read_text()) if cache_file.exists() else None


def recent_news(symbol: str, limit: int = 8) -> list[dict]:
    """Latest headlines Yahoo associates with the stock (quality varies)."""
    try:
        items = yf.Ticker(f"{symbol}.NS").news or []
    except Exception as exc:
        log.warning("News fetch failed for %s: %s", symbol, exc)
        return []
    news = []
    for item in items[:limit]:
        content = item.get("content", item)
        news.append(
            {
                "title": content.get("title"),
                "summary": (content.get("summary") or "")[:300],
                "published": content.get("pubDate"),
                "source": (content.get("provider") or {}).get("displayName"),
            }
        )
    return news
