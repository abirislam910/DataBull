"""Application settings.

A single Pydantic v2 `BaseSettings` object is the one place that reads the
environment. Every value can be overridden by an env var (or a `.env` file) of
the same name, case-insensitively, so the same code runs against local Docker,
CI's testcontainer, and production without edits.
"""

from functools import lru_cache
from typing import Self

from pydantic import model_validator
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

    # --- Auth -------------------------------------------------------------
    # The HMAC key that signs and verifies every JWT. Deliberately has NO
    # default: anyone holding this value can mint a token impersonating any
    # user, so a shipped placeholder would be a backdoor. Missing it fails the
    # app at startup rather than quietly accepting forged tokens.
    # Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"
    secret_key: str

    # HS256 = HMAC-SHA256, a *symmetric* signature: the same secret both signs
    # and verifies. Fine here because one service does both. (Asymmetric RS256
    # would let other services verify without holding the signing key.)
    jwt_algorithm: str = "HS256"

    # 24-hour expiry per the spec. No refresh tokens in v1, so this is the
    # entire session lifetime — the user re-logs in after it lapses.
    access_token_expire_minutes: int = 60 * 24

    # SPEC: minimum 8 characters, no complexity rules — modern NIST guidance favours
    # length over forced symbol/case mixtures, which mostly push users toward
    # predictable substitutions ("Password1!").
    min_password_length: int = 8

    # A bulk request is capped so one call cannot exhaust memory or hold a
    # transaction open indefinitely. Clients paginate above this.

    max_bulk_readings: int = 10000

    # Default and ceiling for `limit` on read endpoints, so an unbounded query can
    # never try to serialize an entire hypertable.
    default_reading_limit: int = 1000
    max_reading_limit: int = 10000

    max_aggregate_buckets: int = 1000

    @model_validator(mode="after")
    def validate_limits(self) -> Self:
        """Check that the default limit is not greater than the max limit."""
        if self.default_reading_limit > self.max_reading_limit:
            raise ValueError("default_reading_limit cannot exceed max_reading_limit")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, constructed once and cached.

    Cached so importing modules share one instance and the env is read a single
    time. Tests can call `get_settings.cache_clear()` to force a reload.
    """
    # The ignore below is needed because mypy sees `secret_key` as a required
    # constructor argument, while pydantic-settings fills it from the
    # environment at runtime. Passing it explicitly would defeat the point. A
    # missing value still raises ValidationError at startup, which is what we
    # want — the ignore silences the type checker, not the runtime check.
    return Settings()  # type: ignore[call-arg]
