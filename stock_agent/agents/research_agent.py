"""
Agent 1 - Research Analyst.

Goal: investigate ONE stock with the tools and submit a long-term verdict.
Its instructions live in prompts/analyst.md (+ the shared investment_policy.md).
The prompt describes WHAT a good analysis covers; the agent decides HOW
(which tools, in which order, whether to dig deeper).
"""

from stock_agent.agents.base_agent import Agent
from stock_agent.agents.prompts import load_system_prompt
from stock_agent.agents.tools import MarketToolbox, Tool, object_schema

DECISIONS = ["STRONG_BUY", "BUY", "ACCUMULATE", "HOLD", "AVOID", "SELL"]

FINISH_TOOL = Tool(
    name="submit_research_verdict",
    description="Submit your final long-term investment verdict for the stock. Call exactly once, "
                "after finishing your research.",
    input_schema=object_schema({
        "decision": {"type": "string", "enum": DECISIONS},
        "conviction": {"type": "integer", "description": "1 (low) to 10 (very high)"},
        "valuation_view": {"type": "string", "enum": ["UNDERVALUED", "FAIRLY_VALUED", "OVERVALUED"]},
        "earnings_momentum": {"type": "string",
                              "enum": ["ACCELERATING", "STEADY", "DECELERATING", "DECLINING"]},
        "investment_thesis": {"type": "string", "description": "3-5 sentences: why own (or not)"},
        "key_strengths": {"type": "array", "items": {"type": "string"}},
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "entry_strategy": {"type": "string",
                           "description": "How to act, e.g. 'buy in 3 tranches', 'wait for a dip "
                                          "near the 200-DMA (~Rs X)'"},
        "what_would_change_my_mind": {"type": "string"},
    }),
)


def build_research_agent(toolbox: MarketToolbox) -> Agent:
    return Agent("Research Analyst", load_system_prompt("analyst"), toolbox.research_tools(), FINISH_TOOL)


def research_task(symbol: str, name: str, is_holding: bool, selection_reason: str) -> str:
    owned = ("The investor ALREADY OWNS this stock - say clearly whether to keep holding, "
             "add more, or exit." if is_holding else "The investor does not own this stock yet.")
    return (f"Analyse {name} (NSE: {symbol}) for long-term investment.\n"
            f"Why it was shortlisted: {selection_reason}\n{owned}")
