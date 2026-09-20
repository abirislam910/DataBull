"""The agent module's public event contract.

SPEC § Architecture § "Event shapes (public contract)" fixes these. Both callers
— the SSE adapter and the eval runner — depend only on this file, so how the
agent works internally can change without touching either.

Every event is a Pydantic model so the adapter can serialize with
`model_dump_json()` and the frontend gets a stable, discriminated union.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class Usage(BaseModel):
    """Token counts, derived cost, and wall time for one agent turn."""

    input_tokens: int
    output_tokens: int
    # Computed from the model's published per-MTok rates (see llm_client.PRICING)
    # so the client never has to know what a token costs.
    cost_usd: float
    latency_ms: int


class TextDelta(BaseModel):
    """A chunk of the model's prose, streamed as it is generated."""

    type: Literal["text"] = "text"
    delta: str


class ToolUse(BaseModel):
    """The model asked to call a tool. Emitted before the tool runs."""

    type: Literal["tool_use"] = "tool_use"
    name: str
    input: dict[str, Any]


class ToolResult(BaseModel):
    """The outcome of one tool call, as a one-line human-readable summary.

    Deliberately not the full payload: the UI shows what the assistant did, not
    the rows it read, and shipping a thousand readings down the SSE channel to
    render "queried Pump-3" would be waste.
    """

    type: Literal["tool_result"] = "tool_result"
    name: str
    summary: str = Field(max_length=200)
    # True when the payload sent back to the model was shortened to fit the
    # size cap — the answer may be based on a subset.
    truncated: bool = False


class Error(BaseModel):
    """A failure the agent absorbed. `run_agent` reports, never raises."""

    type: Literal["error"] = "error"
    code: Literal["tool_failed", "llm_failed", "rate_limited", "invalid_input"]
    message: str


class Done(BaseModel):
    """Terminal event. Always emitted, including after an error."""

    type: Literal["done"] = "done"
    usage: Usage
    prompt_version: str
    tool_calls: int


# Discriminated on `type` so Pydantic and TypeScript can both narrow the union
# without guesswork.
AgentEvent = Annotated[
    TextDelta | ToolUse | ToolResult | Error | Done,
    Field(discriminator="type"),
]
