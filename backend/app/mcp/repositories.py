"""HTTP-backed implementations of the agent's repository Protocols.

SPEC § Extraction path names these three by name as what `AgentServices` grows
when the agent module moves out of process: "`HttpDeviceRepository`,
`HttpReadingRepository`, `HttpAlertRepository` — each wraps calls to the data API
over HTTP." The MCP server is the first thing that needs them.

They live here rather than under `/agent/` on purpose. SPEC's rule is that
`/agent/` reaches data through `AgentServices` and nothing else, and its warning
against a "fake split" is about putting HTTP calls *inside* the agent module
while it still runs in the same process. This is the real thing: the MCP server
is a separate process on someone else's machine, so HTTP is the only way it can
reach the data. `/agent/` still knows only the Protocols it declares.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from app.agent.services import OwnershipError
from app.mcp.client import DataBullClient
from app.schemas.device import DeviceResponse
from app.schemas.reading import (
    AggregateBucket,
    AggregateFn,
    AggregateWindow,
    AlertResponse,
    ReadingResponse,
)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


class _HttpScoped:
    """Shared ownership check, mirroring `_OwnerScoped` in the in-process repos.

    The API enforces ownership itself — it is the authority, and it answers 404
    for another user's rows. This check is the same belt-and-braces the
    SQLAlchemy repositories apply: the authenticated identity is captured once,
    and a mismatch is a bug in the caller rather than something to pass along.
    """

    def __init__(self, client: DataBullClient, owner_id: uuid.UUID) -> None:
        self._client = client
        self._owner_id = owner_id

    def _check(self, user_id: uuid.UUID) -> None:
        if user_id != self._owner_id:
            raise OwnershipError(
                "Repository is scoped to a different user than the one requested."
            )


class HttpDeviceRepository(_HttpScoped):
    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[DeviceResponse]:
        self._check(user_id)
        rows = await self._client.request("GET", "/devices")
        return [DeviceResponse.model_validate(row) for row in rows]


class HttpReadingRepository(_HttpScoped):
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
        rows = await self._client.request(
            "GET",
            "/readings",
            params={
                "device_id": str(device_id),
                "start": _iso(start),
                "end": _iso(end),
                "limit": limit,
            },
        )
        return [ReadingResponse.model_validate(row) for row in rows]

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
        rows = await self._client.request(
            "GET",
            "/readings/aggregate",
            params={
                "device_id": str(device_id),
                "window": window.value,
                "fn": fn.value,
                "start": _iso(start),
                "end": _iso(end),
            },
        )
        return [AggregateBucket.model_validate(row) for row in rows]


class HttpAlertRepository(_HttpScoped):
    async def recent(
        self,
        user_id: uuid.UUID,
        *,
        since: datetime,
        device_id: uuid.UUID | None,
        limit: int,
    ) -> Sequence[AlertResponse]:
        self._check(user_id)
        rows = await self._client.request(
            "GET",
            "/readings/alerts",
            params={
                "since": since.isoformat(),
                "device_id": None if device_id is None else str(device_id),
                "limit": limit,
            },
        )
        return [AlertResponse.model_validate(row) for row in rows]
