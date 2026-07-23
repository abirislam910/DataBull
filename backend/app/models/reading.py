"""The `readings` table — one sensor sample.

This becomes a TimescaleDB *hypertable*: Postgres sees an ordinary table, but
Timescale transparently partitions it into chunks by `time` (1-day chunks per
the spec). The `SELECT create_hypertable('readings', 'time')` call is NOT
expressed here — it can't be, it's Timescale-specific DDL — and lives in the
Alembic migration instead. This model only describes the relational shape that
the hypertable is built on top of.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.device import Device


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (
        # The hot query is "readings for device X between start and end"
        # (GET /readings?device_id=&start=&end=). The primary key below leads
        # with `time`, so it can't serve a device-first lookup efficiently. This
        # composite (device_id, time) index does, and because device_id is its
        # leading column it also satisfies the spec's "device_id indexed"
        # requirement — no separate single-column index needed.
        Index("ix_readings_device_id_time", "device_id", "time"),
    )

    # Composite primary key (time, device_id). A hypertable's partitioning column
    # MUST be part of every unique constraint/PK, which is exactly why `time`
    # leads the key. `time` is the sensor timestamp;
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    # Second half of the PK. ON DELETE CASCADE so dropping a device drops its
    # readings at the DB level (pairs with passive_deletes on Device.readings).
    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )

    # The measured value, Postgres double precision.
    value: Mapped[float] = mapped_column(Float)

    device: Mapped[Device] = relationship(back_populates="readings")
