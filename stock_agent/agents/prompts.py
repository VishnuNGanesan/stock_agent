"""
Loads agent instructions ("grounding files") from stock_agent/prompts/*.md.

Each agent's system prompt = its role file + the shared investment_policy.md.

Keeping prompts in markdown files instead of Python strings means:
  * you can tune agent behaviour by editing plain English,
  * every agent follows the same policy (one source of truth),
  * prompt changes are easy to review (`git diff` shows exactly what changed).

The files are read fresh on every run, so edits take effect on the next run.
The text does not change during a run, so prompt caching keeps it cheap even
though it is re-sent with every API call.
"""

import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _read(name: str) -> str:
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    # Drop <!-- comments --> meant for you, the human editor; Claude doesn't need them.
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


def load_system_prompt(role: str) -> str:
    """e.g. load_system_prompt("analyst") -> analyst.md + investment_policy.md"""
    return f"{_read(role)}\n\n---\n\n{_read('investment_policy')}"
