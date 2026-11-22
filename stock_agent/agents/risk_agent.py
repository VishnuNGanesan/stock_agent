"""
Agent 2 - Risk Reviewer (the "critic").

Multi-agent pattern: one agent proposes, a second agent with a DIFFERENT role
checks the work. The reviewer gets the analyst's verdict plus the same data
tools, so it can verify claims instead of trusting them. Two perspectives
catch more mistakes (over-optimism, misread numbers) than one.
"""

import json

from stock_agent.agents.base_agent import Agent
from stock_agent.agents.prompts import load_system_prompt
from stock_agent.agents.research_agent import DECISIONS
from stock_agent.agents.tools import MarketToolbox, Tool, object_schema

FINISH_TOOL = Tool(
    name="submit_risk_review",
    description="Submit your review and the final decision. Call exactly once.",
    input_schema=object_schema({
        "agrees_with_analyst": {"type": "boolean"},
        "final_decision": {"type": "string", "enum": DECISIONS},
        "final_conviction": {"type": "integer", "description": "1 (low) to 10 (very high)"},
        "challenges": {"type": "array", "items": {"type": "string"},
                       "description": "Specific issues found, each citing data"},
        "review_summary": {"type": "string", "description": "2-3 sentences"},
    }),
)


def build_risk_agent(toolbox: MarketToolbox) -> Agent:
    # The reviewer can check the same data, minus the memory and news tools (kept cheaper).
    allowed = {"get_fundamentals", "get_quarterly_results", "get_price_technicals",
               "compare_with_sector_peers"}
    tools = [t for t in toolbox.research_tools() if t.name in allowed]
    return Agent("Risk Reviewer", load_system_prompt("reviewer"), tools, FINISH_TOOL, max_turns=8)


def review_task(symbol: str, name: str, analyst_verdict: dict) -> str:
    return (f"Review the analyst's verdict on {name} (NSE: {symbol}):\n\n"
            f"{json.dumps(analyst_verdict, indent=2)}")
