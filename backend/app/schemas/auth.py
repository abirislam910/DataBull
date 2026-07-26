"""Request/response models for the auth endpoints.

Kept separate from the ORM on purpose: these define the public HTTP contract,
and nothing here should ever expose `password_hash`. Note there is no schema
containing the hash at all — the type system makes leaking it hard.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import BCRYPT_MAX_PASSWORD_BYTES, password_too_long

# SPEC: minimum 8 characters, no complexity rules — modern NIST guidance favours
# length over forced symbol/case mixtures, which mostly push users toward
# predictable substitutions ("Password1!").
MIN_PASSWORD_LENGTH = 8


class Credentials(BaseModel):
    """Shared body for signup and login: `{email, password}`."""

    # EmailStr runs real syntax validation (via email-validator) instead of a
    # hand-rolled regex, and normalizes the address.
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        """Reject passwords bcrypt cannot consume.

        `Field(min_length=...)` counts characters, but bcrypt's ceiling is 72
        *bytes*. Checking the encoded length here turns what would otherwise be
        a 500 from the hasher into a clean 422 with a useful message.
        """
        if password_too_long(value):
            raise ValueError(
                f"Password must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes "
                "when UTF-8 encoded."
            )
        return value


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
