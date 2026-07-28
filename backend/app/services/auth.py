"""Account creation and credential checking.

Per CLAUDE.md every database write lives in a service, so routers stay a thin
translation layer between HTTP and this module.
"""

from __future__ import annotations

import uuid

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import APIError
from app.core.security import dummy_verify, hash_password, verify_password
from app.models import User


def normalize_email(email: str) -> str:
    """Lower-case an address so `Op@Example.com` and `op@example.com` are one account.

    The `users.email` unique index is byte-comparing and case-sensitive, so
    without this a user could register the same mailbox several times and then
    be unable to guess which capitalization to log in with.
    """
    return email.strip().lower()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(
        select(User).where(User.email == normalize_email(email))
    )
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def create_user(session: AsyncSession, email: str, password: str) -> User:
    """Register a new account, or raise `APIError` if the email is taken.

    Inserts first and catches the unique-violation instead of doing a "does this
    email exist?" SELECT beforehand. A check-then-insert has a race: two
    concurrent signups can both see "available" and both proceed. The database
    constraint is the only thing that can arbitrate atomically, so we let it.
    """
    user = User(
        email=normalize_email(email),
        password_hash=hash_password(password),
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        # The failed statement leaves the transaction unusable until it is
        # unwound, so roll back before raising.
        await session.rollback()
        raise APIError(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
            code="email_already_registered",
            field="email",
        ) from exc

    await session.commit()
    return user


async def authenticate_user(
    session: AsyncSession, email: str, password: str
) -> User | None:
    """Return the user if the credentials are valid, else None.

    Returns a bare None for both "no such email" and "wrong password" so the
    caller cannot accidentally tell them apart — and neither can an attacker.
    """
    user = await get_user_by_email(session, email)
    if user is None:
        # Deliberately hash anyway. Skipping the ~100ms argon2 work here would
        # make "no such account" answer noticeably faster than "wrong password",
        # turning response latency into a user-enumeration oracle.
        dummy_verify()
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
