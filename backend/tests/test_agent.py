"""Agent module tests: the tool loop, driven with fakes and no network.

SPEC § Key decisions: "Direct `run_agent` calls with fake repos — faster tests,
better isolation, no HTTP layer to mock." Everything here exercises the real
runner; only the model and the database are substituted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.agent.events import Done, Error, TextDelta, ToolResult, ToolUse
from app.agent.llm_client import ToolCall, estimate_cost_usd
from app.agent.prompt import PROMPTS, SYSTEM_PROMPT_V1, get_prompt
from app.agent.runner import ChatMessage, run_agent
from app.agent.services import AgentServices, OwnershipError
from app.agent.tool_schemas import TOOL_SCHEMAS
from app.agent.tools import TOOLS, ToolContext, execute_tool
from app.models import DeviceType
from app.schemas.device import DeviceResponse
from app.schemas.reading import AggregateBucket, AlertResponse, ReadingResponse
from tests.agent_fakes import (
    FIXED_NOW,
    ExplodingDeviceRepository,
    FailingLLMClient,
    FakeAlertRepository,
    FakeDeviceRepository,
    FakeLLMClient,
    FakeReadingRepository,
)

USER_ID = uuid.uuid4()
DEVICE_ID = uuid.uuid4()

DEVICE = DeviceResponse(
    id=DEVICE_ID,
    name="Pump-3",
    type=DeviceType.FLOW,
    unit="L/min",
    min_threshold=10.0,
    max_threshold=80.0,
    created_at=FIXED_NOW,
)


def build_services(
    llm: object,
    *,
    devices: object | None = None,
    readings: object | None = None,
    alerts: object | None = None,
) -> AgentServices:
    return AgentServices(
        devices=devices or FakeDeviceRepository(USER_ID, [DEVICE]),  # type: ignore[arg-type]
        readings=readings or FakeReadingRepository(USER_ID),  # type: ignore[arg-type]
        alerts=alerts or FakeAlertRepository(USER_ID),  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        now=lambda: FIXED_NOW,
    )


# The fakes satisfy the Protocols structurally but mypy cannot see that through
# the `object` parameters above; the ignores are confined to this helper.


async def collect(services: AgentServices, text: str = "hi") -> list[object]:
    return [
        event
        async for event in run_agent(
            user_id=USER_ID,
            messages=[ChatMessage(role="user", content=text)],
            services=services,
        )
    ]


# --- Prompt -----------------------------------------------------------------


def test_prompt_v1_covers_the_spec_elements() -> None:
    """SPEC fixes five structural elements; a rewrite must not drop one."""
    prompt = get_prompt("v1")
    assert "industrial operator's assistant" in prompt
    assert "Answer only from tool results" in prompt
    assert "Cite device names" in prompt
    assert "Decline to speculate" in prompt
    assert "3 sentences" in prompt


def test_unknown_prompt_version_raises() -> None:
    """An eval naming a missing version must fail loudly, not silently drift."""
    with pytest.raises(ValueError, match="Unknown prompt version"):
        get_prompt("v99")


def test_prompt_registry_matches_the_constant() -> None:
    assert PROMPTS["v1"] is SYSTEM_PROMPT_V1


# --- Tool schemas -----------------------------------------------------------


def test_every_published_schema_has_an_implementation() -> None:
    """A schema with no handler is a tool the model can call into a 404."""
    assert {schema["name"] for schema in TOOL_SCHEMAS} == set(TOOLS)


def test_schemas_are_strict_and_closed() -> None:
    for schema in TOOL_SCHEMAS:
        assert schema["strict"] is True
        assert schema["input_schema"]["additionalProperties"] is False


# --- The tool loop ----------------------------------------------------------


async def test_plain_answer_streams_text_then_done() -> None:
    events = await collect(build_services(FakeLLMClient(["All clear."])))

    # Text arrives in however many chunks the model streamed; what matters is
    # that only text was emitted, it reassembles, and Done terminates.
    assert all(isinstance(e, TextDelta) for e in events[:-1])
    assert isinstance(events[-1], Done)
    text = "".join(e.delta for e in events if isinstance(e, TextDelta))
    assert text.strip() == "All clear."


async def test_tool_call_emits_use_then_result() -> None:
    llm = FakeLLMClient(
        [
            [ToolCall(id="t1", name="list_devices", input={})],
            "You have one device, Pump-3.",
        ]
    )
    events = await collect(build_services(llm))

    kinds = [type(e) for e in events]
    assert kinds[0] is ToolUse
    assert kinds[1] is ToolResult
    assert kinds[-1] is Done

    tool_use = events[0]
    assert isinstance(tool_use, ToolUse)
    assert tool_use.name == "list_devices"

    result = events[1]
    assert isinstance(result, ToolResult)
    assert "Pump-3" in result.summary


async def test_tool_results_are_fed_back_to_the_model() -> None:
    """The second request must contain the tool_result, or the model is blind."""
    llm = FakeLLMClient(
        [[ToolCall(id="t1", name="list_devices", input={})], "Pump-3 is fine."]
    )
    await collect(build_services(llm))

    assert llm.turns == 2
    second_request = llm.requests[1]
    roles = [message["role"] for message in second_request]
    assert roles == ["user", "assistant", "user"]

    tool_results = second_request[-1]["content"]
    assert isinstance(tool_results, list)
    assert tool_results[0]["type"] == "tool_result"
    assert tool_results[0]["tool_use_id"] == "t1"
    assert "Pump-3" in tool_results[0]["content"]


async def test_parallel_tool_calls_return_in_one_user_message() -> None:
    """Splitting them across messages trains the model out of parallel calls."""
    llm = FakeLLMClient(
        [
            [
                ToolCall(id="a", name="list_devices", input={}),
                ToolCall(id="b", name="list_devices", input={}),
            ],
            "Done.",
        ]
    )
    events = await collect(build_services(llm))

    assert sum(isinstance(e, ToolUse) for e in events) == 2
    results = llm.requests[1][-1]["content"]
    assert isinstance(results, list)
    assert [block["tool_use_id"] for block in results] == ["a", "b"]


async def test_done_reports_tool_call_count_and_usage() -> None:
    llm = FakeLLMClient(
        [[ToolCall(id="t1", name="list_devices", input={})], "Fine."],
        input_tokens=1000,
        output_tokens=500,
    )
    events = await collect(build_services(llm))

    done = events[-1]
    assert isinstance(done, Done)
    assert done.tool_calls == 1
    assert done.prompt_version == "v1"
    # Two turns of 1000 in / 500 out.
    assert done.usage.input_tokens == 2000
    assert done.usage.output_tokens == 1000
    assert done.usage.cost_usd == pytest.approx(
        estimate_cost_usd("claude-sonnet-5", 2000, 1000)
    )
    assert done.usage.latency_ms >= 0


async def test_tool_call_budget_is_enforced() -> None:
    """A model that never stops calling tools must be cut off, not followed."""
    # 11 calls in one turn, one over the configured cap of 10.
    calls = [ToolCall(id=f"t{i}", name="list_devices", input={}) for i in range(11)]
    events = await collect(build_services(FakeLLMClient([calls, "unreachable"])))

    errors = [e for e in events if isinstance(e, Error)]
    assert errors and errors[0].code == "tool_failed"
    assert "tool calls" in errors[0].message
    # Nothing ran: the cap is checked before execution.
    assert not any(isinstance(e, ToolUse) for e in events)
    assert isinstance(events[-1], Done)


# --- Failure handling -------------------------------------------------------


async def test_llm_failure_becomes_an_error_event_not_an_exception() -> None:
    """SPEC: run_agent never raises for LLM errors."""
    events = await collect(build_services(FailingLLMClient(RuntimeError("boom"))))

    errors = [e for e in events if isinstance(e, Error)]
    assert errors and errors[0].code == "llm_failed"
    assert "boom" in errors[0].message
    assert isinstance(events[-1], Done), "Done must still terminate the stream"


async def test_rate_limit_is_reported_distinctly() -> None:
    """A client can back off on rate_limited; it cannot on a generic failure."""

    class RateLimitError(Exception):
        status_code = 429

    events = await collect(
        build_services(FailingLLMClient(RateLimitError("slow down")))
    )
    errors = [e for e in events if isinstance(e, Error)]
    assert errors and errors[0].code == "rate_limited"


async def test_failing_tool_is_reported_and_the_turn_continues() -> None:
    """A broken tool gives the model a chance to recover, not a dead turn."""
    llm = FakeLLMClient(
        [
            [ToolCall(id="t1", name="list_devices", input={})],
            "I could not read your devices.",
        ]
    )
    services = build_services(llm, devices=ExplodingDeviceRepository())
    events = await collect(services)

    result = next(e for e in events if isinstance(e, ToolResult))
    assert "failed" in result.summary
    # The model still got a second turn and produced an answer.
    assert llm.turns == 2
    assert llm.requests[1][-1]["content"][0]["is_error"] is True


async def test_unknown_tool_is_reported_to_the_model() -> None:
    llm = FakeLLMClient([[ToolCall(id="t1", name="nonexistent", input={})], "Sorry."])
    events = await collect(build_services(llm))

    result = next(e for e in events if isinstance(e, ToolResult))
    assert "Unknown tool" in result.summary


async def test_invalid_tool_arguments_are_reported_not_raised() -> None:
    """A malformed date must come back as text the model can correct."""
    llm = FakeLLMClient(
        [
            [
                ToolCall(
                    id="t1",
                    name="query_readings",
                    input={"device_id": "not-a-uuid", "start": "x", "end": "y"},
                )
            ],
            "Let me try again.",
        ]
    )
    events = await collect(build_services(llm))

    result = next(e for e in events if isinstance(e, ToolResult))
    assert "invalid arguments" in result.summary
    assert "device_id" in llm.requests[1][-1]["content"][0]["content"]


async def test_empty_transcript_is_a_programmer_error() -> None:
    """SPEC: raises only for programmer errors."""
    with pytest.raises(ValueError, match="at least one message"):
        async for _ in run_agent(
            user_id=USER_ID, messages=[], services=build_services(FakeLLMClient([]))
        ):
            pass


# --- Isolation --------------------------------------------------------------


async def test_repository_refuses_a_mismatched_user() -> None:
    """The seam where a user-id mixup would silently leak another user's data."""
    repo = FakeDeviceRepository(USER_ID, [DEVICE])
    with pytest.raises(OwnershipError):
        await repo.list_for_user(uuid.uuid4())


# --- Truncation -------------------------------------------------------------


async def test_large_tool_result_is_truncated_and_flagged() -> None:
    """SPEC § Tool policy: results over ~2KB are summarized, and it is visible."""
    readings = [
        ReadingResponse(
            device_id=DEVICE_ID,
            time=FIXED_NOW - timedelta(minutes=i),
            value=float(i),
        )
        for i in range(500)
    ]
    llm = FakeLLMClient(
        [
            [
                ToolCall(
                    id="t1",
                    name="query_readings",
                    input={
                        "device_id": str(DEVICE_ID),
                        "start": (FIXED_NOW - timedelta(days=1)).isoformat(),
                        "end": FIXED_NOW.isoformat(),
                    },
                )
            ],
            "Recent flow is steady.",
        ]
    )
    services = build_services(
        llm, readings=FakeReadingRepository(USER_ID, readings=readings)
    )
    events = await collect(services)

    result = next(e for e in events if isinstance(e, ToolResult))
    assert result.truncated is True

    payload = llm.requests[1][-1]["content"][0]["content"]
    assert len(payload.encode()) <= 2048
    assert '"truncated":true' in payload


async def test_small_tool_result_is_not_flagged() -> None:
    buckets = [AggregateBucket(bucket=FIXED_NOW, value=42.0)]
    llm = FakeLLMClient(
        [
            [
                ToolCall(
                    id="t1",
                    name="aggregate_window",
                    input={
                        "device_id": str(DEVICE_ID),
                        "window": "1h",
                        "fn": "avg",
                        "start": (FIXED_NOW - timedelta(hours=2)).isoformat(),
                        "end": FIXED_NOW.isoformat(),
                    },
                )
            ],
            "Average is 42.",
        ]
    )
    services = build_services(
        llm, readings=FakeReadingRepository(USER_ID, buckets=buckets)
    )
    events = await collect(services)

    result = next(e for e in events if isinstance(e, ToolResult))
    assert result.truncated is False


# --- Tools in isolation -----------------------------------------------------


async def test_alerts_tool_summarizes_an_empty_result_as_good_news() -> None:
    context = ToolContext(user_id=USER_ID, services=build_services(FakeLLMClient([])))
    outcome = await execute_tool(
        context, "get_recent_alerts", {"since": FIXED_NOW.isoformat()}
    )
    assert outcome.failed is False
    assert "No alerts" in outcome.summary


async def test_alerts_tool_reports_breaches() -> None:
    alerts = [
        AlertResponse(
            device_id=DEVICE_ID,
            device_name="Pump-3",
            unit="L/min",
            time=FIXED_NOW,
            value=95.0,
            bound="max",
            threshold=80.0,
        )
    ]
    services = build_services(
        FakeLLMClient([]), alerts=FakeAlertRepository(USER_ID, alerts)
    )
    context = ToolContext(user_id=USER_ID, services=services)
    outcome = await execute_tool(
        context, "get_recent_alerts", {"since": FIXED_NOW.isoformat()}
    )
    assert "1 alert" in outcome.summary
    assert "Pump-3" in outcome.payload


async def test_naive_timestamps_are_read_as_utc() -> None:
    """A naive time must not be interpreted against the server's local zone."""
    readings_repo = FakeReadingRepository(USER_ID)
    services = build_services(FakeLLMClient([]), readings=readings_repo)
    context = ToolContext(user_id=USER_ID, services=services)

    await execute_tool(
        context,
        "query_readings",
        {
            "device_id": str(DEVICE_ID),
            "start": "2026-03-01T00:00:00",
            "end": "2026-03-01T12:00:00",
        },
    )

    call = readings_repo.query_calls[0]
    assert call["start"] == datetime(2026, 3, 1, 0, 0, tzinfo=UTC)


# --- Pricing ----------------------------------------------------------------


def test_cost_matches_published_sonnet_5_rates() -> None:
    # 1M input @ $2 + 1M output @ $10.
    assert estimate_cost_usd("claude-sonnet-5", 1_000_000, 1_000_000) == pytest.approx(
        12.0
    )


def test_unknown_model_costs_zero_rather_than_guessing() -> None:
    assert estimate_cost_usd("some-future-model", 1_000_000, 0) == 0.0
