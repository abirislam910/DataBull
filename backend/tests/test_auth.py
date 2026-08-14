"""Auth tests: the crypto primitives, then the HTTP flow end to end."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import jwt
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.models import User

# --- Password hashing -------------------------------------------------------


def test_hash_is_not_the_password() -> None:
    hashed = hash_password("correct horse battery staple")
    assert "correct horse battery staple" not in hashed
    assert hashed.startswith("$argon2id$"), "expected an argon2 hash"


def test_same_password_hashes_differently_each_time() -> None:
    """A random per-hash salt means identical passwords get unrelated hashes."""
    first = hash_password("same-password")
    second = hash_password("same-password")
    assert first != second, "hashes should differ because of the salt"
    # ...and both still verify.
    assert verify_password("same-password", first), "first hash should verify"
    assert verify_password("same-password", second), "second hash should verify"


def test_verify_rejects_wrong_password() -> None:
    hashed = hash_password("the-real-password")
    assert verify_password("the-real-password", hashed) is True, (
        "correct password should verify"
    )
    assert verify_password("not-the-password", hashed) is False, (
        "incorrect password should not verify"
    )


# --- JWT --------------------------------------------------------------------


def test_token_round_trip() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id)
    assert decode_access_token(token) == user_id, (
        "decoded token should yield the original user ID"
    )


def test_token_payload_is_readable_without_the_secret() -> None:
    """A JWT is signed, NOT encrypted — never put anything private in it."""
    user_id = uuid.uuid4()
    token = create_access_token(user_id)

    settings = get_settings()

    claims = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
        options={"verify_signature": False},
    )

    assert claims["sub"] == str(user_id), "the `sub` claim should be the user ID"
    assert "exp" in claims and "iat" in claims and "iss" in claims, (
        "the token should have standard claims"
    )


def test_expired_token_is_rejected() -> None:
    settings = get_settings()
    expired = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "exp": datetime.now(UTC) - timedelta(minutes=1),
            "iat": datetime.now(UTC) - timedelta(hours=2),
        },
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )
    assert decode_access_token(expired) is None, "expired token should be rejected"


def test_token_signed_with_another_secret_is_rejected() -> None:
    """The signature is the whole security boundary."""
    settings = get_settings()
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "exp": datetime.now(UTC) + timedelta(hours=1)},
        "an-attackers-guess-at-the-secret",
        algorithm=settings.jwt_algorithm,
    )
    assert decode_access_token(forged) is None, (
        "token signed with another secret should be rejected"
    )


def test_tampered_token_is_rejected() -> None:
    """Editing the payload invalidates the signature computed over it."""
    token = create_access_token(uuid.uuid4())
    header, payload, signature = token.split(".")
    # Flip a character in the payload segment.
    mutated = payload[:-2] + ("A" if payload[-2] != "A" else "B") + payload[-1]
    assert decode_access_token(f"{header}.{mutated}.{signature}") is None, (
        "tampered token should be rejected"
    )


def test_garbage_token_is_rejected() -> None:
    assert decode_access_token("not-even-a-jwt") is None, (
        "garbage token should be rejected"
    )


# --- POST /auth/signup -----------------------------------------------------------------


async def test_signup_returns_a_usable_token(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/signup",
        json={"email": "new@example.com", "password": "a-good-password"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert decode_access_token(body["access_token"]) is not None, (
        "the signup endpoint should return a valid access token"
    )


async def test_signup_rejects_duplicate_email(client: AsyncClient) -> None:
    payload = {"email": "dup@example.com", "password": "a-good-password"}
    first = await client.post("/auth/signup", json=payload)
    assert first.status_code == 201, first.text

    second = await client.post("/auth/signup", json=payload)
    assert second.status_code == 409, second.text
    assert second.json()["detail"] == "An account with that email already exists.", (
        "expected correct response detail for duplicate signup"
    )
    assert second.json()["code"] == "email_already_registered", (
        "expected correct response code for duplicate signup"
    )


async def test_signup_rejects_short_password(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/signup",
        json={"email": "short@example.com", "password": "1234567"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_error", (
        "expected validation_error code for short password"
    )
    assert body["field"] == "password", (
        "expected password error field for short password"
    )


async def test_signup_rejects_invalid_email(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/signup",
        json={"email": "not-an-email", "password": "a-good-password"},
    )
    assert resp.status_code == 422
    assert resp.json()["field"] == "email", (
        "expected email error field for invalid email"
    )


async def test_signup_does_not_store_plaintext_password(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await client.post(
        "/auth/signup",
        json={"email": "hashed@example.com", "password": "super-secret-pw"},
    )

    stored = (
        await db_session.execute(select(User).where(User.email == "hashed@example.com"))
    ).scalar_one()
    assert stored.password_hash != "super-secret-pw", (
        "the password should be hashed, not stored in plaintext"
    )
    assert verify_password("super-secret-pw", stored.password_hash), (
        "the stored hash should verify the original password"
    )


async def test_signup_normalizes_email_case(client: AsyncClient) -> None:
    first = await client.post(
        "/auth/signup",
        json={"email": "MixedCase@Example.com", "password": "a-good-password"},
    )
    assert first.status_code == 201
    # The same address in different case is the same account.
    second = await client.post(
        "/auth/signup",
        json={"email": "mixedcase@example.com", "password": "a-good-password"},
    )
    assert second.status_code == 409, second.text


# --- POST /auth/login ------------------------------------------------------------------


async def test_login_succeeds_with_correct_credentials(client: AsyncClient) -> None:
    await client.post(
        "/auth/signup",
        json={"email": "login@example.com", "password": "a-good-password"},
    )
    resp = await client.post(
        "/auth/login",
        json={"email": "login@example.com", "password": "a-good-password"},
    )
    assert resp.status_code == 200, resp.text
    assert decode_access_token(resp.json()["access_token"]) is not None, (
        "the login endpoint should return a valid access token"
    )


async def test_login_is_case_insensitive_on_email(client: AsyncClient) -> None:
    await client.post(
        "/auth/signup",
        json={"email": "caseless@example.com", "password": "a-good-password"},
    )
    resp = await client.post(
        "/auth/login",
        json={"email": "CASELESS@example.com", "password": "a-good-password"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    assert decode_access_token(token) is not None, (
        "the login endpoint should return a valid access token even if the email case differs"
    )


async def test_login_rejects_wrong_password(client: AsyncClient) -> None:
    await client.post(
        "/auth/signup",
        json={"email": "wrongpw@example.com", "password": "a-good-password"},
    )
    resp = await client.post(
        "/auth/login",
        json={"email": "wrongpw@example.com", "password": "not-the-password"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == "Incorrect email or password.", (
        "expected correct response detail for wrong password"
    )
    assert resp.json()["code"] == "invalid_credentials", (
        "expected invalid_credentials code for wrong password"
    )


async def test_login_does_not_reveal_whether_the_email_exists(
    client: AsyncClient,
) -> None:
    """Unknown email and wrong password must be indistinguishable to a caller."""
    await client.post(
        "/auth/signup",
        json={"email": "known@example.com", "password": "a-good-password"},
    )
    wrong_password = await client.post(
        "/auth/login",
        json={"email": "known@example.com", "password": "not-the-password"},
    )
    unknown_email = await client.post(
        "/auth/login",
        json={"email": "nobody@example.com", "password": "a-good-passwordd"},
    )
    assert wrong_password.status_code == unknown_email.status_code == 401, (
        "both wrong password and unknown email should return 401"
    )
    assert wrong_password.json() == unknown_email.json(), (
        "both wrong password and unknown email should return the same response body"
    )


# --- GET /auth/me -----------------------------------------------------------


async def test_me_returns_the_authenticated_user(
    authed_client: AsyncClient, user: User
) -> None:
    resp = await authed_client.get("/auth/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(user.id), (
        "expected the authenticated user's ID in the response"
    )
    assert body["email"] == user.email, (
        "expected the authenticated user's email in the response"
    )
    assert "created_at" in body, (
        "expected the authenticated user's creation timestamp in the response"
    )


async def test_me_never_exposes_the_password_hash(authed_client: AsyncClient) -> None:
    resp = await authed_client.get("/auth/me")
    assert "password_hash" not in resp.json(), (
        "the /auth/me endpoint should never expose the password hash"
    )


async def test_me_requires_a_token(client: AsyncClient) -> None:
    resp = await client.get("/auth/me")
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "not_authenticated", (
        "expected not_authenticated code when no token is provided"
    )
    assert resp.headers["WWW-Authenticate"] == "Bearer", (
        "expected WWW-Authenticate header to indicate Bearer token is required"
    )


async def test_me_rejects_a_garbage_token(client: AsyncClient) -> None:
    client.headers["Authorization"] = "Bearer not-a-real-token"
    resp = await client.get("/auth/me")
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "invalid_token", (
        "expected invalid_token code for garbage token"
    )


async def test_me_rejects_a_token_for_a_deleted_user(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: Callable[..., Awaitable[User]],
) -> None:
    """A signed token stays cryptographically valid after its user is gone.

    Nothing about the token changes when the account is deleted — this is why
    `get_current_user` looks the user up on every request instead of trusting
    the claims alone.
    """
    doomed = await make_user(email="doomed@example.com")
    token = create_access_token(doomed.id)

    await db_session.delete(doomed)
    await db_session.flush()

    client.headers["Authorization"] = f"Bearer {token}"
    resp = await client.get("/auth/me")
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "invalid_token", (
        "expected invalid_token code for token of deleted user"
    )


# --- POST /auth/me/delete -----------------------------------------------------------


async def test_delete_me_removes_the_user(
    authed_client: AsyncClient, db_session: AsyncSession, user: User
) -> None:
    resp = await authed_client.post(
        "/auth/me/delete", json={"password": "a-good-password"}
    )
    assert resp.status_code == 204, resp.text

    # The user should be gone from the database.
    deleted = await db_session.get(User, user.id)
    assert deleted is None, "the user should be deleted from the database"


async def test_delete_me_rejects_wrong_password(
    authed_client: AsyncClient, db_session: AsyncSession, user: User
) -> None:
    resp = await authed_client.post(
        "/auth/me/delete", json={"password": "not-the-password"}
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "invalid_credentials", (
        "expected invalid_credentials code for wrong password"
    )

    # The user should still exist in the database.
    still_there = await db_session.get(User, user.id)
    assert still_there is not None, "the user should not be deleted with wrong password"


async def test_delete_me_requires_a_token(client: AsyncClient) -> None:
    resp = await client.post("/auth/me/delete", json={"password": "a-good-password"})
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "not_authenticated", (
        "expected not_authenticated code when no token is provided"
    )
    assert resp.headers["WWW-Authenticate"] == "Bearer", (
        "expected WWW-Authenticate header to indicate Bearer token is required"
    )
