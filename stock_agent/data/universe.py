"""
Builds the list of stocks to monitor: Nifty 100 (large cap) + Nifty Midcap 150.

NSE publishes the constituents as CSV files. We download them and keep a local
copy so the app still works when NSE is unreachable.
"""

import csv
import io
import logging

import requests

from stock_agent import config

log = logging.getLogger(__name__)

# NSE rejects requests without a browser-like User-Agent header.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
_CACHE_FILE = config.DATA_DIR / "universe.csv"
_FIELDS = ["symbol", "name", "industry", "segment"]


def _download(segment: str, url: str) -> list[dict]:
    response = requests.get(url, headers=_HEADERS, timeout=30)
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.text))
    return [
        {
            "symbol": row["Symbol"].strip(),
            "name": row["Company Name"].strip(),
            "industry": row["Industry"].strip(),
            "segment": segment,
        }
        for row in reader
        if row.get("Series", "EQ").strip() == "EQ"
    ]


def load_universe() -> list[dict]:
    """Return [{'symbol': 'TCS', 'name': ..., 'industry': ..., 'segment': 'Large Cap'}, ...]."""
    stocks: list[dict] = []
    try:
        for segment, url in config.INDEX_SOURCES.items():
            stocks.extend(_download(segment, url))
        with open(_CACHE_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_FIELDS)
            writer.writeheader()
            writer.writerows(stocks)
        log.info("Downloaded %d index constituents from NSE", len(stocks))
    except Exception as exc:  # network down, NSE blocked us, format changed...
        if not _CACHE_FILE.exists():
            raise RuntimeError(f"Cannot download NSE index lists and no cache exists: {exc}")
        log.warning("NSE download failed (%s); using cached universe", exc)
        with open(_CACHE_FILE) as f:
            stocks = list(csv.DictReader(f))

    # Your holdings may sit outside these indices (e.g. small caps) - add them.
    known = {s["symbol"] for s in stocks}
    for symbol in config.MY_HOLDINGS:
        if symbol not in known:
            stocks.append({"symbol": symbol, "name": symbol, "industry": "Unknown", "segment": "Holding"})
    return stocks
