"""The `users` table — one row per registered account."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Imported for type-checking only. At runtime SQLAlchemy resolves the "Device"
# relationship target from its class registry by name, so we never actually need
# the symbol here — importing it for real would create a circular import
# (device.py imports User right back).
if TYPE_CHECKING:
    from app.models.device import Device


class User(Base):
    __tablename__ = "users"

    # Application-generated UUID. Using a Python-side `default=uuid.uuid4` (rather
    # than the Postgres `gen_random_uuid()` server default) means the id exists
    # the instant we construct the object, before any flush — convenient for
    # building relationships and asserting in tests without a round-trip.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # `unique=True` adds a UNIQUE constraint; `index=True` adds a btree index.
    # Login looks users up by email, so the index earns its keep.
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    # bcrypt hashes are 60 chars; 255 leaves headroom for any future scheme.
    # Never stores the plaintext password — hashing happens in the service layer.
    password_hash: Mapped[str] = mapped_column(String(255))

    # `DateTime(timezone=True)` maps to Postgres `timestamptz`. `server_default`
    # means the DEFAULT lives in the DDL (`now()`), so the database stamps the
    # row even on inserts that bypass the ORM (e.g. bulk SQL, the simulator).
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # One user owns many devices. `cascade="all, delete-orphan"` tells the ORM to
    # delete a device when it's removed from this collection; `passive_deletes`
    # defers the actual cascade to the database's ON DELETE CASCADE (defined on
    # the FK in device.py) instead of loading every child to delete it row by row.
    devices: Mapped[list[Device]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
