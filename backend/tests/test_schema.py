"""Tests for the schema produced by the migration.

These focus on the things only a real TimescaleDB can confirm: that `readings`
is genuinely a hypertable, the enum has exactly our three values, the index set
is precisely what we declared, and the models still agree with the migration.
"""

from datetime import timedelta

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def test_readings_is_a_hypertable(db_session: AsyncSession) -> None:
    dims = (
        await db_session.execute(
            text(
                "SELECT num_dimensions FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'readings'"
            )
        )
    ).scalar_one()
    assert dims == 1


async def test_readings_chunk_interval_is_one_day(db_session: AsyncSession) -> None:
    interval = (
        await db_session.execute(
            text(
                "SELECT time_interval FROM timescaledb_information.dimensions "
                "WHERE hypertable_name = 'readings'"
            )
        )
    ).scalar_one()
    assert interval == timedelta(days=1)


async def test_device_type_enum_values(db_session: AsyncSession) -> None:
    values = (
        (
            await db_session.execute(
                text("SELECT unnest(enum_range(NULL::device_type))::text")
            )
        )
        .scalars()
        .all()
    )
    assert values == ["temperature", "pressure", "flow"]


async def test_readings_has_only_declared_indexes(db_session: AsyncSession) -> None:
    # If TimescaleDB's default time index leaked in, this would have three rows.
    names = (
        (
            await db_session.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'readings' ORDER BY indexname"
                )
            )
        )
        .scalars()
        .all()
    )
    assert names == ["ix_readings_device_id_time", "pk_readings"]


def test_migration_matches_models(alembic_config: Config) -> None:
    """`alembic check` errors if the live (migrated) schema and the ORM models
    diverge. The `alembic_config` fixture depends on `database_url`, so the
    container is already migrated to head when this runs.
    """
    command.check(alembic_config)
