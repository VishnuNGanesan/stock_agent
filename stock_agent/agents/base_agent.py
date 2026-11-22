"""
THE AGENT LOOP - the heart of "agentic AI". Read this file first.

An LLM by itself can only produce text. It becomes an *agent* when we:
  1. give it a goal (the task),
  2. give it tools (Python functions it may request),
  3. run a loop: ask the model -> run the tools it requested -> send results
     back -> ask again ... until it decides it is done.

The model, not our code, decides WHICH tools to call, in WHAT order and
WHEN it has enough information. That autonomy is what separates an agent
from a fixed script.

    ┌──────────── task ────────────┐
    ▼                              │
  Claude ──"call get_fundamentals"──► our Python runs the function
    ▲                                         │
    └──────────── tool result (JSON) ◄────────┘
           ... repeats until Claude calls the "submit" tool

To get a structured answer back, every agent has a special *finish tool*
(e.g. `submit_research_verdict`). Its JSON schema defines the answer format;
when Claude calls it, we take its input as the final result and stop.
"""

import json
import logging
from dataclasses import dataclass, field

from stock_agent import config
from stock_agent.agents import llm
from stock_agent.agents.tools import Tool

log = logging.getLogger(__name__)


@dataclass
class AgentResult:
    output: dict | None            # the arguments Claude passed to the finish tool
    turns: int                     # how many round-trips to Claude it took
    tool_calls: list[str] = field(default_factory=list)
    stop_reason: str = "finished"


class Agent:
    def __init__(self, name: str, system_prompt: str, tools: list[Tool], finish_tool: Tool,
                 max_turns: int = config.MAX_AGENT_TURNS):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = {tool.name: tool for tool in tools}   # lookup table: name -> Tool
        self.finish_tool = finish_tool
        self.max_turns = max_turns

    # ------------------------------------------------------------------ loop
    def run(self, task: str, label: str = "") -> AgentResult:
        messages: list = [{"role": "user", "content": task}]
        api_tools = [t.to_api() for t in self.tools.values()] + [self.finish_tool.to_api()]
        calls_made: list[str] = []
        reminded = False

        for turn in range(1, self.max_turns + 1):
            response = llm.chat(self.system_prompt, messages, api_tools)
            # Always store Claude's full reply (text, thinking AND tool calls) in the history.
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                log.warning("[%s] Claude declined this request", self.name)
                return self._finish(None, turn, calls_made, "refusal", messages, label)
            if response.stop_reason == "max_tokens":
                log.warning("[%s] reply was cut off (max_tokens)", self.name)
                return self._finish(None, turn, calls_made, "max_tokens", messages, label)
            if response.stop_reason == "pause_turn":
                continue  # server asked us to simply send the conversation again

            tool_requests = [block for block in response.content if block.type == "tool_use"]

            if not tool_requests:
                # Claude answered in plain text instead of using the finish tool. Remind it once.
                if reminded:
                    return self._finish(None, turn, calls_made, "no_submission", messages, label)
                reminded = True
                messages.append({"role": "user", "content":
                                 f"Please submit your final answer by calling `{self.finish_tool.name}`."})
                continue

            # Run every tool Claude asked for, then send ALL results back in one message.
            results, final_output = [], None
            for request in tool_requests:
                calls_made.append(request.name)
                if request.name == self.finish_tool.name:
                    final_output = request.input
                    results.append(_tool_result(request.id, "Submitted. Thank you."))
                else:
                    results.append(self._execute(request))
            messages.append({"role": "user", "content": results})

            if final_output is not None:
                return self._finish(final_output, turn, calls_made, "finished", messages, label)

        log.warning("[%s] hit the %d-turn limit without finishing", self.name, self.max_turns)
        return self._finish(None, self.max_turns, calls_made, "turn_limit", messages, label)

    # --------------------------------------------------------------- helpers
    def _execute(self, request) -> dict:
        """Run one tool. Errors go back to Claude (is_error) so it can recover."""
        tool = self.tools.get(request.name)
        args = ", ".join(f"{k}={v!r}" for k, v in request.input.items())
        log.info("    [%s] 🔧 %s(%s)", self.name, request.name, args)
        if tool is None:
            return _tool_result(request.id, f"Unknown tool {request.name}", is_error=True)
        try:
            output = tool.function(**request.input)
            return _tool_result(request.id, json.dumps(output, default=str))
        except Exception as exc:
            log.info("    [%s] ⚠️  tool error: %s", self.name, exc)
            return _tool_result(request.id, f"Error: {exc}", is_error=True)

    def _finish(self, output, turns, calls, reason, messages, label) -> AgentResult:
        _save_transcript(self.name, label, messages)
        return AgentResult(output=output, turns=turns, tool_calls=calls, stop_reason=reason)


def _tool_result(tool_use_id: str, content: str, is_error: bool = False) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": is_error}


def _save_transcript(agent_name: str, label: str, messages: list) -> None:
    """Write the whole conversation to logs/<date>/ so you can study what the agent did."""
    folder = config.LOGS_DIR / config.today_ist()
    folder.mkdir(exist_ok=True)

    def to_plain(item):
        if hasattr(item, "model_dump"):            # SDK objects (pydantic models)
            return item.model_dump(exclude_none=True)
        if isinstance(item, list):
            return [to_plain(i) for i in item]
        if isinstance(item, dict):
            return {k: to_plain(v) for k, v in item.items()}
        return item

    name = f"{agent_name.lower().replace(' ', '_')}{'_' + label if label else ''}.json"
    (folder / name).write_text(json.dumps(to_plain(messages), indent=2, default=str))
