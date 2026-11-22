"""
Central configuration.

Every setting can be overridden from the `.env` file in the project root, so
you never have to edit code to change behaviour (or to store secrets).

Python lesson: `os.getenv("NAME", default)` reads an environment variable and
returns `default` when it is missing. `python-dotenv`'s `load_dotenv()` copies
the key=value lines from `.env` into the environment first.
"""

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Path(__file__) is this file; .parent twice climbs to the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------- folders
DATA_DIR = PROJECT_ROOT / "data_cache"      # downloaded market data (per day)
REPORTS_DIR = PROJECT_ROOT / "reports"      # generated HTML reports
LOGS_DIR = PROJECT_ROOT / "logs"            # agent transcripts + run logs
DB_PATH = PROJECT_ROOT / "stock_agent.db"   # SQLite "long-term memory"

for _folder in (DATA_DIR, REPORTS_DIR, LOGS_DIR):
    _folder.mkdir(exist_ok=True)

# ---------------------------------------------------------------- universe
# Official NSE constituent lists (refreshed by NSE twice a year).
INDEX_SOURCES = {
    "Large Cap": "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv",
    "Mid Cap": "https://nsearchives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
}

# Stocks you already own. They are ALWAYS researched by the agents, even if
# their screening score is low, so you get a hold / exit view on them.
MY_HOLDINGS = _list("MY_HOLDINGS")          # e.g. "TCS,HDFCBANK,POLYCAB"

# ---------------------------------------------------------------- AI agents
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
# effort = how hard Claude thinks: low | medium | high | xhigh | max
AGENT_EFFORT = os.getenv("AGENT_EFFORT", "medium")
MAX_AGENT_TURNS = _int("MAX_AGENT_TURNS", 12)        # safety stop for the loop
TOP_N_TO_RESEARCH = _int("TOP_N_TO_RESEARCH", 8)     # best screened stocks sent to agents
MAX_BIG_MOVERS = _int("MAX_BIG_MOVERS", 3)           # stocks whose score jumped/fell a lot
AGENT_WORKERS = _int("AGENT_WORKERS", 3)             # stocks researched in parallel

# Approximate USD price per million tokens, used only to show run cost.
PRICE_PER_MTOK = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

# ---------------------------------------------------------------- email
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = _int("SMTP_PORT", 465)
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "")
EMAIL_RECIPIENT = os.getenv("EMAIL_RECIPIENT", EMAIL_SENDER)

# ---------------------------------------------------------------- schedule
# The scheduled run starts once per trading day after this IST time.
RUN_AFTER_IST = os.getenv("RUN_AFTER_IST", "18:30")

IST = ZoneInfo("Asia/Kolkata")


def today_ist() -> str:
    """Today's date in India - the market's calendar, wherever your computer is."""
    return datetime.now(IST).date().isoformat()
