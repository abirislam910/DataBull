"""Shared pytest fixtures.

The suite runs against a real TimescaleDB in a throwaway testcontainer — never
SQLite, because the schema relies on a hypertable and a native enum that SQLite
cannot represent. The container starts once per session and its schema is built
by running the actual Alembic migration, so the migration is exercised on every
test run. Each test then executes inside a transaction that is rolled back at the
end, which isolates tests from one another without rebuilding the schema.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.core.config import get_settings
from app.db.session import get_db
from app.main import app
from app.models import Device, DeviceType, Reading, User

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    """Build an Alembic Config with absolute paths (cwd-independent)."""
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Start TimescaleDB once, migrate it to head, and yield its URL.

    Sync + session-scoped on purpose: it hands back a plain string, so nothing
    async is shared across event loops. `driver="psycopg"` forces the psycopg 3
    DSN (`postgresql+psycopg://…`) that both our async engine and Alembic use.
    """
    with PostgresContainer("timescale/timescaledb:latest-pg16", driver="psycopg") as pg:
        url = pg.get_connection_url()
        # Redirect the app + Alembic at this container. get_settings() is cached,
        # so clear it or env.py would keep the localhost default it read earlier.
        original_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        get_settings.cache_clear()

        command.upgrade(_alembic_config(), "head")
        try:
            yield url
        finally:
            if original_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = original_url
            get_settings.cache_clear()


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    """Alembic config pointed at the already-migrated test container."""
    return _alembic_config()


@pytest_asyncio.fixture
async def db_session(database_url: str) -> AsyncGenerator[AsyncSession, None]:
    """An `AsyncSession` wrapped in an always-rolled-back outer transaction.

    A fresh engine is created *inside* this (function-scoped) fixture so it is
    built and disposed within the test's own event loop — sidestepping the
    "future attached to a different loop" errors that a session-scoped async
    engine would cause. `join_transaction_mode="create_savepoint"` lets code
    under test call `commit()` (released as a savepoint) while the outer
    transaction below still discards everything on rollback.
    """
    engine = create_async_engine(database_url)
    conn = await engine.connect()
    transaction = await conn.begin()
    session = AsyncSession(
        bind=conn,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """An httpx client bound to the ASGI app, with `get_db` overridden.

    Routes resolve `get_db` to the same transaction-bound session the test uses,
    so HTTP-level assertions and direct DB assertions see one consistent view
    that is rolled back afterwards. (No DB-backed routes exist yet, but this is
    the harness the router/auth phases will build on.)
    """

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# --- Factories --------------------------------------------------------------
# Return async callables so a test can build as many rows as it needs. They
# `flush` (not `commit`) so the rows are visible within the test's savepoint but
# vanish when the outer transaction rolls back.


@pytest.fixture
def make_user(db_session: AsyncSession) -> Callable[..., Awaitable[User]]:
    created = 0

    async def _make(**overrides: object) -> User:
        nonlocal created
        created += 1
        user = User(
            email=overrides.get("email", f"user{created}@example.com"),
            password_hash=overrides.get("password_hash", "not-a-real-hash"),
        )
        db_session.add(user)
        await db_session.flush()
        return user

    return _make


@pytest.fixture
def make_device(db_session: AsyncSession) -> Callable[..., Awaitable[Device]]:
    async def _make(user: User, **overrides: object) -> Device:
        device = Device(
            user_id=user.id,
            name=overrides.get("name", "Pump-3"),
            type=overrides.get("type", DeviceType.FLOW),
            unit=overrides.get("unit", "L/min"),
            min_threshold=overrides.get("min_threshold"),
            max_threshold=overrides.get("max_threshold"),
        )
        db_session.add(device)
        await db_session.flush()
        return device

    return _make


@pytest.fixture
def make_reading(db_session: AsyncSession) -> Callable[..., Awaitable[Reading]]:
    async def _make(device: Device, **overrides: object) -> Reading:
        reading = Reading(
            device_id=device.id,
            time=overrides.get("time", datetime.now(timezone.utc)),
            value=overrides.get("value", 18.0),
        )
        db_session.add(reading)
        await db_session.flush()
        return reading

    return _make


# --- Single-instance conveniences ------------------------------------------


@pytest_asyncio.fixture
async def user(make_user: Callable[..., Awaitable[User]]) -> User:
    return await make_user()


@pytest_asyncio.fixture
async def device(make_device: Callable[..., Awaitable[Device]], user: User) -> Device:
    return await make_device(user)


@pytest_asyncio.fixture
async def reading(
    make_reading: Callable[..., Awaitable[Reading]], device: Device
) -> Reading:
    return await make_reading(device)
