"""Alembic migration environment.

Runs synchronously on purpose: migrations are a short, serial, offline-friendly
task, so the extra machinery of an async engine buys nothing here. psycopg 3
drives both — `create_async_engine` for the app, plain `create_engine` for this
file — from the very same URL, so there is no second DSN to keep in sync.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.db.base import Base

# Importing the models package registers User, Device, and Reading on
# Base.metadata. Without this line autogenerate would see an empty schema and
# happily propose dropping every table. `noqa: F401` — imported for the import
# side effect, not to be referenced.
import app.models  # noqa: F401

config = context.config

# Set up Python logging per alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# What autogenerate diffs against, and what create_all would build.
target_metadata = Base.metadata

# Single source of truth for the connection string.
database_url = get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting (``alembic ... --sql``)."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect with a short-lived sync engine and run migrations in a txn."""
    # NullPool: a migration run makes one connection and exits, so pooling is
    # pointless overhead.
    connectable = create_engine(database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
