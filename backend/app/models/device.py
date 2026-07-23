"""The `devices` table — a logical sensor registered by a user."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.reading import Reading
    from app.models.user import User


class DeviceType(str, enum.Enum):
    """The kinds of sensor we support.

    Subclassing `str` makes each member behave like its string value, so it
    serializes straight to JSON as "temperature" etc. and Pydantic accepts the
    raw string from request bodies. Member names are UPPER_SNAKE per our
    conventions; the lowercase *values* are what we persist and expose.
    """

    TEMPERATURE = "temperature"
    PRESSURE = "pressure"
    FLOW = "flow"


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        # "name unique per user" from the spec — a composite UNIQUE, not a plain
        # one. Two different users may both own a device called "Pump-3".
        UniqueConstraint("user_id", "name", name="uq_devices_user_id_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # FK to the owner. `ondelete="CASCADE"` writes ON DELETE CASCADE into the DDL
    # so deleting a user wipes their devices at the database level (the matching
    # half of `passive_deletes` on User.devices). Indexed because every
    # per-user query and the FK join filter on it.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(255))

    # Native Postgres ENUM named "device_type". By default SQLAlchemy would
    # persist the member *names* (e.g. "TEMPERATURE"); `values_callable` tells it
    # to store the lowercase *values* instead, matching the API contract. The
    # same mapping is used on read to turn "temperature" back into the member.
    type: Mapped[DeviceType] = mapped_column(
        SAEnum(
            DeviceType,
            name="device_type",
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        )
    )

    # Display unit: "°C", "kPa", "L/min".
    unit: Mapped[str] = mapped_column(String(32))

    # Optional alert bounds. `float | None` (not `Optional[float]`) per our style
    # rule; the `| None` is what makes SQLAlchemy infer the column is NULLable.
    min_threshold: Mapped[float | None] = mapped_column(Float)
    max_threshold: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # The other side of User.devices.
    user: Mapped[User] = relationship(back_populates="devices")

    # A device owns its readings. Same cascade story as User.devices: the ORM
    # marks them for deletion, the database actually does it via ON DELETE CASCADE.
    readings: Mapped[list[Reading]] = relationship(
        back_populates="device",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
