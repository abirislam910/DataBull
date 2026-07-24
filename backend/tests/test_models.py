"""Behavioral tests for the ORM models against real Postgres/TimescaleDB."""

from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Device, DeviceType, User, Reading


async def test_round_trip(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
    make_reading: Callable[..., Awaitable[object]],
) -> None:
    user = await make_user(email="op@example.com")
    device = await make_device(user, name="Furnace-1", type=DeviceType.TEMPERATURE)
    await make_reading(device, value=25.5)

    count = (
        await db_session.execute(
            text("SELECT count(*) FROM readings WHERE device_id = :d"),
            {"d": device.id},
        )
    ).scalar_one()
    assert count == 1, "Expected one reading to be persisted in the database"


async def test_email_is_unique(
    make_user: Callable[..., Awaitable[User]],
) -> None:
    await make_user(email="dup@example.com")
    with pytest.raises(IntegrityError):
        await make_user(email="dup@example.com")


async def test_device_name_unique_per_user(
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
) -> None:
    user = await make_user()
    await make_device(user, name="Pump-3")
    with pytest.raises(IntegrityError):
        await make_device(user, name="Pump-3")


async def test_same_name_different_users_allowed(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
) -> None:
    user_a = await make_user()
    user_b = await make_user()
    await make_device(user_a, name="Pump-3")
    await make_device(user_b, name="Pump-3")
    device_ids = (
        (
            await db_session.execute(
                text("SELECT id FROM devices WHERE name = :n"),
                {"n": "Pump-3"},
            )
        )
        .scalars()
        .all()
    )
    assert len(device_ids) == 2, (
        "Devices with the same name for different users should be independent records"
    )


async def test_device_type_persisted_as_lowercase_value(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
) -> None:
    user = await make_user()
    device = await make_device(user, type=DeviceType.TEMPERATURE)
    # The DB should hold the enum *value* "temperature", not the member name.
    raw = (
        await db_session.execute(
            text("SELECT type::text FROM devices WHERE id = :d"),
            {"d": device.id},
        )
    ).scalar_one()
    assert raw == "temperature", "Expected the enum value to be persisted in lowercase"
    assert device.type is DeviceType.TEMPERATURE, (
        "Expected the ORM to return the enum member"
    )


async def test_thresholds_optional(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
) -> None:
    user = await make_user()
    device = await make_device(user, min_threshold=None, max_threshold=None)
    thresholds = (
        await db_session.execute(
            text("SELECT min_threshold, max_threshold FROM devices WHERE id = :d"),
            {"d": device.id},
        )
    ).one()
    assert thresholds.min_threshold is None, (
        "Expected min_threshold to be NULL in the database"
    )
    assert thresholds.max_threshold is None, (
        "Expected max_threshold to be NULL in the database"
    )


async def test_created_at_autopopulated(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
) -> None:
    user = await make_user()
    created_at = (
        await db_session.execute(
            text("SELECT created_at FROM users WHERE id = :u"), {"u": user.id}
        )
    ).scalar_one()
    assert created_at is not None, "Expected created_at to be autopopulated"


async def test_delete_device_cascades_to_readings(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
    make_reading: Callable[..., Awaitable[object]],
) -> None:
    user = await make_user()
    device = await make_device(user)
    await make_reading(device)
    await make_reading(device)

    await db_session.delete(device)
    await db_session.flush()

    remaining = (
        await db_session.execute(
            text("SELECT count(*) FROM readings WHERE device_id = :d"),
            {"d": device.id},
        )
    ).scalar_one()
    assert remaining == 0, "Expected all readings for the device to be deleted"


async def test_delete_user_cascades_to_devices(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
) -> None:
    user = await make_user()
    await make_device(user)

    await db_session.delete(user)
    await db_session.flush()

    remaining = (
        await db_session.execute(
            text("SELECT count(*) FROM devices WHERE user_id = :u"),
            {"u": user.id},
        )
    ).scalar_one()
    assert remaining == 0, "Expected all devices for the user to be deleted"


async def test_delete_user_cascades_to_readings(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
    make_reading: Callable[..., Awaitable[Reading]],
) -> None:
    user = await make_user()
    device = await make_device(user)
    await make_reading(device)

    await db_session.delete(user)
    await db_session.flush()

    remaining = (
        await db_session.execute(
            text("SELECT count(*) FROM readings WHERE device_id = :d"), {"d": device.id}
        )
    ).scalar_one()
    assert remaining == 0, "Expected all readings for the user to be deleted"


async def test_relationships_load_eagerly(
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
    make_reading: Callable[..., Awaitable[Reading]],
) -> None:
    user = await make_user()
    device = await make_device(user)
    reading = await make_reading(device)

    loaded = (
        await db_session.execute(
            select(User)
            .where(User.id == user.id)
            .options(selectinload(User.devices).selectinload(Device.readings))
        )
    ).scalar_one()

    assert len(loaded.devices) == 1, "Expected one device to be loaded for the user"
    assert loaded.devices[0].id == device.id, (
        "Loaded device ID does not match the created device ID"
    )
    assert len(loaded.devices[0].readings) == 1, (
        "Expected one reading to be loaded for the device"
    )
    # Reading has no surrogate `id`; its identity is its composite primary key
    # (time, device_id). Assert on both PK columns to confirm the exact row was
    # loaded through the relationship.
    loaded_reading = loaded.devices[0].readings[0]
    assert loaded_reading.device_id == reading.device_id, (
        "Loaded reading is not linked to the expected device"
    )
    assert loaded_reading.time == reading.time, (
        "Loaded reading time does not match the created reading"
    )
