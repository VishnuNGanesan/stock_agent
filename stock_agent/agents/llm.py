"""
The ONLY file that talks to the Claude API.

Keeping the API call in one place means every agent shares the same model
settings, error handling and token/cost tracking.

Concepts used here:
  * system prompt   - the agent's "job description" (persona + rules)
  * messages        - the conversation so far (user / assistant turns)
  * tools           - JSON descriptions of Python functions Claude may ask us to run
  * adaptive thinking + effort - Claude decides how much to reason; effort caps it
  * prompt caching  - repeated prefixes (system prompt + tools + earlier turns)
                      are billed at ~10% on later calls in the same agent loop
  * refusal fallback - if a safety filter declines, the API retries on another model
"""

import logging
import threading
from dataclasses import dataclass, field

import anthropic

from stock_agent import config

log = logging.getLogger(__name__)


@dataclass
class UsageTracker:
    """Adds up tokens across every call so the report can show what a run cost."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Several agents run in parallel threads, so updates are guarded by a lock.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, usage) -> None:
        with self._lock:
            self._add(usage)

    def _add(self, usage) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

    def cost_usd(self) -> float:
        in_price, out_price = config.PRICE_PER_MTOK.get(config.ANTHROPIC_MODEL, (4.0, 20.0))
        return (
            self.input_tokens * in_price
            + self.cache_write_tokens * in_price * 1.25   # cache writes cost a bit more
            + self.cache_read_tokens * in_price * 0.10    # cache reads are ~90% cheaper
            + self.output_tokens * out_price
        ) / 1_000_000


usage = UsageTracker()
_client: anthropic.Anthropic | None = None

# Models that support the server-side refusal fallback.
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")


def get_client() -> anthropic.Anthropic:
    """Create the API client once and reuse it (it keeps connections open)."""
    global _client
    if _client is None:
        # Reads ANTHROPIC_API_KEY from the environment / .env automatically.
        # The SDK retries rate limits (429) and server errors (5xx) for us.
        _client = anthropic.Anthropic(max_retries=4, timeout=600)
    return _client


def chat(system: str, messages: list, tools: list[dict]):
    """Send the conversation to Claude and return its reply (a Message object)."""
    params = dict(
        model=config.ANTHROPIC_MODEL,
        max_tokens=16000,
        system=system,
        messages=messages,
        tools=tools,
        thinking={"type": "adaptive"},
        output_config={"effort": config.AGENT_EFFORT},
        cache_control={"type": "ephemeral"},  # automatic prompt caching
    )
    if config.ANTHROPIC_MODEL.startswith(_FALLBACK_MODELS):
        params["betas"] = ["server-side-fallback-2026-07-01"]
        params["fallbacks"] = "default"

    try:
        response = get_client().beta.messages.create(**params)
    except anthropic.AuthenticationError:
        raise SystemExit("Claude API key is missing or invalid. Set ANTHROPIC_API_KEY in .env")
    except anthropic.BadRequestError as exc:
        log.error("Claude rejected the request: %s", exc.message)
        raise
    except anthropic.APIConnectionError:
        log.error("Could not reach the Claude API - check your internet connection")
        raise

    usage.add(response.usage)
    return response
