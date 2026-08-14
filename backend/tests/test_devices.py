"""Device API tests.

The isolation tests are the important ones: they assert that a user cannot read,
enumerate, or delete another user's devices — and cannot even learn that a
device id is real.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models import Device, User

VALID_DEVICE = {"name": "Pump-3", "type": "flow", "unit": "L/min"}


def auth_header(user: User) -> dict[str, str]:
    """Per-request Authorization header for an arbitrary user."""
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


# --- POST /devices -----------------------------------------------------------------


async def test_create_device(authed_client: AsyncClient) -> None:
    resp = await authed_client.post("/devices", json=VALID_DEVICE)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Pump-3"
    assert body["type"] == "flow"
    assert body["unit"] == "L/min"
    assert body["min_threshold"] is None
    assert body["max_threshold"] is None
    assert uuid.UUID(body["id"])


async def test_create_device_with_thresholds(authed_client: AsyncClient) -> None:
    resp = await authed_client.post(
        "/devices",
        json={**VALID_DEVICE, "min_threshold": 10.0, "max_threshold": 90.0},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["min_threshold"] == 10.0
    assert resp.json()["max_threshold"] == 90.0


async def test_create_requires_authentication(client: AsyncClient) -> None:
    resp = await client.post("/devices", json=VALID_DEVICE)
    assert resp.status_code == 401
    assert resp.json()["code"] == "not_authenticated"


async def test_create_rejects_unknown_device_type(authed_client: AsyncClient) -> None:
    """`vibration` was removed from the enum, so it must not be accepted."""
    resp = await authed_client.post(
        "/devices", json={**VALID_DEVICE, "type": "vibration"}
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


async def test_create_rejects_inverted_thresholds(authed_client: AsyncClient) -> None:
    resp = await authed_client.post(
        "/devices",
        json={**VALID_DEVICE, "min_threshold": 90.0, "max_threshold": 10.0},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


async def test_create_rejects_blank_name(authed_client: AsyncClient) -> None:
    resp = await authed_client.post("/devices", json={**VALID_DEVICE, "name": ""})
    assert resp.status_code == 422
    assert resp.json()["field"] == "name"


async def test_duplicate_name_for_same_user_conflicts(
    authed_client: AsyncClient,
) -> None:
    first = await authed_client.post("/devices", json=VALID_DEVICE)
    assert first.status_code == 201
    second = await authed_client.post("/devices", json=VALID_DEVICE)
    assert second.status_code == 409
    assert second.json()["code"] == "device_name_taken"


async def test_same_name_for_different_users_is_allowed(
    authed_client: AsyncClient, make_user: Callable[..., Awaitable[User]]
) -> None:
    """Names are unique *per user*, not globally."""
    mine = await authed_client.post("/devices", json=VALID_DEVICE)
    assert mine.status_code == 201

    other = await make_user(email="other@example.com")
    theirs = await authed_client.post(
        "/devices", json=VALID_DEVICE, headers=auth_header(other)
    )
    assert theirs.status_code == 201
    assert theirs.json()["id"] != mine.json()["id"]


# --- GET /devices -------------------------------------------------------------------


async def test_list_is_empty_for_a_new_user(authed_client: AsyncClient) -> None:
    resp = await authed_client.get("/devices")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_is_ordered_by_name(authed_client: AsyncClient) -> None:
    await authed_client.post("/devices", json={**VALID_DEVICE, "name": "B-device"})
    await authed_client.post("/devices", json={**VALID_DEVICE, "name": "A-device"})
    resp = await authed_client.get("/devices")
    assert [d["name"] for d in resp.json()] == ["A-device", "B-device"]


async def test_list_returns_only_the_callers_devices(
    authed_client: AsyncClient,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
) -> None:
    """The core isolation guarantee for the collection endpoint."""
    await authed_client.post("/devices", json={**VALID_DEVICE, "name": "Mine"})

    stranger = await make_user(email="stranger@example.com")
    await make_device(stranger, name="Theirs")

    resp = await authed_client.get("/devices")
    assert [d["name"] for d in resp.json()] == ["Mine"]


async def test_list_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get("/devices")
    assert resp.status_code == 401


# --- GET /devices/{device_id} ---------------------------------------------------------------


async def test_get_own_device(authed_client: AsyncClient) -> None:
    created = await authed_client.post("/devices", json=VALID_DEVICE)
    device_id = created.json()["id"]

    resp = await authed_client.get(f"/devices/{device_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == device_id


async def test_get_another_users_device_returns_404_not_403(
    authed_client: AsyncClient,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
) -> None:
    """404, never 403.

    A 403 would confirm the id names a real device, which is exactly the signal
    an attacker needs to enumerate other people's resources. "Not yours" must be
    indistinguishable from "not there".
    """
    stranger = await make_user(email="stranger@example.com")
    their_device = await make_device(stranger, name="Not-Yours")

    resp = await authed_client.get(f"/devices/{their_device.id}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "device_not_found"


async def test_get_unknown_id_returns_the_same_404(
    authed_client: AsyncClient,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
) -> None:
    """A real-but-foreign id and a nonexistent id must look identical."""
    stranger = await make_user(email="stranger@example.com")
    their_device = await make_device(stranger, name="Not-Yours")

    foreign = await authed_client.get(f"/devices/{their_device.id}")
    missing = await authed_client.get(f"/devices/{uuid.uuid4()}")

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


async def test_get_rejects_a_malformed_id(authed_client: AsyncClient) -> None:
    resp = await authed_client.get("/devices/not-a-uuid")
    assert resp.status_code == 422


# --- PATCH /devices/{device_id} -------------------------------------------------------------


async def test_update_own_device(authed_client: AsyncClient) -> None:
    created = await authed_client.post("/devices", json=VALID_DEVICE)
    device_id = created.json()["id"]

    updated = await authed_client.patch(
        f"/devices/{device_id}",
        json={
            "name": "New Name",
            "min_threshold": 10.0,
            "max_threshold": 90.0,
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["id"] == device_id
    assert body["name"] == "New Name"
    assert body["min_threshold"] == 10.0
    assert body["max_threshold"] == 90.0


async def test_update_another_users_device_is_refused(
    authed_client: AsyncClient,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
) -> None:
    stranger = await make_user(email="stranger@example.com")
    their_device = await make_device(stranger, name="Not-Yours")

    resp = await authed_client.patch(
        f"/devices/{their_device.id}", json={"name": "New Name"}
    )
    assert resp.status_code == 404


async def test_update_requires_authentication(
    client: AsyncClient, device: Device
) -> None:
    resp = await client.patch(f"/devices/{device.id}", json={"name": "New Name"})
    assert resp.status_code == 401


async def test_update_rejects_inverted_thresholds(authed_client: AsyncClient) -> None:
    created = await authed_client.post("/devices", json=VALID_DEVICE)
    device_id = created.json()["id"]

    resp = await authed_client.patch(
        f"/devices/{device_id}", json={"min_threshold": 90.0, "max_threshold": 10.0}
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_error"


async def test_update_rejects_blank_name(authed_client: AsyncClient) -> None:
    created = await authed_client.post("/devices", json=VALID_DEVICE)
    device_id = created.json()["id"]

    updated = await authed_client.patch(f"/devices/{device_id}", json={"name": ""})
    assert updated.status_code == 422


async def test_update_enforces_max_length_on_name(authed_client: AsyncClient) -> None:
    created = await authed_client.post("/devices", json=VALID_DEVICE)
    device_id = created.json()["id"]

    updated = await authed_client.patch(
        f"/devices/{device_id}", json={"name": "x" * 256}
    )
    assert updated.status_code == 422


async def test_update_can_clear_a_threshold(authed_client: AsyncClient) -> None:
    created = await authed_client.post(
        "/devices",
        json={**VALID_DEVICE, "min_threshold": 10.0, "max_threshold": 90.0},
    )
    device_id = created.json()["id"]

    updated = await authed_client.patch(
        f"/devices/{device_id}", json={"min_threshold": None}
    )
    assert updated.status_code == 200
    assert updated.json()["min_threshold"] is None
    assert updated.json()["max_threshold"] == 90.0


async def test_update_rejects_duplicate_name_for_same_user(
    authed_client: AsyncClient,
) -> None:
    await authed_client.post("/devices", json=VALID_DEVICE)
    second = await authed_client.post(
        "/devices", json={**VALID_DEVICE, "name": "Other Name"}
    )

    resp = await authed_client.patch(
        f"/devices/{second.json()['id']}", json={"name": "Pump-3"}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "device_name_taken"


async def test_update_ignores_omitted_fields(authed_client: AsyncClient) -> None:
    created = await authed_client.post(
        "/devices",
        json={**VALID_DEVICE, "min_threshold": 10.0, "max_threshold": 90.0},
    )
    device_id = created.json()["id"]

    updated = await authed_client.patch(f"/devices/{device_id}", json={"name": "New"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "New"
    assert updated.json()["min_threshold"] == 10.0
    assert updated.json()["max_threshold"] == 90.0


# --- DELETE /devices/{device_id} -------------------------------------------------------------


async def test_delete_own_device(authed_client: AsyncClient) -> None:
    created = await authed_client.post("/devices", json=VALID_DEVICE)
    device_id = created.json()["id"]

    deleted = await authed_client.delete(f"/devices/{device_id}")
    assert deleted.status_code == 204

    assert (await authed_client.get(f"/devices/{device_id}")).status_code == 404


async def test_delete_another_users_device_is_refused_and_leaves_it_intact(
    authed_client: AsyncClient,
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
) -> None:
    stranger = await make_user(email="stranger@example.com")
    their_device = await make_device(stranger, name="Not-Yours")

    resp = await authed_client.delete(f"/devices/{their_device.id}")
    assert resp.status_code == 404

    # The device must still be there — a refused delete must not delete.
    still_there = (
        await db_session.execute(
            text("SELECT count(*) FROM devices WHERE id = :d"),
            {"d": their_device.id},
        )
    ).scalar_one()
    assert still_there == 1


async def test_delete_cascades_to_readings(
    authed_client: AsyncClient,
    db_session: AsyncSession,
    user: User,
    make_device: Callable[..., Awaitable[Device]],
    make_reading: Callable[..., Awaitable[object]],
) -> None:
    device = await make_device(user, name="Doomed")
    await make_reading(device, value=1.0)
    await make_reading(device, value=2.0)

    resp = await authed_client.delete(f"/devices/{device.id}")
    assert resp.status_code == 204

    remaining = (
        await db_session.execute(
            text("SELECT count(*) FROM readings WHERE device_id = :d"),
            {"d": device.id},
        )
    ).scalar_one()
    assert remaining == 0


async def test_delete_requires_authentication(
    client: AsyncClient, device: Device
) -> None:
    resp = await client.delete(f"/devices/{device.id}")
    assert resp.status_code == 401


# --- Response shape ---------------------------------------------------------


async def test_response_does_not_leak_owner_id(authed_client: AsyncClient) -> None:
    resp = await authed_client.post("/devices", json=VALID_DEVICE)
    assert "user_id" not in resp.json()
