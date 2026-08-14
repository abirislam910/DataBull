"""Request/response models for the devices endpoints.

`user_id` is deliberately absent from `DeviceResponse`: every device the API
will ever hand back belongs to the caller, so echoing the owner adds noise and
nothing else.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import DeviceType


class DeviceCreate(BaseModel):
    """Body for `POST /devices`."""

    name: str = Field(min_length=1, max_length=255)
    # Typed as the enum, so FastAPI rejects anything outside
    # temperature|pressure|flow with a 422 before the service ever runs.
    type: DeviceType
    unit: str = Field(min_length=1, max_length=32)

    # Optional alert bounds. Absent means "no alerting on that side".
    min_threshold: float | None = None
    max_threshold: float | None = None

    @model_validator(mode="after")
    def thresholds_must_be_ordered(self) -> Self:
        """Reject an inverted band, which could never produce a sane alert.

        Runs in "after" mode because it needs both fields parsed — a field
        validator only sees one at a time.
        """
        if (
            self.min_threshold is not None
            and self.max_threshold is not None
            and self.min_threshold >= self.max_threshold
        ):
            raise ValueError("min_threshold must be less than max_threshold.")
        return self


class DeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    min_threshold: float | None = None
    max_threshold: float | None = None

    @model_validator(mode="after")
    def thresholds_must_be_ordered(self) -> Self:
        """Reject an inverted band, which could never produce a sane alert.

        Runs in "after" mode because it needs both fields parsed — a field
        validator only sees one at a time.
        """
        if (
            self.min_threshold is not None
            and self.max_threshold is not None
            and self.min_threshold >= self.max_threshold
        ):
            raise ValueError("min_threshold must be less than max_threshold.")
        return self


class DeviceResponse(BaseModel):
    """Public view of a device."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: DeviceType
    unit: str
    min_threshold: float | None
    max_threshold: float | None
    created_at: datetime
