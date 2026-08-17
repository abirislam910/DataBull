"""Request/response models for the readings endpoints."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import get_settings

def ensure_utc(value: datetime) -> datetime:
    """Normalize any datetime to UTC.

    SPEC fixes every timestamp in the API as UTC. A client may still send a
    naive string ("2026-01-01T00:00:00") or one with a non-UTC offset, so this
    reads naive input as already-UTC and converts anything aware. Without it,
    psycopg would interpret a naive datetime against the server's local zone and
    silently store the wrong instant.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class AggregateWindow(str, enum.Enum):
    """Bucket widths accepted by `GET /readings/aggregate`."""

    HOUR = "1h"
    DAY = "1d"
    WEEK = "1w"


WINDOW_INTERVALS: dict[AggregateWindow, timedelta] = {
    AggregateWindow.HOUR: timedelta(hours=1),
    AggregateWindow.DAY: timedelta(days=1),
    AggregateWindow.WEEK: timedelta(weeks=1),
}


class AggregateFn(str, enum.Enum):
    """Rollup functions accepted by `GET /readings/aggregate`."""

    AVG = "avg"
    MIN = "min"
    MAX = "max"
    P95 = "p95"


class ReadingCreate(BaseModel):
    """Body for `POST /devices/{device_id}/readings`.

    `time` is optional: a live sensor posting "now" can omit it, while a
    backfill supplies the original instant.
    """

    value: float
    time: datetime | None = None

    @field_validator("time")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        return None if value is None else ensure_utc(value)


class BulkReadingCreate(BaseModel):
    """One item of `POST /devices/{device_id}/readings/bulk`.

    Unlike the single-reading body, `time` is REQUIRED here. Defaulting a whole
    batch to "now" would give every row the same timestamp, and since the
    primary key is (time, device_id) the batch would collide with itself — a
    confusing 409 for what looks like valid input. Bulk uploads are backfills;
    they know their timestamps.
    """

    value: float
    time: datetime

    @field_validator("time")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class ReadingResponse(BaseModel):
    """A single stored reading."""

    model_config = ConfigDict(from_attributes=True)

    device_id: uuid.UUID
    time: datetime
    value: float


class BulkReadingsResponse(BaseModel):
    """`POST /devices/{device_id}/readings/bulk` → `{count}`."""

    count: int


class AggregateBucket(BaseModel):
    """One row of `GET /readings/aggregate` → `{bucket, value}`."""

    bucket: datetime
    value: float


class AlertResponse(BaseModel):
    """A reading that breached one of its device's configured thresholds.

    Alerts are *derived*, not stored: there is no alerts table. Each response is
    computed by comparing readings against the owning device's thresholds at
    query time, which means editing a threshold retroactively changes what
    counts as an alert.

    Carries `device_name` and `unit` so the row is self-describing — the AI
    assistant is required to cite device names, and making it join that back
    itself would be wasted round-trips.
    """

    device_id: uuid.UUID
    device_name: str
    unit: str
    time: datetime
    value: float
    # Which bound was crossed, and the value it was compared against.
    bound: Literal["min", "max"]
    threshold: float


class _TimeWindow(BaseModel):
    """Shared `start`/`end` validation for the read endpoints."""

    start: datetime | None = None
    end: datetime | None = None

    @field_validator("start", "end")
    @classmethod
    def normalize_bounds(cls, value: datetime | None) -> datetime | None:
        if value > datetime.now(tz=UTC) + timedelta(minutes=5) or value < datetime(1970, 1, 1, tzinfo=UTC):
            raise ValueError("timestamp out of range")
        return None if value is None else ensure_utc(value)

    @model_validator(mode="after")
    def window_must_be_ordered(self) -> Self:
        """An inverted window can only ever return nothing — say so explicitly."""
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("start must be earlier than end.")
        return self


class _Limit(BaseModel):
    """Shared `start`/`end`/`limit` validation for the read endpoints."""

    limit: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def apply_default_limit(self) -> Self:
        settings = get_settings()
        if self.limit is None:
            object.__setattr__(self, "limit", settings.default_reading_limit)
        elif self.limit > settings.max_reading_limit:
            raise ValueError(f"limit cannot exceed {settings.max_reading_limit}")
        return self


class ReadingFilters(_TimeWindow, _Limit):
    """Query params for `GET /readings`.

    Modelled as a Pydantic query model rather than loose parameters so the
    defaults live here instead of in the signature — which is what lets
    `current_user` stay the last parameter on the route, per our conventions.
    """

    device_id: uuid.UUID


class AggregateFilters(_TimeWindow):
    """Query params for `GET /readings/aggregate`."""

    device_id: uuid.UUID
    window: AggregateWindow
    fn: AggregateFn

    @model_validator(mode="after")
    def bound_bucket_count(self) -> Self:
        settings = get_settings()
        MAX_BUCKETS = settings.max_aggregate_buckets
        if self.start is None and self.end is None:
            self.end = datetime.now(tz=UTC)
            self.start = self.end - MAX_BUCKETS * WINDOW_INTERVALS[self.window]
            return self
        elif self.start is None:
            self.start = self.end - MAX_BUCKETS * WINDOW_INTERVALS[self.window]
            return self
        elif self.end is None:
            self.end = self.start + MAX_BUCKETS * WINDOW_INTERVALS[self.window]
            return self
        span = self.end - self.start
        bucket_count = span / WINDOW_INTERVALS[self.window]
        if bucket_count > MAX_BUCKETS:
            raise ValueError(
                f"Requested range produces too many buckets "
                f"({int(bucket_count)} > {MAX_BUCKETS}); narrow the range or widen the window."
            )
        return self


class AlertFilters(_Limit):
    """Query params for `GET /readings/alerts`.

    `device_id` is optional here (SPEC marks it `device_id?`): omitted means
    "across every device I own".
    """

    since: datetime
    device_id: uuid.UUID | None = None

    @field_validator("since")
    @classmethod
    def normalize_since(cls, value: datetime) -> datetime:
        return ensure_utc(value)
