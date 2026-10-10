"""Authenticated HTTP client for the DataBull API.

The MCP server is a *client* of the API, not a second consumer of the database.
That is deliberate: the API already enforces ownership twice and answers 404
rather than 403 for another tenant's resources, so going over HTTP inherits all
of it. It also means no Postgres credentials ever leave the deployment, which is
the only shape in which a third party can run this server against their own
account.

Credentials come in two forms, and they are not equivalent:

* **email + password** — the client can mint its own access token and re-mint it
  when one expires, so a long-lived session survives unattended.
* **token** — scoped to its 24-hour lifetime and cannot be renewed, because the
  API issues no refresh tokens (a documented v1 limitation). When it expires the
  client says so plainly instead of surfacing an opaque 401.
"""

from __future__ import annotations

import uuid
from types import TracebackType
from typing import Any, Self

import httpx


class DataBullError(RuntimeError):
    """Any failure reaching or using the API."""


class DataBullAuthError(DataBullError):
    """Credentials were rejected, or a supplied token expired and cannot be renewed."""


class DataBullClient:
    """Talks to one DataBull deployment as one user.

    `transport` exists for tests: passing `httpx.ASGITransport(app=app)` runs
    every request against the real FastAPI application in-process, so the
    repositories above are exercised against genuine routing, auth, and SQL
    without a socket.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        email: str | None = None,
        password: str | None = None,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if token is None and (email is None or password is None):
            raise DataBullAuthError(
                "Supply either a token or an email and password to reach the API."
            )
        self._email = email
        self._password = password
        # A caller-supplied token is used as-is; one we minted ourselves can be
        # replaced on expiry. The distinction is what `_can_reauthenticate` reads.
        self._token = token
        # `transport=None` is httpx's own default, so it is passed through
        # directly rather than conditionally splatted — a `**dict` defeats
        # keyword matching and mypy cannot check the call at all.
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
        )
        self._user_id: uuid.UUID | None = None

    @property
    def _can_reauthenticate(self) -> bool:
        return self._email is not None and self._password is not None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _login(self) -> None:
        """Exchange email and password for an access token."""
        if self._email is None or self._password is None:
            raise DataBullAuthError("No password on file; cannot obtain a token.")
        response = await self._client.post(
            "/auth/login", json={"email": self._email, "password": self._password}
        )
        if response.status_code == 401:
            raise DataBullAuthError("The API rejected these credentials.")
        self._raise_for_status(response)
        body = response.json()
        token = body.get("access_token")
        if not isinstance(token, str):
            raise DataBullAuthError("The login response carried no access token.")
        self._token = token

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        # The API's documented error shape is `{detail, code, field?}`; fall back
        # to the status line when a proxy or a crash produces something else.
        detail = response.reason_phrase
        code = "unknown_error"
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = str(body.get("detail", detail))
                code = str(body.get("code", code))
        except ValueError:
            pass
        raise DataBullError(f"{response.status_code} {code}: {detail}")

    async def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        """Make one authenticated request, renewing the token once if it expired.

        The retry is deliberately single-shot: a second 401 after a fresh login
        means the credentials themselves are no longer good, and retrying in a
        loop would just hammer the login endpoint.
        """
        if self._token is None:
            await self._login()

        response = await self._send(method, path, params)
        if response.status_code == 401:
            if not self._can_reauthenticate:
                raise DataBullAuthError(
                    "The access token is no longer valid. It cannot be renewed "
                    "because the server was started with a token rather than a "
                    "password; restart with fresh credentials."
                )
            await self._login()
            response = await self._send(method, path, params)
            if response.status_code == 401:
                raise DataBullAuthError(
                    "The API rejected a freshly issued token; the account may be gone."
                )

        self._raise_for_status(response)
        return response.json()

    async def _send(
        self, method: str, path: str, params: dict[str, Any] | None
    ) -> httpx.Response:
        # `None`-valued params are dropped rather than serialized as "None",
        # which the API would reject as a malformed value.
        cleaned = (
            {key: value for key, value in params.items() if value is not None}
            if params
            else None
        )
        return await self._client.request(
            method,
            path,
            params=cleaned,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
        )

    async def user_id(self) -> uuid.UUID:
        """The id of the account these credentials belong to, fetched once.

        The repositories check the `user_id` they are handed against this, which
        mirrors `_OwnerScoped._check` in the in-process implementation. The API
        would enforce ownership regardless; this catches a harness bug locally
        instead of quietly querying as the wrong user.
        """
        if self._user_id is None:
            body = await self.request("GET", "/auth/me")
            self._user_id = uuid.UUID(str(body["id"]))
        return self._user_id
