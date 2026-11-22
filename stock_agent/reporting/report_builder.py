"""
Turns the pipeline result into an HTML report (the email body) and a CSV of
every screened stock (the email attachment).

Email clients ignore most <style> blocks, so styles are written inline.
"""

import csv
from html import escape

from stock_agent import config

DECISION_COLOURS = {
    "STRONG_BUY": "#0b7a3e", "BUY": "#1f9d55", "ACCUMULATE": "#5fa83a",
    "HOLD": "#b7950b", "AVOID": "#c0392b", "SELL": "#8e1b10",
}
CELL = "padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;"
HEAD = CELL + "background:#f3f4f6;text-align:left;font-weight:600;"


def _pct(v, digits=1):
    return "–" if v is None else f"{v * 100:.{digits}f}%"


def _num(v, digits=1):
    return "–" if v is None else f"{v:,.{digits}f}"


def _badge(decision: str) -> str:
    colour = DECISION_COLOURS.get(decision, "#555")
    label = escape(decision.replace("_", " "))
    return (f'<span style="background:{colour};color:#fff;padding:2px 8px;border-radius:10px;'
            f'font-size:12px;font-weight:600;white-space:nowrap">{label}</span>')


def _list(items) -> str:
    if not items:
        return "<em>–</em>"
    return "<ul style='margin:4px 0 4px 18px;padding:0'>" + "".join(
        f"<li style='margin:2px 0'>{escape(str(i))}</li>" for i in items) + "</ul>"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th style='{HEAD}'>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td style='{CELL}'>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table style='border-collapse:collapse;width:100%;margin:8px 0 16px'>{head}{body}</table>"


def _section(title: str, content: str) -> str:
    return (f"<h2 style='font-size:17px;margin:26px 0 8px;color:#111827;border-bottom:2px solid "
            f"#2563eb;padding-bottom:4px'>{title}</h2>{content}")


def _picks(picks: list[dict]) -> str:
    if not picks:
        return "<p><em>None today.</em></p>"
    return _table(["Stock", "Action", "Why"],
                  [[f"<b>{escape(p['symbol'])}</b>", _badge(p["decision"]), escape(p["reason"])]
                   for p in picks])


def _verdict_card(v: dict) -> str:
    changed = "" if v.get("agrees_with_analyst", True) else (
        f" <span style='color:#b45309;font-size:12px'>(analyst said "
        f"{escape(v['analyst_decision'])}, reviewer changed it)</span>")
    prev = (f"<div style='font-size:12px;color:#6b7280'>Previous call: {escape(v['previous_decision'])}</div>"
            if v.get("previous_decision") else "")
    holding = " <span style='font-size:11px;color:#2563eb'>● YOU OWN</span>" if v["is_holding"] else ""
    return f"""
<div style="border:1px solid #e5e7eb;border-radius:8px;padding:12px 14px;margin:12px 0">
  <div style="font-size:16px;font-weight:700">{escape(v['name'])} ({escape(v['symbol'])}){holding}</div>
  <div style="margin:6px 0">{_badge(v['final_decision'])} conviction <b>{v['final_conviction']}/10</b>{changed}</div>
  <div style="font-size:12px;color:#6b7280">{escape(v['segment'])} · {escape(v['sector'])} ·
    ₹{_num(v['price'])} · P/E {_num(v['pe'])} · screen score {v['screen_score']}/100 ·
    valuation {escape(v.get('valuation_view', '–'))} · earnings {escape(v.get('earnings_momentum', '–'))}</div>
  {prev}
  <p style="font-size:13px;margin:8px 0"><b>Thesis:</b> {escape(v.get('investment_thesis', ''))}</p>
  <table style="width:100%;font-size:13px"><tr>
    <td style="vertical-align:top;width:50%"><b>Strengths</b>{_list(v.get('key_strengths'))}</td>
    <td style="vertical-align:top"><b>Risks</b>{_list(v.get('key_risks'))}</td></tr></table>
  <p style="font-size:13px;margin:6px 0"><b>How to act:</b> {escape(v.get('entry_strategy', ''))}</p>
  <p style="font-size:13px;margin:6px 0"><b>Risk review:</b> {escape(v.get('review_summary', ''))}</p>
  {_list(v.get('challenges')) if v.get('challenges') else ''}
  <p style="font-size:12px;color:#6b7280;margin:6px 0"><b>Would change view if:</b>
    {escape(v.get('what_would_change_my_mind', ''))}</p>
  <p style="font-size:11px;color:#9ca3af;margin:4px 0">Shortlisted because: {escape(v['selection_reason'])}</p>
</div>"""


def build_html(result: dict) -> str:
    brief, breadth, verdicts = result["brief"], result["breadth"], result["verdicts"]
    order = {d: i for i, d in enumerate(DECISION_COLOURS)}
    verdicts = sorted(verdicts, key=lambda v: (order.get(v["final_decision"], 9), -v["final_conviction"]))

    summary_rows = [[f"<b>{escape(v['symbol'])}</b>", escape(v["segment"]), _badge(v["final_decision"]),
                     f"{v['final_conviction']}/10", str(v["screen_score"]), _num(v["price"]), _num(v["pe"])]
                    for v in verdicts]

    top = result["scores"][:25]
    screen_rows = [[str(i + 1), f"<b>{escape(s['symbol'])}</b>", escape(s["segment"]),
                    escape(s["sector"]), f"<b>{s['score']}</b>",
                    "–" if s.get("score_change") is None else f"{s['score_change']:+.1f}",
                    " / ".join(f"{p:.0f}" for p in s["pillars"].values()),
                    _num(s["pe"]), _pct(s["roe"], 0), escape("; ".join(s["red_flags"])) or "–"]
                   for i, s in enumerate(top)]

    holdings_flags = [s for s in result["scores"] if s["symbol"] in config.MY_HOLDINGS and s["red_flags"]]
    usage = result["usage"]
    minutes = (result["finished"] - result["started"]).total_seconds() / 60

    breadth_html = _table(["Measure", "Value"], [
        ["Stocks analysed", str(breadth["stocks_analysed"])],
        ["Above 200-day average", f"{breadth['pct_above_200dma']}%"],
        ["Median 1-month / 6-month return", f"{breadth['median_return_1m_pct']}% / {breadth['median_return_6m_pct']}%"],
        ["Advancers / decliners (last session)", f"{breadth['advancers_today']} / {breadth['decliners_today']}"],
        ["Ratings (Strong / Good / Neutral / Weak)", " / ".join(str(v) for v in breadth["rating_counts"].values())],
        ["Strongest sectors", escape(", ".join(f"{s} ({v})" for s, v in breadth["strongest_sectors_by_avg_score"]))],
        ["Weakest sectors", escape(", ".join(f"{s} ({v})" for s, v in breadth["weakest_sectors_by_avg_score"]))],
    ])

    parts = [
        f"<div style='font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:860px;"
        f"margin:auto;color:#1f2937;line-height:1.45'>",
        f"<div style='background:#1e3a8a;color:#fff;padding:16px 18px;border-radius:8px'>"
        f"<div style='font-size:12px;opacity:.8'>Nifty Large &amp; Mid Cap · Daily AI Investment Report · {result['run_date']}</div>"
        f"<div style='font-size:20px;font-weight:700;margin-top:4px'>{escape(brief['headline'])}</div></div>",
        _section("Market backdrop", f"<p style='font-size:14px'>{escape(brief['market_mood'])}</p>"),
        _section("Executive summary", f"<p style='font-size:14px'>{escape(brief['executive_summary'])}</p>"),
        _section("Top picks", _picks(brief.get("top_picks", []))),
    ]
    if config.MY_HOLDINGS:
        parts.append(_section("Your holdings", _picks(brief.get("holdings_actions", []))))
    if brief.get("portfolio_guidance"):
        parts.append(_section("Portfolio guidance", f"<p style='font-size:14px'>{escape(brief['portfolio_guidance'])}</p>"))
    if brief.get("watchlist"):
        parts.append(_section("Watchlist", f"<p style='font-size:14px'>{escape(', '.join(brief['watchlist']))}</p>"))
    if brief.get("changes_since_last_run"):
        parts.append(_section("What changed", f"<p style='font-size:14px'>{escape(brief['changes_since_last_run'])}</p>"))
    if holdings_flags:
        parts.append(_section("⚠️ Red flags on your holdings", _table(
            ["Stock", "Flags"], [[escape(s["symbol"]), escape("; ".join(s["red_flags"]))] for s in holdings_flags])))
    parts += [
        _section("All verdicts", _table(["Stock", "Segment", "Decision", "Conviction", "Score", "Price ₹", "P/E"],
                                        summary_rows)),
        _section("Detailed analysis", "".join(_verdict_card(v) for v in verdicts)),
        _section("Market breadth", breadth_html),
        _section("Screener top 25",
                 "<p style='font-size:12px;color:#6b7280'>Pillars = Quality/25 · Growth/25 · Valuation/20 · "
                 "Health/15 · Momentum/15. Δ = change since previous run.</p>" +
                 _table(["#", "Stock", "Segment", "Sector", "Score", "Δ", "Pillars", "P/E", "ROE", "Red flags"],
                        screen_rows)),
        f"<p style='font-size:11px;color:#9ca3af;margin-top:24px'>Run took {minutes:.1f} min · "
        f"{'model ' + escape(result['model']) + f' · {usage.calls} API calls · ' if result['used_llm'] else 'no AI used · '}"
        f"{'≈ $' + format(usage.cost_usd(), '.2f') + ' API cost · ' if result['used_llm'] else ''}"
        f"{'Failed: ' + ', '.join(result['failed']) + ' · ' if result['failed'] else ''}"
        f"Data: NSE index lists, Yahoo Finance (may be delayed or incomplete).</p>",
        "<p style='font-size:11px;color:#9ca3af'>This report is generated automatically by software "
        "for personal research and education. It is not investment advice and is not from a "
        "SEBI-registered adviser. Verify data and consult a professional before investing.</p></div>",
    ]
    return "\n".join(parts)


def save_report(result: dict) -> tuple:
    html_path = config.REPORTS_DIR / f"report_{result['run_date']}.html"
    html_path.write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>Stock report "
                         f"{result['run_date']}</title></head><body>{build_html(result)}</body></html>")

    csv_path = config.REPORTS_DIR / f"screener_{result['run_date']}.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rank", "symbol", "name", "segment", "sector", "score", "rating", "change",
                         *[f"{p}_pts" for p in ("quality", "growth", "valuation", "health", "momentum")],
                         "price", "pe", "sector_pe", "eps_ttm", "roe", "red_flags"])
        for i, s in enumerate(result["scores"], 1):
            writer.writerow([i, s["symbol"], s["name"], s["segment"], s["sector"], s["score"], s["rating"],
                             s.get("score_change"), *s["pillars"].values(), s["price"], s["pe"],
                             s["sector_pe"], s["eps_ttm"], s["roe"], "; ".join(s["red_flags"])])
    return html_path, csv_path
