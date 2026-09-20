"""`AgentServices` — everything the agent needs to reach the outside world.

SPEC § "AgentServices — the extraction seam". The agent never opens a database
session and never imports an ORM model; it goes through these repositories. If
the module is ever split into its own service, these get HTTP implementations
and nothing inside `/agent/` changes.

The repositories are `Protocol`s, not base classes, so the in-memory fakes the
eval suite uses are ordinary objects rather than subclasses of a SQLAlchemy
thing they have nothing to do with.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from app.schemas.device import DeviceResponse
from app.schemas.reading import (
    AggregateBucket,
    AggregateFn,
    AggregateWindow,
    AlertResponse,
    ReadingResponse,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.agent.llm_client import LLMClient
    from app.models import User


class OwnershipError(RuntimeError):
    """Raised when a repository is asked for a user other than its owner.

    This should be unreachable: the runner passes the same `user_id` the
    adapter authenticated. It exists so that a future bug which mixes up user
    ids fails loudly here instead of quietly serving one user another's
    telemetry.
    """


class DeviceRepository(Protocol):
    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[DeviceResponse]: ...


class ReadingRepository(Protocol):
    async def query(
        self,
        user_id: uuid.UUID,
        *,
        device_id: uuid.UUID,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> Sequence[ReadingResponse]: ...

    async def aggregate(
        self,
        user_id: uuid.UUID,
        *,
        device_id: uuid.UUID,
        window: AggregateWindow,
        fn: AggregateFn,
        start: datetime | None,
        end: datetime | None,
    ) -> Sequence[AggregateBucket]: ...


class AlertRepository(Protocol):
    async def recent(
        self,
        user_id: uuid.UUID,
        *,
        since: datetime,
        device_id: uuid.UUID | None,
        limit: int,
    ) -> Sequence[AlertResponse]: ...


@dataclass(frozen=True)
class AgentServices:
    """Dependency container passed into `run_agent`."""

    devices: DeviceRepository
    readings: ReadingRepository
    alerts: AlertRepository
    llm: LLMClient
    # Injected clock. The agent resolves relative phrasing ("the last hour")
    # against this, so an eval can pin "now" and get a reproducible answer.
    now: Callable[[], datetime]


# --- SQLAlchemy-backed implementations --------------------------------------
#
# Thin adapters over the existing `/services/` functions. They are the reason
# the data plane needed no changes for the agent: the agent's shape is
# satisfied here rather than by reshaping the services every router uses.


class _OwnerScoped:
    """Base for the live repositories: one session, one authenticated owner.

    The owner is captured at construction from the authenticated request, and
    every method re-checks the `user_id` it is handed against it. Ownership is
    enforced twice — once here, and again by the `/services/` layer's
    `get_owned_device` — because this is the boundary where a mistake would be
    invisible.
    """

    def __init__(self, session: AsyncSession, owner: User) -> None:
        self._session = session
        self._owner = owner

    def _check(self, user_id: uuid.UUID) -> User:
        if user_id != self._owner.id:
            raise OwnershipError(
                "Repository is scoped to a different user than the one requested."
            )
        return self._owner


class SqlalchemyDeviceRepository(_OwnerScoped):
    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[DeviceResponse]:
        from app.services.device import list_devices

        owner = self._check(user_id)
        devices = await list_devices(self._session, owner)
        return [DeviceResponse.model_validate(device) for device in devices]


class SqlalchemyReadingRepository(_OwnerScoped):
    async def query(
        self,
        user_id: uuid.UUID,
        *,
        device_id: uuid.UUID,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> Sequence[ReadingResponse]:
        from app.services.reading import list_readings

        owner = self._check(user_id)
        readings = await list_readings(
            self._session, owner, device_id, start, end, limit
        )
        return [ReadingResponse.model_validate(reading) for reading in readings]

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
        from app.services.reading import aggregate_readings

        owner = self._check(user_id)
        return await aggregate_readings(
            self._session, owner, device_id, window, fn, start, end
        )


class SqlalchemyAlertRepository(_OwnerScoped):
    async def recent(
        self,
        user_id: uuid.UUID,
        *,
        since: datetime,
        device_id: uuid.UUID | None,
        limit: int,
    ) -> Sequence[AlertResponse]:
        from app.services.reading import list_alerts

        owner = self._check(user_id)
        return await list_alerts(self._session, owner, since, device_id, limit)
