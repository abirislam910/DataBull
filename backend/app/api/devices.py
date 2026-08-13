"""Device routes. Every one of them is protected and scoped to the caller.

`CurrentUser` does double duty: it rejects unauthenticated requests, and the
`User` it resolves is the ownership filter passed into every service call.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.core.deps import CurrentUser, DbSession
from app.schemas.device import DeviceCreate, DeviceResponse
from app.services.device import (
    create_device,
    delete_device,
    get_owned_device,
    list_devices,
)

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
async def create(
    payload: DeviceCreate, session: DbSession, current_user: CurrentUser
) -> DeviceResponse:
    """Register a new device owned by the caller."""
    device = await create_device(session, current_user, payload)
    return DeviceResponse.model_validate(device)


@router.get("", response_model=list[DeviceResponse])
async def index(session: DbSession, current_user: CurrentUser) -> list[DeviceResponse]:
    """List the caller's devices. Never anyone else's."""
    devices = await list_devices(session, current_user)
    return [DeviceResponse.model_validate(device) for device in devices]


@router.get("/{device_id}", response_model=DeviceResponse)
async def show(
    device_id: uuid.UUID, session: DbSession, current_user: CurrentUser
) -> DeviceResponse:
    """Fetch one device by id — 404 unless the caller owns it."""
    device = await get_owned_device(session, current_user, device_id)
    return DeviceResponse.model_validate(device)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def destroy(
    device_id: uuid.UUID, session: DbSession, current_user: CurrentUser
) -> None:
    """Delete a device and, by cascade, all of its readings."""
    await delete_device(session, current_user, device_id)
