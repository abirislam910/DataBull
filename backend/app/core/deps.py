"""Shared FastAPI dependencies.

`get_current_user` is the gate every protected route sits behind. Declaring it
as a dependency (rather than middleware) means the route signature itself states
its auth requirement, and FastAPI surfaces the lock icon in the OpenAPI docs.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import User
from app.services.auth import get_user_by_id

# Parses `Authorization: Bearer <token>`. auto_error=False makes a missing or
# malformed header yield None instead of FastAPI's own 403 with a bare
# `{"detail": ...}` body — we want our documented `{detail, code}` shape and a
# 401 instead.
bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str, code: str) -> APIError:
    """Build a 401 carrying the RFC 6750 challenge header."""
    return APIError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        code=code,
        # Required by the spec for 401s on bearer-protected resources; tells the
        # client which scheme to retry with.
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Resolve the caller's `User` from the bearer token, or raise 401.

    Three gates, in order:
      1. A bearer token was actually presented.
      2. It verifies — correct signature, not expired, parseable `sub`.
      3. The user it names still exists.

    Step 3 is not redundant. A JWT is a self-contained assertion the server
    never recorded, so a token minted for an account that has since been deleted
    still passes signature and expiry checks. Only the database can say whether
    that user is real *now*, which is why we look it up on every request rather
    than trusting the claims alone.
    """
    if credentials is None:
        raise _unauthorized("Not authenticated.", "not_authenticated")

    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise _unauthorized("Invalid or expired token.", "invalid_token")

    user = await get_user_by_id(session, user_id)
    if user is None:
        raise _unauthorized("Invalid or expired token.", "invalid_token")

    return user


# Alias so routes can write `current_user: CurrentUser` instead of repeating the
# full Annotated[...] spelling.
CurrentUser = Annotated[User, Depends(get_current_user)]
