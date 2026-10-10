"""The HTTP-backed repositories, driven against the real application.

These run every request through `httpx.ASGITransport` into the actual FastAPI
app, which resolves real routes, real auth dependencies, and real SQL against the
testcontainers Postgres. Nothing is mocked: a tenant-isolation assertion here is
checking the API's genuine behaviour, not a stub's.

That is the whole reason the MCP server was built as an HTTP client rather than a
second database consumer — the enforcement it relies on is the enforcement these
tests exercise.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.agent.services import OwnershipError
from app.core.security import create_access_token
from app.main import app
from app.mcp.client import DataBullAuthError, DataBullClient, DataBullError
from app.mcp.repositories import (
    HttpAlertRepository,
    HttpDeviceRepository,
    HttpReadingRepository,
)
from app.models import Device, User
from app.schemas.reading import AggregateFn, AggregateWindow

PASSWORD = "a-good-password"
NOW = datetime.now(UTC)


def build_client(**kwargs: object) -> DataBullClient:
    """A `DataBullClient` whose transport is the live ASGI app.

    `client` must be requested alongside this so the `get_db` override is
    installed; without it the app would open its own session and see none of the
    test's uncommitted rows.
    """
    return DataBullClient(
        "http://test",
        transport=ASGITransport(app=app),
        **kwargs,  # type: ignore[arg-type]  # kwargs are the credential variants
    )


@pytest_asyncio.fixture
async def api(client: AsyncClient, user: User) -> AsyncGenerator[DataBullClient, None]:
    """A client authenticated as `user` through the real login endpoint."""
    databull = build_client(email=user.email, password=PASSWORD)
    yield databull
    await databull.aclose()


# --- authentication ---------------------------------------------------------


async def test_logs_in_with_email_and_password(api: DataBullClient, user: User) -> None:
    assert await api.user_id() == user.id


async def test_a_wrong_password_is_an_auth_error(
    client: AsyncClient, user: User
) -> None:
    api = build_client(email=user.email, password="not-the-password")
    try:
        with pytest.raises(DataBullAuthError, match="rejected these credentials"):
            await api.user_id()
    finally:
        await api.aclose()


async def test_a_supplied_token_is_used_as_is(client: AsyncClient, user: User) -> None:
    api = build_client(token=create_access_token(user.id))
    try:
        assert await api.user_id() == user.id
    finally:
        await api.aclose()


async def test_constructing_without_any_credential_fails_fast() -> None:
    with pytest.raises(DataBullAuthError, match="Supply either a token"):
        DataBullClient("http://test")


async def test_an_unrenewable_token_says_so_rather_than_surfacing_a_bare_401(
    client: AsyncClient,
) -> None:
    """Token-only: there is no password to re-login with, so the error must explain."""
    api = build_client(token="not-a-real-token")
    try:
        with pytest.raises(DataBullAuthError, match="cannot be renewed"):
            await api.user_id()
    finally:
        await api.aclose()


async def test_a_stale_token_is_renewed_when_a_password_is_available(
    client: AsyncClient, user: User
) -> None:
    """The long-lived-session case: start with a dead token, recover silently."""
    api = build_client(token="stale", email=user.email, password=PASSWORD)
    try:
        assert await api.user_id() == user.id
    finally:
        await api.aclose()


async def test_a_non_auth_failure_carries_the_api_error_code(
    api: DataBullClient, client: AsyncClient
) -> None:
    with pytest.raises(DataBullError, match="422|validation"):
        # A malformed uuid is rejected by request validation, not by auth.
        await api.request("GET", "/readings", params={"device_id": "not-a-uuid"})


# --- repositories -----------------------------------------------------------


@pytest_asyncio.fixture
async def repos(
    api: DataBullClient,
) -> tuple[HttpDeviceRepository, HttpReadingRepository, HttpAlertRepository]:
    owner = await api.user_id()
    return (
        HttpDeviceRepository(api, owner),
        HttpReadingRepository(api, owner),
        HttpAlertRepository(api, owner),
    )


async def test_device_repository_lists_the_callers_devices(
    repos: tuple[HttpDeviceRepository, HttpReadingRepository, HttpAlertRepository],
    device: Device,
    user: User,
) -> None:
    devices, _, _ = repos
    found = await devices.list_for_user(user.id)
    assert [d.name for d in found] == [device.name]
    assert found[0].id == device.id


async def test_reading_repository_honours_the_time_window(
    repos: tuple[HttpDeviceRepository, HttpReadingRepository, HttpAlertRepository],
    device: Device,
    user: User,
    make_reading: Callable[..., Awaitable[object]],
) -> None:
    _, readings, _ = repos
    await make_reading(device, time=NOW - timedelta(hours=5), value=1.0)
    await make_reading(device, time=NOW - timedelta(minutes=5), value=2.0)

    recent = await readings.query(
        user.id,
        device_id=device.id,
        start=NOW - timedelta(hours=1),
        end=NOW + timedelta(minutes=1),
        limit=100,
    )
    assert [r.value for r in recent] == [2.0]

    everything = await readings.query(
        user.id, device_id=device.id, start=None, end=None, limit=100
    )
    assert len(everything) == 2


async def test_reading_repository_aggregates(
    repos: tuple[HttpDeviceRepository, HttpReadingRepository, HttpAlertRepository],
    device: Device,
    user: User,
    make_reading: Callable[..., Awaitable[object]],
) -> None:
    _, readings, _ = repos
    await make_reading(device, time=NOW - timedelta(minutes=30), value=10.0)
    await make_reading(device, time=NOW - timedelta(minutes=20), value=20.0)

    buckets = await readings.aggregate(
        user.id,
        device_id=device.id,
        window=AggregateWindow.DAY,
        fn=AggregateFn.AVG,
        start=NOW - timedelta(hours=2),
        end=NOW + timedelta(minutes=1),
    )
    assert len(buckets) == 1
    assert buckets[0].value == pytest.approx(15.0)


async def test_alert_repository_returns_threshold_breaches(
    api: DataBullClient,
    user: User,
    make_device: Callable[..., Awaitable[Device]],
    make_reading: Callable[..., Awaitable[object]],
) -> None:
    monitored = await make_device(user, name="Furnace-9", max_threshold=100.0)
    await make_reading(monitored, time=NOW - timedelta(minutes=10), value=250.0)
    await make_reading(monitored, time=NOW - timedelta(minutes=9), value=50.0)

    alerts = HttpAlertRepository(api, await api.user_id())
    breaches = await alerts.recent(
        user.id, since=NOW - timedelta(hours=1), device_id=None, limit=100
    )
    assert [a.value for a in breaches] == [250.0]
    assert breaches[0].bound == "max"
    assert breaches[0].device_name == "Furnace-9"


# --- isolation --------------------------------------------------------------


async def test_a_repository_refuses_a_foreign_user_id(
    repos: tuple[HttpDeviceRepository, HttpReadingRepository, HttpAlertRepository],
) -> None:
    devices, _, _ = repos
    with pytest.raises(OwnershipError):
        await devices.list_for_user(uuid.uuid4())


async def test_one_users_client_cannot_see_anothers_devices(
    client: AsyncClient,
    make_user: Callable[..., Awaitable[User]],
    make_device: Callable[..., Awaitable[Device]],
) -> None:
    """Isolation across the real HTTP boundary, not a stubbed one."""
    alice = await make_user(email="alice@example.com")
    bob = await make_user(email="bob@example.com")
    await make_device(alice, name="Alice-Pump")
    await make_device(bob, name="Bob-Pump")

    api = build_client(email=bob.email, password=PASSWORD)
    try:
        devices = HttpDeviceRepository(api, await api.user_id())
        found = await devices.list_for_user(bob.id)
        assert [d.name for d in found] == ["Bob-Pump"]
    finally:
        await api.aclose()
