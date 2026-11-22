"""
Agent 3 - Chief Strategist.

Sees the big picture: market breadth, every final verdict and the investor's
holdings, and writes the executive brief at the top of the email.

It uses the same Agent class with NO data tools, only its finish tool.
An agent with nothing but a "submit" tool is effectively a single structured
LLM call, which shows that "agent" is a spectrum, not a separate technology.
"""

import json

from stock_agent.agents.base_agent import Agent
from stock_agent.agents.prompts import load_system_prompt
from stock_agent.agents.research_agent import DECISIONS
from stock_agent.agents.tools import Tool, object_schema

_PICK = object_schema({"symbol": {"type": "string"}, "decision": {"type": "string", "enum": DECISIONS},
                       "reason": {"type": "string"}})

FINISH_TOOL = Tool(
    name="submit_market_brief",
    description="Submit the daily brief. Call exactly once.",
    input_schema=object_schema({
        "headline": {"type": "string", "description": "One line, under 15 words"},
        "market_mood": {"type": "string", "description": "2-3 sentences on breadth and trend"},
        "executive_summary": {"type": "string", "description": "One paragraph"},
        "top_picks": {"type": "array", "items": _PICK, "description": "Best 3-5 ideas, best first"},
        "holdings_actions": {"type": "array", "items": _PICK},
        "watchlist": {"type": "array", "items": {"type": "string"},
                      "description": "Symbols worth tracking but not acting on yet"},
        "portfolio_guidance": {"type": "string",
                               "description": "Diversification, allocation and staggered-buying advice"},
        "changes_since_last_run": {"type": "string"},
    }),
)


def build_strategist_agent() -> Agent:
    return Agent("Chief Strategist", load_system_prompt("strategist"), [], FINISH_TOOL, max_turns=3)


def strategist_task(breadth: dict, verdicts: list[dict], holdings: list[str],
                    decision_changes: list[str]) -> str:
    compact = [
        {k: v.get(k) for k in ("symbol", "name", "sector", "segment", "final_decision",
                               "final_conviction", "screen_score", "price", "valuation_view",
                               "earnings_momentum", "investment_thesis", "key_risks",
                               "entry_strategy", "review_summary")}
        for v in verdicts
    ]
    return (
        f"MARKET BREADTH (all monitored stocks):\n{json.dumps(breadth, indent=2)}\n\n"
        f"INVESTOR HOLDINGS: {', '.join(holdings) or 'none provided'}\n\n"
        f"DECISION CHANGES VS PREVIOUS RUNS:\n{json.dumps(decision_changes) if decision_changes else 'none / first run'}\n\n"
        f"FINAL VERDICTS FROM THE RESEARCH TEAM:\n{json.dumps(compact, indent=2, default=str)}"
    )
