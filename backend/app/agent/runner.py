"""`run_agent` — the agent module's single public function.

Runs one turn of conversation, which may involve several tool calls, and yields
events as they happen. It never raises for an LLM or tool failure: those become
`Error` events and the stream still ends with `Done`, so a caller can render a
turn that went wrong without special-casing exceptions mid-stream.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal

from pydantic import BaseModel

from app.agent.events import (
    AgentEvent,
    Done,
    Error,
    TextDelta,
    ToolResult,
    ToolUse,
    Usage,
)
from app.agent.llm_client import TextChunk, TurnComplete, estimate_cost_usd
from app.agent.prompt import DEFAULT_PROMPT_VERSION, get_prompt
from app.agent.services import AgentServices
from app.agent.tool_schemas import TOOL_SCHEMAS
from app.agent.tools import ToolContext, execute_tool
from app.core.config import get_settings


class ChatMessage(BaseModel):
    """One turn of the conversation as the client sends it."""

    role: str
    content: str


def _to_anthropic_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    """Convert the client's transcript into provider message dicts."""
    return [{"role": m.role, "content": m.content} for m in messages]


async def run_agent(
    *,
    user_id: uuid.UUID,
    messages: Sequence[ChatMessage],
    services: AgentServices,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> AsyncIterator[AgentEvent]:
    """Run one agent turn, yielding events until the model stops calling tools.

    Raises only for programmer errors — an empty transcript, or a prompt version
    that does not exist. Everything else is reported as an `Error` event.
    """
    if not messages:
        raise ValueError("run_agent requires at least one message")
    system = get_prompt(prompt_version)

    settings = get_settings()
    started = time.monotonic()
    context = ToolContext(user_id=user_id, services=services)

    conversation = _to_anthropic_messages(messages)
    input_tokens = 0
    output_tokens = 0
    tool_calls_made = 0
    model = getattr(services.llm, "model", settings.agent_model)

    def done() -> Done:
        return Done(
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=round(
                    estimate_cost_usd(model, input_tokens, output_tokens), 6
                ),
                latency_ms=int((time.monotonic() - started) * 1000),
            ),
            prompt_version=prompt_version,
            tool_calls=tool_calls_made,
        )

    try:
        # The whole turn is bounded, not just each call: a model that keeps
        # asking for one more tool would otherwise hold the SSE connection
        # open indefinitely.
        async with asyncio.timeout(settings.agent_turn_timeout_seconds):
            while True:
                turn: TurnComplete | None = None

                async for event in services.llm.stream_turn(
                    system=system,
                    messages=conversation,
                    tools=TOOL_SCHEMAS,
                ):
                    if isinstance(event, TextChunk):
                        if event.text:
                            yield TextDelta(delta=event.text)
                    else:
                        turn = event

                if turn is None:
                    yield Error(
                        code="llm_failed",
                        message="The model returned no response.",
                    )
                    break

                input_tokens += turn.input_tokens
                output_tokens += turn.output_tokens

                if not turn.tool_calls:
                    break

                # Budget check before executing, so the cap is a real ceiling on
                # work done rather than on work already paid for.
                if (
                    tool_calls_made + len(turn.tool_calls)
                    > settings.agent_max_tool_calls
                ):
                    yield Error(
                        code="tool_failed",
                        message=(
                            "Reached the limit of "
                            f"{settings.agent_max_tool_calls} tool calls for one turn."
                        ),
                    )
                    break

                # Echo the assistant turn back verbatim — text first, then the
                # tool_use blocks. Dropping the text would lose any reasoning
                # the model wrote before deciding to call a tool.
                assistant_content: list[dict[str, Any]] = []
                if turn.text:
                    assistant_content.append({"type": "text", "text": turn.text})
                assistant_content.extend(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.input,
                    }
                    for call in turn.tool_calls
                )
                conversation.append({"role": "assistant", "content": assistant_content})

                # Parallel tool calls must come back as tool_result blocks in a
                # SINGLE user message; splitting them teaches the model to stop
                # calling tools in parallel.
                results: list[dict[str, Any]] = []
                for call in turn.tool_calls:
                    tool_calls_made += 1
                    yield ToolUse(name=call.name, input=call.input)

                    outcome = await execute_tool(context, call.name, call.input)
                    yield ToolResult(
                        name=call.name,
                        summary=outcome.summary,
                        truncated=outcome.truncated,
                    )
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call.id,
                            "content": outcome.payload,
                            # Flagging the failure lets the model recover rather
                            # than treat the error text as data.
                            "is_error": outcome.failed,
                        }
                    )

                conversation.append({"role": "user", "content": results})

    except TimeoutError:
        yield Error(
            code="llm_failed",
            message=(
                f"The assistant took longer than "
                f"{settings.agent_turn_timeout_seconds:.0f}s and was stopped."
            ),
        )
    except Exception as exc:  # noqa: BLE001 - the contract is "never raise"
        # SPEC: "Never raises for LLM or tool errors — those become error
        # AgentEvents." Rate limiting is called out separately because a client
        # can act on it (back off and retry) where it cannot on a generic fault.
        code: Literal["rate_limited", "llm_failed"] = (
            "rate_limited" if _is_rate_limit(exc) else "llm_failed"
        )
        yield Error(code=code, message=str(exc) or exc.__class__.__name__)

    yield done()


def _is_rate_limit(exc: BaseException) -> bool:
    """True for a 429 from the SDK, without importing it at module scope."""
    status = getattr(exc, "status_code", None)
    return status == 429 or exc.__class__.__name__ == "RateLimitError"
