"""Password hashing and JWT minting/verification.

Deliberately dependency-light: no database, no FastAPI. Everything here is a
pure function of its arguments plus settings, which keeps the security-critical
logic easy to reason about and to unit-test.

WHY HASHING AND SIGNING ARE DIFFERENT JOBS
------------------------------------------
* Hashing (argon2) is **one-way**. We store `hash(password)` so that a database
  leak does not hand an attacker anyone's password. There is no "unhash".
* Signing (JWT/HMAC) is **verification of authorship**. The token's payload is
  readable by anyone; the signature only proves *we* issued it and that nobody
  edited it in transit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import get_settings

# A tuple of hashers: the first is used for new hashes, the rest stay valid for
# verifying old ones. That ordering is what makes future migrations a
# one-line change instead of a forced password reset for every user.
_password_hash = PasswordHash((Argon2Hasher(),))

# Verifying a password is intentionally slow (~40ms of key stretching), so an
# attacker who steals the table cannot test billions of guesses per second. That
# same cost is why a login for a *nonexistent* email must still do the work —
# see `_DUMMY_HASH` below.
_DUMMY_HASH = _password_hash.hash("a-password-that-is-never-valid")


def hash_password(password: str) -> str:
    """Return an Argon2 hash (salt included) for storage in `users.password_hash`.

    Argon2 generates a fresh random salt per call and embeds it in the output,
    which is why hashing the same password twice yields different strings — and
    why two users with identical passwords have unrelated hashes.
    """
    return _password_hash.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Check a candidate password against a stored hash."""
    return _password_hash.verify(plain_password, password_hash)


def dummy_verify() -> None:
    """Burn the same CPU time a real password check would.

    Called on the "email not found" branch of login. Without it, a failed login
    for an unregistered address returns measurably faster than one for a
    registered address, and that timing difference alone lets an attacker
    enumerate which emails have accounts.
    """
    _password_hash.verify("a-password-that-is-never-valid", _DUMMY_HASH)


def create_access_token(subject: uuid.UUID) -> str:
    """Mint a signed JWT identifying `subject` (the user's id).

    The result is three base64url segments joined by dots:

        header . payload . signature
        eyJhbGci...  eyJzdWIi...  4pZ8x...

    * header  — {"alg": "HS256", "typ": "JWT"}
    * payload — our claims, below
    * signature — HMAC-SHA256(header + "." + payload, SECRET_KEY)

    CRITICAL: the payload is base64-encoded, NOT encrypted. Anyone holding the
    token can read every claim in it. Never put a password, a secret, or
    anything private in here. The signature makes the payload *tamper-evident*,
    not *confidential*: flip a byte of the payload and the signature no longer
    matches, so verification fails — but reading it needs no key at all.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        # "sub" (subject) — who the token is about. RFC 7519 requires this to be
        # a string, so the UUID is stringified here and parsed back on decode.
        "sub": str(subject),
        # "exp" (expiration) — the instant the token stops being accepted. This
        # is the ONLY thing bounding a stolen token's usefulness, because a
        # stateless JWT cannot be revoked: the server keeps no record of issued
        # tokens, so there is nothing to delete. 24h per the spec.
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        # "iat" (issued at) — useful for auditing and for a future
        # "invalidate everything issued before X" check.
        "iat": now,
        "iss": "DataBull",
    }
    return jwt.encode(claims, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> uuid.UUID | None:
    """Verify a JWT and return the user id it asserts, or None if unusable.

    `jwt.decode` does two things that matter, and it does them BEFORE we trust
    any claim:
      1. Recomputes the signature with SECRET_KEY and compares — a forged or
         edited token fails here.
      2. Checks `exp` against the clock — an expired token fails here.

    Passing `algorithms=[...]` explicitly is a security requirement, not a
    formality: it pins which algorithm we accept. Without the pin, a token whose
    header says {"alg": "none"} could be honoured as validly signed — the
    classic JWT bypass.

    Returns None for every failure mode (bad signature, expired, missing `sub`,
    malformed uuid) so callers surface one indistinguishable 401 and leak
    nothing about *why* a token was rejected.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.exceptions.PyJWTError:
        return None

    subject = payload.get("sub")
    if not isinstance(subject, str):
        return None
    try:
        return uuid.UUID(subject)
    except ValueError:
        return None
