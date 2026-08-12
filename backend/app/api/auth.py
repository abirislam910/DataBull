"""Auth routes: signup, login, and the current-user lookup.

Thin by design — each handler translates HTTP to a service call and back. The
password never leaves this process: it arrives, gets hashed or compared, and is
discarded. It is never logged and never stored in plaintext.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core.deps import CurrentUser, DbSession
from app.core.errors import APIError, AuthErr
from app.core.security import create_access_token, verify_password
from app.schemas.auth import (
    Credentials,
    DeleteAccountRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth import authenticate_user, create_user, delete_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED
)
async def signup(credentials: Credentials, session: DbSession) -> TokenResponse:
    """Create an account and return a token, so the client is logged in at once."""
    user = await create_user(session, credentials.email, credentials.password)
    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/login", response_model=TokenResponse)
async def login(credentials: Credentials, session: DbSession) -> TokenResponse:
    """Exchange email + password for a 24-hour access token."""
    user = await authenticate_user(session, credentials.email, credentials.password)
    if user is None:
        # One message for both "unknown email" and "wrong password". Saying
        # which was wrong would let anyone probe the endpoint to discover who
        # has an account here.
        raise AuthErr(
            detail="Incorrect email or password.",
            code="invalid_credentials",
        )
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserResponse)
async def read_current_user(current_user: CurrentUser) -> UserResponse:
    """Return the authenticated user. Useful for the frontend to rehydrate state."""
    return UserResponse.model_validate(current_user)


@router.post("/me/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_current_user(
    body: DeleteAccountRequest, current_user: CurrentUser, session: DbSession
) -> None:
    if not verify_password(body.password, current_user.password_hash):
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password.",
            code="invalid_credentials",
        )
    await delete_user(session, current_user)
