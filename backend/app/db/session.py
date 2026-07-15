"""Async engine and session factory.

The engine owns the connection pool and is created exactly once per process.
`AsyncSessionLocal` mints a fresh `AsyncSession` per unit of work, and `get_db`
is the FastAPI dependency that hands one session to a request and disposes of it
afterwards. Importing this module never opens a connection — the async engine
connects lazily on first use — so it's safe to import without a running database.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()

# One engine (= one pool) for the whole process. `pool_pre_ping` sends a cheap
# liveness check before handing out a pooled connection, which sidesteps the
# "server closed the connection unexpectedly" errors that otherwise appear after
# a DB restart or an idle timeout.
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.sql_echo,
    pool_pre_ping=True,
)

# `expire_on_commit=False` keeps attributes readable after `commit()`. Under
# async this matters: the default would expire loaded attributes, and touching
# one would trigger a *lazy* reload — an implicit I/O that async sessions forbid,
# raising `MissingGreenlet`. Turning it off makes returning ORM objects from a
# request safe without an extra refresh round-trip.
AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield a session, then close it.

    The `async with` block guarantees the session is closed (and any open
    transaction rolled back) when the request finishes, even on error. Services
    own their transaction boundaries by calling `commit()` explicitly; this
    dependency deliberately does not commit for them.
    """
    async with AsyncSessionLocal() as session:
        yield session
