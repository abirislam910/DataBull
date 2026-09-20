"""The Anthropic wrapper, behind a protocol narrow enough to fake.

`LLMClient` exposes exactly one operation — stream one turn — and yields a tiny
union of our own types rather than the SDK's. That is what lets the eval suite
and the unit tests run the real `run_agent` loop with no API key, no network,
and no recorded cassette, which CLAUDE.md requires ("never make Anthropic API
calls in tests without a recorded fixture").
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Protocol, cast

from app.core.config import get_settings

if TYPE_CHECKING:
    from anthropic.types import MessageParam, ToolUnionParam
    from anthropic.types.output_config_param import OutputConfigParam

# Published per-MTok rates, used for the `cost_usd` in the Done event. Kept
# beside the client because it is the only place that knows which model ran.
# Rates change; a wrong number here is a wrong number in the UI, not a bug.
PRICING_USD_PER_MTOK: Final[dict[str, tuple[float, float]]] = {
    # model: (input, output)
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Cost of one turn, or 0.0 for a model we have no published rate for.

    Returning zero rather than guessing keeps a stale table from inventing
    plausible-looking numbers — a zero is obviously missing data.
    """
    rates = PRICING_USD_PER_MTOK.get(model)
    if rates is None:
        return 0.0
    input_rate, output_rate = rates
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


@dataclass(frozen=True)
class ToolCall:
    """A tool the model asked for, with its id so results can be matched back."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class TextChunk:
    """Streamed prose."""

    text: str


@dataclass(frozen=True)
class TurnComplete:
    """End of one model turn: what it said, what it wants to call, what it cost."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str | None = None


LLMEvent = TextChunk | TurnComplete


class LLMClient(Protocol):
    """One turn of conversation, streamed."""

    @property
    def model(self) -> str: ...

    def stream_turn(
        self,
        *,
        system: str,
        # Anthropic message dicts. Typed loosely because the shape is the
        # provider's (text blocks, tool_use blocks, tool_result blocks) and
        # restating it here as TypedDicts would be a second contract to keep in
        # sync with the SDK for no checking benefit.
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[LLMEvent]: ...


class LLMConfigurationError(RuntimeError):
    """No API key configured. Surfaced as an error event, never a crash."""


class AnthropicLLMClient:
    """`LLMClient` backed by the Anthropic Messages API.

    Streams because SPEC's chat contract is token-by-token, and because a
    streamed request cannot trip the SDK's long-request timeout guard.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        key = api_key if api_key is not None else settings.anthropic_api_key
        if not key:
            raise LLMConfigurationError(
                "ANTHROPIC_API_KEY is not set; the assistant is unavailable."
            )
        # Imported here rather than at module scope so importing the agent
        # package stays cheap and dependency-light for the tests that never
        # touch the network.
        from anthropic import AsyncAnthropic

        self._model = model or settings.agent_model
        self._settings = settings
        self._client = AsyncAnthropic(
            api_key=key,
            timeout=settings.agent_llm_timeout_seconds,
        )

    @property
    def model(self) -> str:
        return self._model

    async def stream_turn(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[LLMEvent]:
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=self._settings.agent_max_tokens,
            # `effort` rather than `temperature`: sampling parameters are
            # rejected by current-generation models. Low effort suits four
            # narrow tools and a three-sentence answer budget.
            #
            # The three casts below are the SDK boundary. Our protocol carries
            # plain dicts so the test fakes stay trivial; the SDK types them as
            # TypedDicts, which a `dict[str, Any]` can never structurally
            # satisfy. Shapes are verified by the integration test, not here.
            output_config=cast(
                "OutputConfigParam", {"effort": self._settings.agent_effort}
            ),
            system=[
                {
                    "type": "text",
                    "text": system,
                    # The system prompt and tool list are byte-identical on every
                    # turn, so caching the prefix makes each follow-up in a
                    # conversation markedly cheaper.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=cast("list[ToolUnionParam]", list(tools)),
            messages=cast("list[MessageParam]", list(messages)),
        ) as stream:
            async for text in stream.text_stream:
                yield TextChunk(text=text)

            final = await stream.get_final_message()

        text = "".join(block.text for block in final.content if block.type == "text")
        tool_calls = [
            ToolCall(
                id=block.id,
                name=block.name,
                # SDK tool inputs are already parsed JSON; never string-match
                # the serialized form (escaping varies by model).
                input=dict(block.input) if isinstance(block.input, dict) else {},
            )
            for block in final.content
            if block.type == "tool_use"
        ]
        yield TurnComplete(
            text=text,
            tool_calls=tool_calls,
            input_tokens=final.usage.input_tokens,
            output_tokens=final.usage.output_tokens,
            stop_reason=final.stop_reason,
        )
