"""Recorded Anthropic turns, so the eval suite is deterministic and free to run.

SPEC § Eval plan: "CI uses recorded Anthropic responses (vcrpy-style) for
determinism. A separate weekly job hits the live API to track drift."

Three modes, selected by `EVAL_MODE`:

- `replay` (default) — read turns from `evals/cassettes/<case_id>.json`. No key,
  no network, no cost. This is what CI runs, and what CLAUDE.md requires: "never
  make Anthropic API calls in tests without a recorded fixture."
- `record` — call the real API and write a cassette per case. Opt-in, needs a
  key, **spends money**.
- `live` — call the real API and write nothing. For the drift job.

The cassette stores our own `TurnComplete` shape rather than raw SDK JSON. That
is a deliberate trade: it will not survive a change to `LLMClient`'s event types,
but it is readable, diffable in review, and independent of the SDK's wire format
— so an SDK upgrade does not invalidate every recording.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

from app.agent.llm_client import LLMEvent, TextChunk, ToolCall, TurnComplete

CASSETTE_DIR: Final[Path] = Path(__file__).parent / "cassettes"

Mode = Literal["replay", "record", "live"]


def current_mode() -> Mode:
    """The mode this run uses. Anything unrecognised is a hard error, not a default."""
    raw = os.environ.get("EVAL_MODE", "replay").strip().lower()
    if raw not in ("replay", "record", "live"):
        raise ValueError(
            f"EVAL_MODE must be one of replay|record|live, got {raw!r}",
        )
    return raw  # type: ignore[return-value]  # narrowed by the membership test


class CassetteMissing(RuntimeError):
    """No recording exists for this case."""


def cassette_path(case_id: str) -> Path:
    return CASSETTE_DIR / f"{case_id}.json"


def _turn_to_json(turn: TurnComplete, chunks: Sequence[str]) -> dict[str, Any]:
    return {
        "text": turn.text,
        "text_chunks": list(chunks),
        "tool_calls": [
            {"id": call.id, "name": call.name, "input": call.input}
            for call in turn.tool_calls
        ],
        "input_tokens": turn.input_tokens,
        "output_tokens": turn.output_tokens,
        "stop_reason": turn.stop_reason,
    }


def _turn_from_json(raw: dict[str, Any]) -> tuple[TurnComplete, list[str]]:
    calls = [
        ToolCall(id=str(call["id"]), name=str(call["name"]), input=dict(call["input"]))
        for call in raw.get("tool_calls", [])
    ]
    turn = TurnComplete(
        text=str(raw.get("text", "")),
        tool_calls=calls,
        input_tokens=int(raw.get("input_tokens", 0)),
        output_tokens=int(raw.get("output_tokens", 0)),
        stop_reason=raw.get("stop_reason"),
    )
    chunks = [str(chunk) for chunk in raw.get("text_chunks", [])]
    return turn, chunks


class CassetteLLMClient:
    """Replays a recorded conversation. Satisfies `LLMClient`."""

    def __init__(self, case_id: str) -> None:
        path = cassette_path(case_id)
        if not path.exists():
            raise CassetteMissing(
                f"No cassette for {case_id!r} at {path}. "
                f"Record one with: EVAL_MODE=record pytest evals/ -k {case_id}"
            )
        payload = json.loads(path.read_text())
        self._model = str(payload.get("model", "claude-sonnet-5"))
        self._turns = [_turn_from_json(turn) for turn in payload.get("turns", [])]
        self._index = 0
        self.prompt_version = str(payload.get("prompt_version", "v1"))

    @property
    def model(self) -> str:
        return self._model

    async def stream_turn(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        now: datetime | None = None,
    ) -> AsyncIterator[LLMEvent]:
        if self._index >= len(self._turns):
            # The loop asked for more turns than were recorded. That means the
            # agent's behaviour changed since the recording — surface it rather
            # than silently ending the conversation with empty text.
            raise CassetteMissing(
                f"Cassette exhausted after {len(self._turns)} turn(s); "
                "the agent asked for another. Re-record this case."
            )
        turn, chunks = self._turns[self._index]
        self._index += 1

        for chunk in chunks:
            yield TextChunk(text=chunk)
        yield turn


class RecordingLLMClient:
    """Wraps a real client, captures every turn, writes a cassette on `save()`."""

    def __init__(self, inner: Any, case_id: str, prompt_version: str = "v1") -> None:
        self._inner = inner
        self._case_id = case_id
        self._prompt_version = prompt_version
        self._turns: list[dict[str, Any]] = []

    @property
    def model(self) -> str:
        return str(self._inner.model)

    async def stream_turn(
        self,
        *,
        system: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        now: datetime | None = None,
    ) -> AsyncIterator[LLMEvent]:
        chunks: list[str] = []
        async for event in self._inner.stream_turn(
            system=system, messages=messages, tools=tools, now=now
        ):
            if isinstance(event, TextChunk):
                chunks.append(event.text)
            else:
                self._turns.append(_turn_to_json(event, chunks))
                chunks = []
            yield event

    def save(self) -> Path:
        CASSETTE_DIR.mkdir(parents=True, exist_ok=True)
        path = cassette_path(self._case_id)
        path.write_text(
            json.dumps(
                {
                    "case_id": self._case_id,
                    "model": self.model,
                    "prompt_version": self._prompt_version,
                    "recorded_at": datetime.now(UTC).isoformat(),
                    "turns": self._turns,
                },
                indent=2,
            )
            + "\n"
        )
        return path
