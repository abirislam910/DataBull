# DataBull
Industrial Time-Series API with an AI Operator's Assistant

## Getting started

```bash
cp backend/.env.example backend/.env      # then fill in SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"
docker compose up
```

The API refuses to start without `SECRET_KEY`. That is deliberate: the value
signs every access token, so a shipped placeholder would let anyone mint a token
for any account. Use a different key per environment.

## Authentication

JWT bearer tokens, HS256, 24-hour expiry.

```
POST /auth/signup  {email, password}  -> {access_token, token_type: "bearer"}
POST /auth/login   {email, password}  -> {access_token, token_type: "bearer"}
GET  /auth/me      Authorization: Bearer <token> -> {id, email, created_at}
```

Passwords are hashed with bcrypt (via `pwdlib`) and must be at least 8
characters. There are no composition rules — current NIST guidance favours
length over forced symbol/case mixes. bcrypt reads at most 72 bytes of input, so
longer passwords are rejected at validation rather than silently truncated.

### Documented tradeoffs

**Token in memory only.** The frontend keeps the token in React context, never
in `localStorage` or `document.cookie`. Anything readable from JavaScript is
exfiltratable by an XSS payload; a token held only in a closure is not. The cost
is that a tab reload loses it and the user logs in again.

**No refresh tokens in v1.** A 24-hour access token is the whole session. When
it expires, the user logs in again. Refresh tokens would smooth that over but
add rotation, storage, and revocation machinery that v1 does not need.

**Stateless tokens cannot be revoked.** The server stores no record of issued
tokens — it re-verifies the signature on each request instead. So a token stays
valid until it expires, even if the user logs out elsewhere. Deleting a user
*does* lock them out immediately, because `get_current_user` looks the account
up on every request. Revoking a specific outstanding token would require a
denylist, which is deliberately out of scope for v1. Rotating `SECRET_KEY`
invalidates every outstanding token at once.

**Login does not reveal whether an email is registered.** Wrong password and
unknown account return byte-identical 401s, and the unknown-account path still
runs a bcrypt comparison so response timing does not leak the difference either.
