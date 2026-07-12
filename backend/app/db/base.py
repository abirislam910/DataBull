"""Declarative base shared by every ORM model.

All models inherit from `Base`, which means they all register their tables on
one shared `MetaData` object. Alembic reads that same `MetaData` to diff the
models against the live database, so anything not reachable from `Base` is
invisible to autogenerate.

The `MetaData` here carries an explicit naming convention. Without it, SQLAlchemy
lets the database invent names for indexes and constraints, which (a) differ
across engines and (b) leave Alembic unable to emit a stable `DROP CONSTRAINT`
later because it can't predict the name. Pinning the templates makes every
migration deterministic and reviewable.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Each key is a constraint kind; the value is the name template SQLAlchemy fills
# in from the table/column it applies to. "ix" = index, "uq" = unique,
# "ck" = check, "fk" = foreign key, "pk" = primary key.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Root of the ORM hierarchy. Owns the shared metadata + naming convention."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
