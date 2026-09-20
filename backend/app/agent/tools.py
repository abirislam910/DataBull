"""The four tools Claude can call, and their execution.

Every tool input is validated by Pydantic before anything runs, so a malformed
argument from the model becomes a clean error the model can read and retry —
not an exception that kills the turn. SPEC § Tool policy: "Tool errors become
ToolResult events with an error summary rather than exceptions — the model gets
a chance to recover."
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agent.services import AgentServices
from app.schemas.reading import AggregateFn, AggregateWindow

# Cap on how much of a tool's output is fed back to the model, from
# SPEC § Tool policy. Enforced on the serialized JSON, because that is what
# actually costs tokens.
DEFAULT_MAX_RESULT_BYTES = 2048

# Ceiling on rows a single tool call may pull back. The model cannot raise it;
# without one, "show me everything" turns into a hypertable scan serialized
# into the context window.
MAX_TOOL_ROWS = 1000


@dataclass(frozen=True)
class ToolContext:
    """What a tool needs: who is asking, and how to reach the outside world."""

    user_id: uuid.UUID
    services: AgentServices


@dataclass(frozen=True)
class ToolOutcome:
    """The result of one tool call, in both the shapes the runner needs.

    `payload` goes back to the model as the tool_result content; `summary` is
    the one-liner the UI shows. They are different on purpose — the model needs
    data, the operator needs to know what happened.
    """

    payload: str
    summary: str
    truncated: bool = False
    failed: bool = False


def _utc(value: datetime) -> datetime:
    """Read a naive timestamp as UTC rather than as server-local time."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class _WindowInput(BaseModel):
    """Shared start/end handling for the time-ranged tools."""

    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def to_utc(cls, value: datetime) -> datetime:
        return _utc(value)


class QueryReadingsInput(_WindowInput):
    device_id: uuid.UUID
    limit: int = Field(default=MAX_TOOL_ROWS, ge=1, le=MAX_TOOL_ROWS)


class AggregateWindowInput(_WindowInput):
    device_id: uuid.UUID
    window: AggregateWindow
    fn: AggregateFn


class GetRecentAlertsInput(BaseModel):
    since: datetime
    device_id: uuid.UUID | None = None

    @field_validator("since")
    @classmethod
    def to_utc(cls, value: datetime) -> datetime:
        return _utc(value)


def _serialize(rows: list[dict[str, Any]], max_bytes: int) -> tuple[str, bool]:
    """JSON-encode `rows`, dropping from the end until it fits `max_bytes`.

    Truncation keeps the *head* of the list. Readings come back newest-first,
    so the rows that survive are the recent ones — the useful half of a series
    the model asked too much of. The reply says how many were dropped so the
    model can narrow its range rather than assume it saw everything.
    """
    payload = json.dumps(rows, separators=(",", ":"), default=str)
    if len(payload.encode()) <= max_bytes:
        return payload, False

    kept = list(rows)
    while kept:
        kept.pop()
        candidate = json.dumps(
            {"rows": kept, "truncated": True, "omitted": len(rows) - len(kept)},
            separators=(",", ":"),
            default=str,
        )
        if len(candidate.encode()) <= max_bytes:
            return candidate, True

    return json.dumps({"rows": [], "truncated": True, "omitted": len(rows)}), True


async def list_devices(ctx: ToolContext, _raw: dict[str, Any]) -> ToolOutcome:
    devices = await ctx.services.devices.list_for_user(ctx.user_id)
    rows = [
        {
            "device_id": str(device.id),
            "name": device.name,
            "type": device.type.value,
            "unit": device.unit,
            "min_threshold": device.min_threshold,
            "max_threshold": device.max_threshold,
        }
        for device in devices
    ]
    payload, truncated = _serialize(rows, DEFAULT_MAX_RESULT_BYTES)
    names = ", ".join(device.name for device in devices[:5])
    summary = (
        f"Found {len(devices)} device(s): {names}" if devices else "No devices found"
    )
    return ToolOutcome(payload=payload, summary=summary[:200], truncated=truncated)


async def query_readings(ctx: ToolContext, raw: dict[str, Any]) -> ToolOutcome:
    args = QueryReadingsInput.model_validate(raw)
    readings = await ctx.services.readings.query(
        ctx.user_id,
        device_id=args.device_id,
        start=args.start,
        end=args.end,
        limit=args.limit,
    )
    rows = [
        {"time": reading.time.isoformat(), "value": reading.value}
        for reading in readings
    ]
    payload, truncated = _serialize(rows, DEFAULT_MAX_RESULT_BYTES)
    summary = (
        f"{len(readings)} reading(s) between {args.start.isoformat()} "
        f"and {args.end.isoformat()}"
    )
    return ToolOutcome(payload=payload, summary=summary[:200], truncated=truncated)


async def aggregate_window(ctx: ToolContext, raw: dict[str, Any]) -> ToolOutcome:
    args = AggregateWindowInput.model_validate(raw)
    buckets = await ctx.services.readings.aggregate(
        ctx.user_id,
        device_id=args.device_id,
        window=args.window,
        fn=args.fn,
        start=args.start,
        end=args.end,
    )
    rows = [
        {"bucket": bucket.bucket.isoformat(), "value": bucket.value}
        for bucket in buckets
    ]
    payload, truncated = _serialize(rows, DEFAULT_MAX_RESULT_BYTES)
    summary = f"{len(buckets)} {args.window.value} bucket(s), {args.fn.value}"
    return ToolOutcome(payload=payload, summary=summary[:200], truncated=truncated)


async def get_recent_alerts(ctx: ToolContext, raw: dict[str, Any]) -> ToolOutcome:
    args = GetRecentAlertsInput.model_validate(raw)
    alerts = await ctx.services.alerts.recent(
        ctx.user_id,
        since=args.since,
        device_id=args.device_id,
        limit=MAX_TOOL_ROWS,
    )
    rows = [
        {
            "device_name": alert.device_name,
            "time": alert.time.isoformat(),
            "value": alert.value,
            "unit": alert.unit,
            "bound": alert.bound,
            "threshold": alert.threshold,
        }
        for alert in alerts
    ]
    payload, truncated = _serialize(rows, DEFAULT_MAX_RESULT_BYTES)
    summary = (
        f"{len(alerts)} alert(s) since {args.since.isoformat()}"
        if alerts
        else f"No alerts since {args.since.isoformat()}"
    )
    return ToolOutcome(payload=payload, summary=summary[:200], truncated=truncated)


# Registry the runner dispatches through. Keys must match `tool_schemas.TOOLS`.
TOOLS = {
    "list_devices": list_devices,
    "query_readings": query_readings,
    "aggregate_window": aggregate_window,
    "get_recent_alerts": get_recent_alerts,
}


async def execute_tool(ctx: ToolContext, name: str, raw: dict[str, Any]) -> ToolOutcome:
    """Run one tool, converting every failure into a readable result.

    Nothing here raises. An unknown tool, a bad argument, or a repository error
    all come back as `failed` outcomes whose text is written for the model to
    act on — it can correct a malformed date and try again, which it cannot do
    with a stack trace.
    """
    handler = TOOLS.get(name)
    if handler is None:
        return ToolOutcome(
            payload=f"Unknown tool {name!r}.",
            summary=f"Unknown tool {name!r}",
            failed=True,
        )

    try:
        return await handler(ctx, raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        return ToolOutcome(
            payload=f"Invalid arguments for {name}: {problems}",
            summary=f"{name}: invalid arguments"[:200],
            failed=True,
        )
    except Exception as exc:  # noqa: BLE001 - deliberately broad; see docstring
        # A tool failure must not end the turn. The model is told what went
        # wrong in one line and may recover (e.g. by listing devices first
        # after naming one that does not exist).
        return ToolOutcome(
            payload=f"{name} failed: {exc}",
            summary=f"{name} failed"[:200],
            failed=True,
        )
