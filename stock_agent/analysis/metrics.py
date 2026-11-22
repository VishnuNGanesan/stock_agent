"""
Pure calculation helpers - no network, no AI. Plain numbers in, numbers out.

Keeping maths separate from data fetching makes it easy to test and reason
about (a good habit in any Python project).
"""

from datetime import date


def _pct_change(new, old):
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old)


# ------------------------------------------------------------------ prices
def _sma(values: list[float], window: int):
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _rsi(closes: list[float], period: int = 14):
    """Relative Strength Index: >70 'overbought', <30 'oversold'."""
    if len(closes) <= period:
        return None
    gains, losses = [], []
    for prev, cur in zip(closes[-period - 1:-1], closes[-period:]):
        change = cur - prev
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain, avg_loss = sum(gains) / period, sum(losses) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _return_over(closes: list[float], trading_days: int):
    if len(closes) <= trading_days:
        return None
    return _pct_change(closes[-1], closes[-trading_days - 1])


def compute_technicals(closes: list[float]) -> dict:
    """Price-based indicators from ~1 year of daily closing prices."""
    if not closes:
        return {}
    price = closes[-1]
    high_52w, low_52w = max(closes), min(closes)
    sma50, sma200 = _sma(closes, 50), _sma(closes, 200)

    # Annualised volatility from daily returns (a rough risk gauge).
    daily = [(b - a) / a for a, b in zip(closes[:-1], closes[1:]) if a]
    if len(daily) > 20:
        mean = sum(daily) / len(daily)
        variance = sum((r - mean) ** 2 for r in daily) / (len(daily) - 1)
        volatility = (variance ** 0.5) * (252 ** 0.5)
    else:
        volatility = None

    return {
        "price": price,
        "sma50": sma50,
        "sma200": sma200,
        "above_sma200": (price > sma200) if sma200 else None,
        "pct_vs_sma200": _pct_change(price, sma200),
        "rsi14": _rsi(closes),
        "high_52w": high_52w,
        "low_52w": low_52w,
        "pct_from_52w_high": _pct_change(price, high_52w),
        "return_1m": _return_over(closes, 21),
        "return_3m": _return_over(closes, 63),
        "return_6m": _return_over(closes, 126),
        "return_1y": _pct_change(price, closes[0]) if len(closes) > 200 else None,
        "volatility": volatility,
        "day_change": _pct_change(closes[-1], closes[-2]) if len(closes) > 1 else None,
    }


# ---------------------------------------------------------------- quarters
def _year_ago(quarters: list[dict]):
    """Find the quarter ~1 year before the latest one (Yahoo sometimes skips quarters)."""
    latest = date.fromisoformat(quarters[0]["period"])
    for q in quarters[1:]:
        gap = (latest - date.fromisoformat(q["period"])).days
        if 330 <= gap <= 400:
            return q
    return None


def summarise_quarters(quarters: list[dict]) -> dict:
    """Turn raw quarterly rows (newest first) into growth signals."""
    if not quarters:
        return {}
    latest = quarters[0]
    prev = quarters[1] if len(quarters) > 1 else {}
    year_ago = _year_ago(quarters) or {}

    eps_series = [q["eps"] for q in quarters if q.get("eps") is not None]
    growing_streak = 0  # how many consecutive quarters EPS rose, counting back from latest
    for newer, older in zip(eps_series, eps_series[1:]):
        if newer > older:
            growing_streak += 1
        else:
            break

    margin_now, margin_then = latest.get("operating_margin"), year_ago.get("operating_margin")
    # Yahoo occasionally stops updating a company's quarterly data. Flag it so
    # neither the screener nor the agents treat old numbers as current.
    age_days = (date.today() - date.fromisoformat(latest["period"])).days
    return {
        "latest_period": latest["period"],
        "stale": age_days > 200,
        "days_since_quarter_end": age_days,
        "revenue_yoy": _pct_change(latest.get("revenue"), year_ago.get("revenue")),
        "net_income_yoy": _pct_change(latest.get("net_income"), year_ago.get("net_income")),
        "eps_yoy": _pct_change(latest.get("eps"), year_ago.get("eps")),
        "revenue_qoq": _pct_change(latest.get("revenue"), prev.get("revenue")),
        "eps_qoq": _pct_change(latest.get("eps"), prev.get("eps")),
        "operating_margin_latest": margin_now,
        "operating_margin_change_yoy": (
            margin_now - margin_then if margin_now is not None and margin_then is not None else None
        ),
        "eps_growth_streak": growing_streak,
        "negative_eps_quarters": sum(1 for e in eps_series if e < 0),
        "eps_history_oldest_first": list(reversed(eps_series)),
    }
