"""Reading routes: ingest under a device, query across the caller's devices.

Ingest is nested (`/devices/{device_id}/readings`) because a reading only
exists in the context of a device. Queries are top-level (`/readings?...`)
because they filter, and CLAUDE.md keeps filters in the query string.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Query, status

from app.core.config import get_settings
from app.core.deps import CurrentUser, DbSession
from app.schemas.reading import (
    AggregateBucket,
    AggregateFilters,
    AlertFilters,
    AlertResponse,
    BulkReadingCreate,
    BulkReadingsResponse,
    ReadingCreate,
    ReadingFilters,
    ReadingResponse,
    DeleteReadingsFilters,
    DeleteReadingsResponse,
)
from app.services.reading import (
    aggregate_readings,
    create_reading,
    create_readings_bulk,
    list_alerts,
    list_readings,
    delete_readings,
)

router = APIRouter(tags=["readings"])

settings = get_settings()


@router.post(
    "/devices/{device_id}/readings",
    response_model=ReadingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create(
    device_id: uuid.UUID,
    payload: ReadingCreate,
    session: DbSession,
    current_user: CurrentUser,
) -> ReadingResponse:
    """Append a single reading. Omit `time` to stamp it now."""
    reading = await create_reading(session, current_user, device_id, payload)
    return ReadingResponse.model_validate(reading)


@router.post(
    "/devices/{device_id}/readings/bulk",
    response_model=BulkReadingsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_bulk(
    device_id: uuid.UUID,
    # The batch is size-capped at the edge so an oversized upload is a 422
    # rather than a request that ties up a connection building a huge INSERT.
    payload: Annotated[
        list[BulkReadingCreate], Body(max_length=settings.max_bulk_readings)
    ],
    session: DbSession,
    current_user: CurrentUser,
) -> BulkReadingsResponse:
    """Backfill many readings in one statement."""
    count = await create_readings_bulk(session, current_user, device_id, payload)
    return BulkReadingsResponse(count=count)


@router.get("/readings", response_model=list[ReadingResponse])
async def index(
    filters: Annotated[ReadingFilters, Query()],
    session: DbSession,
    current_user: CurrentUser,
) -> list[ReadingResponse]:
    """Raw readings for one device, newest first."""
    readings = await list_readings(
        session,
        current_user,
        filters.device_id,
        filters.start,
        filters.end,
        filters.limit,
    )
    return [ReadingResponse.model_validate(reading) for reading in readings]


@router.get("/readings/aggregate", response_model=list[AggregateBucket])
async def aggregate(
    filters: Annotated[AggregateFilters, Query()],
    session: DbSession,
    current_user: CurrentUser,
) -> list[AggregateBucket]:
    """Bucketed rollups (`avg|min|max|p95`) over `1h|1d|1w` windows."""
    return await aggregate_readings(
        session,
        current_user,
        filters.device_id,
        filters.window,
        filters.fn,
        filters.start,
        filters.end,
    )


@router.get("/readings/alerts", response_model=list[AlertResponse])
async def alerts(
    filters: Annotated[AlertFilters, Query()],
    session: DbSession,
    current_user: CurrentUser,
) -> list[AlertResponse]:
    """Threshold breaches since a given instant, newest first."""
    return await list_alerts(
        session,
        current_user,
        filters.since,
        filters.device_id,
        filters.limit,
    )


@router.delete("/readings", response_model=DeleteReadingsResponse)
async def delete(
    filters: Annotated[DeleteReadingsFilters, Query()],
    session: DbSession,
    current_user: CurrentUser,
) -> DeleteReadingsResponse:
    """Delete readings for one device, optionally within a time window.

    Returns how many rows were deleted. The caller must own the device.
    """
    count = await delete_readings(session, current_user, filters)
    return DeleteReadingsResponse(count=count)