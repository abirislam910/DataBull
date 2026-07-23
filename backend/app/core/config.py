"""Application settings.

A single Pydantic v2 `BaseSettings` object is the one place that reads the
environment. Every value can be overridden by an env var (or a `.env` file) of
the same name, case-insensitively, so the same code runs against local Docker,
CI's testcontainer, and production without edits.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # SQLAlchemy URL. The `+psycopg` driver is psycopg 3, which SQLAlchemy can
    # drive both synchronously (Alembic migrations) and asynchronously (the app)
    # from this one URL. Default points at the docker-compose Postgres/TimescaleDB.
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/telemetry"
    )

    # Echo emitted SQL to the logger. Off by default; handy when debugging.
    sql_echo: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, constructed once and cached.

    Cached so importing modules share one instance and the env is read a single
    time. Tests can call `get_settings.cache_clear()` to force a reload.
    """
    return Settings()
