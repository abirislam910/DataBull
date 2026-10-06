"""A deterministic sensor fleet for the eval suite.

SPEC § Eval plan: the runner drives `run_agent` with in-memory repositories
rather than a database. These are the eval counterparts to `tests/agent_fakes.py`
— but where the unit-test fakes are deliberately dumb (`query` ignores the time
window and returns a fixed list, which is what keeps a tool-loop test focused on
the loop), these honour `start`/`end`/`limit`/`window`/`fn` for real. An
aggregation eval is only meaningful if asking for a different window actually
returns different numbers.

Two properties matter more than they look:

**Device UUIDs are stable.** They are `uuid5` values derived from the device
name, so they are identical on every run, on every machine. Cassettes record the
`device_id` the model chose; if these were `uuid4` every recording would be
invalid the moment it was replayed.

**"Now" is pinned.** `FIXED_NOW` is injected as the agent's clock, so "the last
24 hours" resolves to the same absolute window every time and the generated
series below is always the same shape relative to it.
"""

from __future__ import annotations

import math
import random
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.models.device import DeviceType
from app.schemas.device import DeviceResponse
from app.schemas.reading import (
    AggregateBucket,
    AggregateFn,
    AggregateWindow,
    AlertResponse,
    ReadingResponse,
)

# The agent's clock for every eval run. A Sunday noon, so "this week" and
# "yesterday" both have a full series behind them.
FIXED_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

# How much history the fixture generates before FIXED_NOW.
HISTORY = timedelta(days=7)

# One sample per device per this interval.
SAMPLE_INTERVAL = timedelta(minutes=15)

# Namespace for deterministic device ids. Any fixed UUID works; this one is
# arbitrary and must simply never change.
_NS = uuid.UUID("6f1a9c54-0b3e-4a2d-9f77-2c5a1d8e4b10")

OWNER_ID = uuid.uuid5(_NS, "eval-owner")


def device_id(name: str) -> uuid.UUID:
    """The stable id for a fixture device, by name."""
    return uuid.uuid5(_NS, f"device:{name}")


class _Spec:
    """How one device's series is generated."""

    def __init__(
        self,
        name: str,
        type_: DeviceType,
        unit: str,
        baseline: float,
        swing: float,
        noise: float,
        period_hours: float,
        min_threshold: float | None,
        max_threshold: float | None,
        breaches: int = 0,
    ) -> None:
        self.name = name
        self.type = type_
        self.unit = unit
        self.baseline = baseline
        self.swing = swing
        self.noise = noise
        self.period_hours = period_hours
        self.min_threshold = min_threshold
        self.max_threshold = max_threshold
        # Deliberate threshold breaches in the most recent 24h, so the
        # alert-aware cases have something to find.
        self.breaches = breaches


# Only the three types the schema allows: temperature, pressure, flow.
FLEET: tuple[_Spec, ...] = (
    _Spec(
        "Furnace-1", DeviceType.TEMPERATURE, "°C", 820.0, 60.0, 8.0, 24, 200.0, 1100.0
    ),
    _Spec(
        "Furnace-2",
        DeviceType.TEMPERATURE,
        "°C",
        790.0,
        90.0,
        10.0,
        12,
        200.0,
        1100.0,
        breaches=2,
    ),
    _Spec(
        "Pump-3", DeviceType.PRESSURE, "bar", 5.2, 1.1, 0.15, 6, 1.0, 8.0, breaches=3
    ),
    _Spec("Pump-4", DeviceType.PRESSURE, "bar", 4.1, 0.8, 0.12, 8, 1.0, 8.0),
    _Spec("Inlet-Flow-1", DeviceType.FLOW, "L/min", 48.0, 12.0, 1.5, 24, 10.0, 90.0),
)

DEVICE_NAMES: tuple[str, ...] = tuple(spec.name for spec in FLEET)


def devices() -> list[DeviceResponse]:
    """The fleet as the API would return it."""
    created = FIXED_NOW - timedelta(days=90)
    return [
        DeviceResponse(
            id=device_id(spec.name),
            name=spec.name,
            type=spec.type,
            unit=spec.unit,
            min_threshold=spec.min_threshold,
            max_threshold=spec.max_threshold,
            created_at=created,
        )
        for spec in FLEET
    ]


def _series(spec: _Spec) -> list[ReadingResponse]:
    """Generate one device's readings: baseline + sinusoid + seeded noise.

    Seeded per device name so the series is reproducible and independent of the
    order devices are generated in.
    """
    rng = random.Random(f"databull-eval:{spec.name}")
    did = device_id(spec.name)
    start = FIXED_NOW - HISTORY
    steps = int(HISTORY / SAMPLE_INTERVAL)

    readings: list[ReadingResponse] = []
    for step in range(steps):
        stamp = start + step * SAMPLE_INTERVAL
        hours = step * SAMPLE_INTERVAL.total_seconds() / 3600
        phase = 2 * math.pi * hours / spec.period_hours
        value = spec.baseline + spec.swing * math.sin(phase) + rng.gauss(0, spec.noise)
        readings.append(
            ReadingResponse(device_id=did, time=stamp, value=round(value, 3))
        )

    # Push a few of the most recent samples past a threshold, spaced out so they
    # land in distinct hourly buckets.
    if spec.breaches and spec.max_threshold is not None:
        over = spec.max_threshold + spec.swing * 0.1
        for offset in range(spec.breaches):
            index = len(readings) - 1 - offset * 5
            if index >= 0:
                breached = readings[index]
                readings[index] = ReadingResponse(
                    device_id=did,
                    time=breached.time,
                    value=round(over + offset, 3),
                )

    return readings


def all_readings() -> dict[uuid.UUID, list[ReadingResponse]]:
    """Every device's series, newest-last, keyed by device id."""
    return {device_id(spec.name): _series(spec) for spec in FLEET}


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Linear-interpolated percentile, matching `percentile_cont` semantics."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


# Annotated explicitly: without it mypy infers the value type from four
# differently-shaped callables and lands on `object`, which is not callable.
_AGGREGATORS: dict[AggregateFn, Callable[[Sequence[float]], float]] = {
    AggregateFn.AVG: lambda values: sum(values) / len(values),
    AggregateFn.MIN: min,
    AggregateFn.MAX: max,
    AggregateFn.P95: lambda values: _percentile(values, 0.95),
}


def _floor_to_window(stamp: datetime, window: AggregateWindow) -> datetime:
    """Floor a timestamp to the start of its bucket.

    Weeks align to Monday, which is what TimescaleDB's `time_bucket` does for a
    7-day interval (its epoch is a Monday).
    """
    if window is AggregateWindow.HOUR:
        return stamp.replace(minute=0, second=0, microsecond=0)
    midnight = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    if window is AggregateWindow.DAY:
        return midnight
    return midnight - timedelta(days=midnight.weekday())


class _Scoped:
    """Shared ownership check.

    Mirrors the live repositories: the eval owner is fixed, and a mismatch is a
    bug in the harness rather than something to tolerate quietly.
    """

    def __init__(self, owner_id: uuid.UUID = OWNER_ID) -> None:
        self._owner_id = owner_id

    def _check(self, user_id: uuid.UUID) -> None:
        if user_id != self._owner_id:
            raise AssertionError(f"eval repository asked for foreign user {user_id}")


class EvalDeviceRepository(_Scoped):
    def __init__(self, owner_id: uuid.UUID = OWNER_ID) -> None:
        super().__init__(owner_id)
        self._devices = devices()
        self.calls = 0

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[DeviceResponse]:
        self._check(user_id)
        self.calls += 1
        return self._devices


class EvalReadingRepository(_Scoped):
    """Honours the requested window, limit, bucket width, and function."""

    def __init__(self, owner_id: uuid.UUID = OWNER_ID) -> None:
        super().__init__(owner_id)
        self._readings = all_readings()

    def _window(
        self, device: uuid.UUID, start: datetime | None, end: datetime | None
    ) -> list[ReadingResponse]:
        rows = self._readings.get(device, [])
        # Half-open, as the API documents: start inclusive, end exclusive.
        return [
            row
            for row in rows
            if (start is None or row.time >= start) and (end is None or row.time < end)
        ]

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
        rows = self._window(device_id, start, end)
        # Newest first, like the live endpoint, so truncation keeps recent rows.
        rows.sort(key=lambda row: row.time, reverse=True)
        return rows[:limit]

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
        buckets: dict[datetime, list[float]] = {}
        for row in self._window(device_id, start, end):
            buckets.setdefault(_floor_to_window(row.time, window), []).append(row.value)

        aggregate = _AGGREGATORS[fn]
        return [
            AggregateBucket(bucket=bucket, value=round(aggregate(values), 3))
            for bucket, values in sorted(buckets.items())
        ]


class EvalAlertRepository(_Scoped):
    """Derives breaches from the fixture series, as the live service does."""

    def __init__(self, owner_id: uuid.UUID = OWNER_ID) -> None:
        super().__init__(owner_id)
        self._alerts = self._derive()

    def _derive(self) -> list[AlertResponse]:
        readings = all_readings()
        alerts: list[AlertResponse] = []
        for spec in FLEET:
            did = device_id(spec.name)
            for row in readings[did]:
                bound: Literal["min", "max"]
                if spec.max_threshold is not None and row.value > spec.max_threshold:
                    bound, threshold = "max", spec.max_threshold
                elif spec.min_threshold is not None and row.value < spec.min_threshold:
                    bound, threshold = "min", spec.min_threshold
                else:
                    continue
                alerts.append(
                    AlertResponse(
                        device_id=did,
                        device_name=spec.name,
                        unit=spec.unit,
                        time=row.time,
                        value=row.value,
                        bound=bound,
                        threshold=threshold,
                    )
                )
        alerts.sort(key=lambda alert: alert.time, reverse=True)
        return alerts

    async def recent(
        self,
        user_id: uuid.UUID,
        *,
        since: datetime,
        device_id: uuid.UUID | None,
        limit: int,
    ) -> Sequence[AlertResponse]:
        self._check(user_id)
        matches = [
            alert
            for alert in self._alerts
            if alert.time >= since
            and (device_id is None or alert.device_id == device_id)
        ]
        return matches[:limit]
