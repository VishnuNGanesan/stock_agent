"""
Entry point. Examples:

    python main.py                         # full run: screen + AI agents + email
    python main.py --no-llm --no-email     # free test run, no API key needed
    python main.py --symbols TCS,INFY      # research only these stocks
    python main.py --limit 30 --top 2      # quick, cheap trial on 30 stocks
    python main.py --scheduled             # used by the scheduler (runs once per trading day)
"""

import argparse
import logging
import sys
from datetime import datetime

from stock_agent import config
from stock_agent.notify.emailer import send_report
from stock_agent.orchestrator import run_pipeline
from stock_agent.reporting.report_builder import build_html, save_report
from stock_agent.storage import db

def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(config.LOGS_DIR / "run.log")],
    )
    for noisy in ("yfinance", "httpx", "httpx2", "urllib3", "peewee", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def should_run_now() -> bool:
    """Gate for scheduled runs: weekday, after market close (IST), not already done today."""
    now = datetime.now(config.IST)
    hour, minute = map(int, config.RUN_AFTER_IST.split(":"))
    if now.weekday() >= 5:
        return False
    if (now.hour, now.minute) < (hour, minute):
        return False
    return not db.run_completed(now.date().isoformat())


def main() -> None:
    parser = argparse.ArgumentParser(description="Agentic Indian stock monitor")
    parser.add_argument("--no-llm", action="store_true", help="skip AI agents (free, rule-based)")
    parser.add_argument("--no-email", action="store_true", help="do not send the email")
    parser.add_argument("--top", type=int, help="how many top-scored stocks the agents research")
    parser.add_argument("--limit", type=int, help="only monitor the first N stocks (for testing)")
    parser.add_argument("--symbols", help="comma-separated symbols to research, e.g. TCS,INFY")
    parser.add_argument("--scheduled", action="store_true", help="run only if due (for launchd)")
    args = parser.parse_args()

    setup_logging()
    log = logging.getLogger("main")
    if args.scheduled and not should_run_now():
        return  # the scheduler wakes us every hour; quietly exit when nothing is due

    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    result = run_pipeline(use_llm=not args.no_llm, top_n=args.top,
                          universe_limit=args.limit, only_symbols=symbols)

    html_path, csv_path = save_report(result)
    log.info("📄 Report saved: %s", html_path)

    emailed = False
    if not args.no_email:
        picks = ", ".join(p["symbol"] for p in result["brief"].get("top_picks", [])[:3])
        subject = f"📈 Stock report {result['run_date']}: {result['brief']['headline']}"
        if picks:
            subject += f" | Top: {picks}"
        emailed = send_report(subject, build_html(result), [csv_path])

    usage = result["usage"]
    # Full-universe runs (with or without the AI agents) count towards "already ran
    # today"; --limit / --symbols trial runs do not.
    official_run = not (args.limit or symbols)
    if official_run:
        db.save_run(
            result["run_date"],
            started_at=result["started"].isoformat(timespec="seconds"),
            stocks_screened=len(result["scores"]), stocks_researched=len(result["verdicts"]),
            input_tokens=usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens,
            output_tokens=usage.output_tokens, cost_usd=round(usage.cost_usd(), 3),
            report_path=str(html_path))
    if result["used_llm"]:
        log.info("💰 %d Claude calls, ≈ $%.2f", usage.calls, usage.cost_usd())
    log.info("✅ Done%s", " (emailed)" if emailed else "")


if __name__ == "__main__":
    main()
