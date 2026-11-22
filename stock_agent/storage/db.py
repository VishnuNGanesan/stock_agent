"""
SQLite database = the system's long-term memory.

LLMs forget everything between calls. To let agents say things like
"score improved from 58 to 71 since last week" or "we said BUY 10 days ago",
we store every day's scores and verdicts here and give agents a tool to read it.

SQLite is a full SQL database stored in a single file (stock_agent.db) and
comes built into Python - no server to install.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from stock_agent import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_scores (
    run_date TEXT, symbol TEXT, score REAL, rating TEXT, price REAL, pe REAL,
    pillars TEXT, red_flags TEXT,
    PRIMARY KEY (run_date, symbol)
);
CREATE TABLE IF NOT EXISTS verdicts (
    run_date TEXT, symbol TEXT, decision TEXT, conviction INTEGER,
    analyst_decision TEXT, price REAL, details TEXT,
    PRIMARY KEY (run_date, symbol)
);
CREATE TABLE IF NOT EXISTS runs (
    run_date TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT,
    stocks_screened INTEGER, stocks_researched INTEGER,
    input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, report_path TEXT
);
"""


@contextmanager
def connect():
    """`with connect() as db:` opens the DB, commits on success, always closes."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["score"]
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def today() -> str:
    return config.today_ist()


# ------------------------------------------------------------------ scores
def save_scores(scores: list[dict], run_date: str | None = None) -> None:
    run_date = run_date or today()
    with connect() as db:
        db.executemany(
            "INSERT OR REPLACE INTO daily_scores VALUES (?,?,?,?,?,?,?,?)",
            [
                (run_date, r["symbol"], r["score"], r["rating"], r["price"], r["pe"],
                 json.dumps(r["pillars"]), json.dumps(r["red_flags"]))
                for r in scores
            ],
        )


def score_history(symbol: str, limit: int = 30) -> list[dict]:
    with connect() as db:
        rows = db.execute(
            "SELECT run_date, score, rating, price, pe FROM daily_scores "
            "WHERE symbol=? ORDER BY run_date DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def previous_scores(before: str | None = None) -> dict[str, float]:
    """Scores from the most recent run before `before` (default: today)."""
    before = before or today()
    with connect() as db:
        row = db.execute(
            "SELECT MAX(run_date) AS d FROM daily_scores WHERE run_date < ?", (before,)
        ).fetchone()
        if not row["d"]:
            return {}
        rows = db.execute(
            "SELECT symbol, score FROM daily_scores WHERE run_date=?", (row["d"],)
        ).fetchall()
    return {r["symbol"]: r["score"] for r in rows}


# ---------------------------------------------------------------- verdicts
def save_verdict(verdict: dict, run_date: str | None = None) -> None:
    with connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO verdicts VALUES (?,?,?,?,?,?,?)",
            (run_date or today(), verdict["symbol"], verdict["final_decision"],
             verdict["final_conviction"], verdict.get("analyst_decision"),
             verdict.get("price"), json.dumps(verdict)),
        )


def last_verdicts(symbol: str, limit: int = 3, before: str | None = None) -> list[dict]:
    with connect() as db:
        rows = db.execute(
            "SELECT run_date, decision, conviction, price, details FROM verdicts "
            "WHERE symbol=? AND run_date < ? ORDER BY run_date DESC LIMIT ?",
            (symbol, before or today(), limit),
        ).fetchall()
    result = []
    for r in rows:
        details = json.loads(r["details"])
        result.append({
            "run_date": r["run_date"], "decision": r["decision"],
            "conviction": r["conviction"], "price_then": r["price"],
            "thesis_then": details.get("investment_thesis", "")[:400],
        })
    return result


# -------------------------------------------------------------------- runs
def run_completed(run_date: str) -> bool:
    with connect() as db:
        row = db.execute(
            "SELECT finished_at FROM runs WHERE run_date=?", (run_date,)
        ).fetchone()
    return bool(row and row["finished_at"])


def save_run(run_date: str, **fields) -> None:
    fields.setdefault("finished_at", datetime.now().isoformat(timespec="seconds"))
    columns = ["run_date", *fields]
    with connect() as db:
        db.execute(
            f"INSERT OR REPLACE INTO runs ({','.join(columns)}) "
            f"VALUES ({','.join('?' * len(columns))})",
            (run_date, *fields.values()),
        )
