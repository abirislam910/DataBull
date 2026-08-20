"""Device CRUD.

THE ONE RULE IN THIS MODULE: every statement is scoped to an owner. There is no
"get device by id" here — only "get *this user's* device by id". Keeping the
ownership filter inside the query (rather than fetching first and comparing
`device.user_id` afterwards) means a caller cannot forget the check: the row
simply is not in the result set.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError, NotFoundErr, DuplicateErr
from app.models import Device, User
from app.schemas.device import DeviceCreate, DeviceUpdate


async def create_device(
    session: AsyncSession, owner: User, data: DeviceCreate
) -> Device:
    """Register a device for `owner`, or raise 409 if the name is taken.

    Same insert-and-catch shape as signup: the `uq_devices_user_id_name`
    constraint is the only thing that can adjudicate concurrent creates
    atomically, so we let the database rule rather than pre-checking.
    """
    device = Device(
        user_id=owner.id,
        name=data.name,
        type=data.type,
        unit=data.unit,
        min_threshold=data.min_threshold,
        max_threshold=data.max_threshold,
    )
    session.add(device)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateErr(
            detail="You already have a device with that name.",
            code="device_name_taken",
            field="name",
        ) from exc

    await session.commit()
    return device


async def list_devices(session: AsyncSession, owner: User) -> Sequence[Device]:
    """Every device belonging to `owner`, alphabetically.

    Ordered by name for a stable list. Ordering by `created_at` would not be
    deterministic: Postgres' `now()` is transaction-start time, so devices
    created in one transaction share a timestamp.
    """
    result = await session.execute(
        select(Device).where(Device.user_id == owner.id).order_by(Device.name)
    )
    return result.scalars().all()


async def get_owned_device(
    session: AsyncSession, owner: User, device_id: uuid.UUID
) -> Device:
    """Fetch one of `owner`'s devices, or raise 404.

    Note both predicates are in the WHERE clause. Another user's device never
    enters the result set, so there is no loaded object to accidentally return.
    """
    result = await session.execute(
        select(Device).where(Device.id == device_id, Device.user_id == owner.id)
    )
    device = result.scalar_one_or_none()
    if device is None:
        raise NotFoundErr(
            detail="Device not found.",
            code="device_not_found",
        )
    return device


async def update_device(
    session: AsyncSession, owner: User, device_id: uuid.UUID, data: DeviceUpdate
) -> Device:
    """Update one of `owner`'s devices, or raise 404."""
    device = await get_owned_device(session, owner, device_id)
    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(device, field, value)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateErr(
            detail="You already have a device with that name.",
            code="device_name_taken",
            field="name",
        ) from exc
    await session.commit()
    return device


async def delete_device(
    session: AsyncSession, owner: User, device_id: uuid.UUID
) -> None:
    """Delete one of `owner`'s devices; its readings go with it.

    The readings are removed by the `ON DELETE CASCADE` on
    `readings.device_id`, so this stays a single statement no matter how many
    million rows the hypertable holds for that device.
    """
    device = await get_owned_device(session, owner, device_id)
    await session.delete(device)
    await session.commit()
