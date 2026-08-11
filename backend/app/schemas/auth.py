"""Request/response models for the auth endpoints.

Kept separate from the ORM on purpose: these define the public HTTP contract,
and nothing here should ever expose `password_hash`. Note there is no schema
containing the hash at all — the type system makes leaking it hard.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.config import get_settings

settings = get_settings()


class Credentials(BaseModel):
    """Shared body for signup and login: `{email, password}`."""

    # EmailStr runs real syntax validation (via email-validator) instead of a
    # hand-rolled regex, and normalizes the address.
    email: EmailStr
    password: str = Field(min_length=settings.min_password_length)


class TokenResponse(BaseModel):
    """What signup and login return: `{access_token, token_type}`."""

    access_token: str
    # Literal "bearer" per RFC 6750 — it tells the client to send the token as
    # `Authorization: Bearer <token>`.
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Public view of a user: `{id, email, created_at}`. Never the hash."""

    # from_attributes lets FastAPI build this straight from the SQLAlchemy
    # object's attributes rather than requiring a dict.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    created_at: datetime
