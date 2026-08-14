"""Reading ingest, query, aggregation, and alert derivation.

Every function here starts by resolving the device through
`get_owned_device`, which 404s unless the caller owns it. That single gate is
what keeps one user's telemetry invisible to another — readings have no
`user_id` of their own, so ownership is always reached through the device.

This module holds the only raw-ish SQL in the project (TimescaleDB's
`time_bucket`, and `percentile_cont` for p95), which is exactly why CLAUDE.md
puts SQL in services rather than routers.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import status
from sqlalchemy import Interval, and_, cast, func, insert, literal, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import APIError
from app.models import Device, Reading, User
from app.schemas.reading import (
    AggregateBucket,
    AggregateFn,
    AggregateWindow,
    AlertResponse,
    BulkReadingCreate,
    ReadingCreate,
    ensure_utc,
)
from app.services.device import get_owned_device

# Bucket widths, as real intervals rather than SQL string fragments — passing a
# timedelta as a bound parameter keeps the query injection-proof.
WINDOW_INTERVALS: dict[AggregateWindow, timedelta] = {
    AggregateWindow.HOUR: timedelta(hours=1),
    AggregateWindow.DAY: timedelta(days=1),
    AggregateWindow.WEEK: timedelta(weeks=1),
}

P95 = 0.95


def _duplicate_reading_error(exc: IntegrityError) -> APIError:
    """409 for a (time, device_id) that already exists.

    The composite primary key makes a device's timeline unique per instant, so
    re-posting the same timestamp is a conflict rather than an overwrite.
    """
    return APIError(
        status_code=status.HTTP_409_CONFLICT,
        detail="A reading already exists for that device at that time.",
        code="duplicate_reading",
        field="time",
    )


def _aggregate_expression(fn: AggregateFn) -> ColumnElement[Any]:
    """Map the requested rollup to its SQL expression.

    Returns `ColumnElement[Any]` because SQLAlchemy's generic `func.` accessor
    cannot know a function's return type — `avg` and `percentile_cont` are both
    untyped `Function` objects. The concrete float conversion happens where the
    rows are read.
    """
    if fn is AggregateFn.AVG:
        return func.avg(Reading.value)
    if fn is AggregateFn.MIN:
        return func.min(Reading.value)
    if fn is AggregateFn.MAX:
        return func.max(Reading.value)
    # p95: percentile_cont interpolates between samples, so it returns a
    # sensible number even for small buckets. `within_group` renders the
    # required `WITHIN GROUP (ORDER BY value)` clause.
    return func.percentile_cont(P95).within_group(Reading.value)


async def create_reading(
    session: AsyncSession, owner: User, device_id: uuid.UUID, data: ReadingCreate
) -> Reading:
    """Append one reading to a device the caller owns."""
    device = await get_owned_device(session, owner, device_id)

    reading = Reading(
        device_id=device.id,
        # The model dropped its server-side default, so "now" is decided here.
        time=data.time if data.time is not None else datetime.now(UTC),
        value=data.value,
    )
    session.add(reading)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _duplicate_reading_error(exc) from exc

    await session.commit()
    return reading


async def create_readings_bulk(
    session: AsyncSession,
    owner: User,
    device_id: uuid.UUID,
    items: Sequence[BulkReadingCreate],
) -> int:
    """Insert many readings in one statement; returns how many landed.

    Uses a single executemany-style INSERT rather than adding ORM objects one at
    a time: for a backfill of thousands of rows the per-object identity-map
    bookkeeping dominates, and none of it is needed here since nothing reads the
    inserted objects back.
    """
    device = await get_owned_device(session, owner, device_id)
    if not items:
        return 0

    rows = [
        {"device_id": device.id, "time": item.time, "value": item.value}
        for item in items
    ]
    try:
        await session.execute(insert(Reading), rows)
    except IntegrityError as exc:
        await session.rollback()
        raise _duplicate_reading_error(exc) from exc

    await session.commit()
    return len(rows)


async def list_readings(
    session: AsyncSession,
    owner: User,
    device_id: uuid.UUID,
    start: datetime | None,
    end: datetime | None,
    limit: int,
) -> Sequence[Reading]:
    """Raw readings for one device, newest first.

    Newest-first matters because `limit` truncates: when a caller asks for 1000
    readings from a device with a million, the useful thousand are the recent
    ones. The `(device_id, time)` index serves this exactly — equality on the
    leading column, range on the second.
    """
    device = await get_owned_device(session, owner, device_id)

    stmt = select(Reading).where(Reading.device_id == device.id)
    if start is not None:
        stmt = stmt.where(Reading.time >= ensure_utc(start))
    if end is not None:
        # Half-open [start, end): adjacent windows tile without double-counting
        # the instant on the boundary.
        stmt = stmt.where(Reading.time < ensure_utc(end))

    stmt = stmt.order_by(Reading.time.desc()).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


async def aggregate_readings(
    session: AsyncSession,
    owner: User,
    device_id: uuid.UUID,
    window: AggregateWindow,
    fn: AggregateFn,
    start: datetime | None,
    end: datetime | None,
) -> list[AggregateBucket]:
    """Bucketed rollups via TimescaleDB's `time_bucket`.

    `time_bucket(interval, ts)` floors each timestamp to the start of its bucket
    so rows group into fixed-width slots. This is the operation the hypertable
    exists for: chunk exclusion lets Postgres skip every chunk outside
    [start, end) instead of scanning the whole table.

    Buckets with no readings are simply absent — this returns the buckets that
    exist, not a gap-filled series.
    """
    device = await get_owned_device(session, owner, device_id)

    bucket = func.time_bucket(
        cast(literal(WINDOW_INTERVALS[window]), Interval), Reading.time
    ).label("bucket")

    stmt = select(bucket, _aggregate_expression(fn).label("value")).where(
        Reading.device_id == device.id
    )
    if start is not None:
        stmt = stmt.where(Reading.time >= ensure_utc(start))
    if end is not None:
        stmt = stmt.where(Reading.time < ensure_utc(end))

    stmt = stmt.group_by(bucket).order_by(bucket)
    result = await session.execute(stmt)
    return [
        AggregateBucket(bucket=row.bucket, value=float(row.value))
        for row in result
        if row.value is not None
    ]


async def list_alerts(
    session: AsyncSession,
    owner: User,
    since: datetime,
    device_id: uuid.UUID | None,
    limit: int,
) -> list[AlertResponse]:
    """Readings that breached their device's configured thresholds.

    Derived at query time by joining readings to their device and comparing
    against that device's bounds — there is no alerts table. A device with both
    thresholds NULL can never produce an alert, and the NULL checks below keep
    such devices out rather than letting a NULL comparison quietly drop rows.
    """
    if device_id is not None:
        # Validate ownership explicitly so an unowned id 404s, rather than
        # silently returning an empty list.
        await get_owned_device(session, owner, device_id)

    breached_min = and_(
        Device.min_threshold.is_not(None), Reading.value < Device.min_threshold
    )
    breached_max = and_(
        Device.max_threshold.is_not(None), Reading.value > Device.max_threshold
    )

    stmt = (
        select(
            Reading.time,
            Reading.value,
            Device.id.label("device_id"),
            Device.name.label("device_name"),
            Device.unit,
            Device.min_threshold,
            Device.max_threshold,
        )
        .join(Device, Device.id == Reading.device_id)
        # The ownership filter for the "all my devices" case.
        .where(Device.user_id == owner.id)
        .where(Reading.time >= ensure_utc(since))
        .where(or_(breached_min, breached_max))
    )
    if device_id is not None:
        stmt = stmt.where(Device.id == device_id)

    stmt = stmt.order_by(Reading.time.desc()).limit(limit)
    result = await session.execute(stmt)

    alerts: list[AlertResponse] = []
    for row in result:
        # Thresholds are mutually exclusive (min < max is enforced on write), so
        # a reading can only be under the floor or over the ceiling, never both.
        under_min = row.min_threshold is not None and row.value < row.min_threshold
        bound: Literal["min", "max"] = "min" if under_min else "max"
        threshold = row.min_threshold if under_min else row.max_threshold
        alerts.append(
            AlertResponse(
                device_id=row.device_id,
                device_name=row.device_name,
                unit=row.unit,
                time=row.time,
                value=row.value,
                bound=bound,
                threshold=threshold,
            )
        )
    return alerts
