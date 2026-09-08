"""In-memory doubles for the agent's dependencies.

SPEC § AgentServices names `Fake*Repository` implementations used by agent
tests and the eval runner. They exist because the agent's contract with the
outside world is a set of Protocols — satisfying them in memory means the tool
loop can be tested exhaustively without a database, and without a single
Anthropic call (CLAUDE.md: no API calls in tests without a recorded fixture).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any

from app.agent.llm_client import LLMEvent, TextChunk, ToolCall, TurnComplete
from app.agent.services import OwnershipError
from app.schemas.device import DeviceResponse
from app.schemas.reading import (
    AggregateBucket,
    AggregateFn,
    AggregateWindow,
    AlertResponse,
    ReadingResponse,
)

FIXED_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


class FakeDeviceRepository:
    def __init__(self, owner_id: uuid.UUID, devices: Sequence[DeviceResponse]) -> None:
        self._owner_id = owner_id
        self._devices = list(devices)
        self.calls = 0

    def _check(self, user_id: uuid.UUID) -> None:
        if user_id != self._owner_id:
            raise OwnershipError("wrong user")

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[DeviceResponse]:
        self._check(user_id)
        self.calls += 1
        return self._devices


class FakeReadingRepository:
    def __init__(
        self,
        owner_id: uuid.UUID,
        readings: Sequence[ReadingResponse] = (),
        buckets: Sequence[AggregateBucket] = (),
    ) -> None:
        self._owner_id = owner_id
        self._readings = list(readings)
        self._buckets = list(buckets)
        self.query_calls: list[dict[str, object]] = []

    def _check(self, user_id: uuid.UUID) -> None:
        if user_id != self._owner_id:
            raise OwnershipError("wrong user")

    async def query(
        self,
        user_id: uuid.UUID,
        *,
        device_id: uuid.UUID,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> Sequence[ReadingResponse]:
        self._check(user_id)
        self.query_calls.append(
            {"device_id": device_id, "start": start, "end": end, "limit": limit}
        )
        return self._readings[:limit]

    async def aggregate(
        self,
        user_id: uuid.UUID,
        *,
        device_id: uuid.UUID,
        window: AggregateWindow,
        fn: AggregateFn,
        start: datetime | None,
        end: datetime | None,
    ) -> Sequence[AggregateBucket]:
        self._check(user_id)
        return self._buckets


class FakeAlertRepository:
    def __init__(
        self, owner_id: uuid.UUID, alerts: Sequence[AlertResponse] = ()
    ) -> None:
        self._owner_id = owner_id
        self._alerts = list(alerts)

    async def recent(
        self,
        user_id: uuid.UUID,
        *,
        since: datetime,
        device_id: uuid.UUID | None,
        limit: int,
    ) -> Sequence[AlertResponse]:
        if user_id != self._owner_id:
            raise OwnershipError("wrong user")
        return self._alerts


class ExplodingDeviceRepository:
    """Repository whose call always fails, to exercise tool error recovery."""

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[DeviceResponse]:
        raise RuntimeError("database is on fire")


class FakeLLMClient:
    """Replays a scripted list of turns.

    Each element is one model turn: either a string (plain prose) or a list of
    `ToolCall`s. This lets a test drive the real `run_agent` loop through
    multi-step tool sequences deterministically.
    """

    def __init__(
        self,
        script: Sequence[str | list[ToolCall]],
        *,
        model: str = "claude-sonnet-5",
        input_tokens: int = 100,
        output_tokens: int = 20,
    ) -> None:
        self._script = list(script)
        self._model = model
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self.turns = 0
        # Every request the runner made, so tests can assert on what the model
        # was actually shown (e.g. that tool results were echoed back). `Any`
        # because these are provider message dicts whose nested content blocks
        # are heterogeneous — tests index into them directly.
        self.requests: list[list[dict[str, Any]]] = []

    @property
    def model(self) -> str:
        return self._model

    def rescript(self, script: Sequence[str | list[ToolCall]]) -> None:
        """Replace the scripted turns, for a test that needs a different flow."""
        self._script = list(script)
        self.turns = 0

    async def stream_turn(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[LLMEvent]:
        self.requests.append([dict(m) for m in messages])
        step = self._script[self.turns] if self.turns < len(self._script) else ""
        self.turns += 1

        if isinstance(step, str):
            # Chunk the text so the test sees real streaming behaviour.
            for word in step.split(" "):
                yield TextChunk(text=word + " ")
            yield TurnComplete(
                text=step,
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                stop_reason="end_turn",
            )
        else:
            yield TurnComplete(
                text="",
                tool_calls=list(step),
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                stop_reason="tool_use",
            )


class FailingLLMClient:
    """Raises on every turn, to exercise the never-raise contract."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    @property
    def model(self) -> str:
        return "claude-sonnet-5"

    async def stream_turn(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[LLMEvent]:
        raise self._exc
        yield TextChunk(text="")  # pragma: no cover - makes this an async generator
