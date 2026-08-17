"""The project's error contract.

SPEC.md fixes the error body as::

    {"detail": "...", "code": "machine_readable_string", "field"?: "field_name"}

FastAPI's stock `HTTPException` renders only `{"detail": ...}`, so `APIError`
adds the machine-readable `code` (what clients should branch on — prose in
`detail` is for humans and may be reworded at any time) and an optional `field`
for validation failures.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class APIError(Exception):
    """An error that renders in the project's documented shape."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        code: str,
        field: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code
        self.field = field
        self.headers = headers

    def to_body(self) -> dict[str, str]:
        body = {"detail": self.detail, "code": self.code}
        if self.field is not None:
            body["field"] = self.field
        return body


def AuthErr(detail: str, code: str) -> APIError:
    """Build a 401 carrying the RFC 6750 challenge header."""
    return APIError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        code=code,
        # Required by the spec for 401s on bearer-protected resources; tells the
        # client which scheme to retry with.
        headers={"WWW-Authenticate": "Bearer"},
    )


def NotFoundErr(detail: str, code: str) -> APIError:
    """Build a 404 error."""
    return APIError(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=detail,
        code=code,
    )


def DuplicateErr(detail: str, code: str, field: str) -> APIError:
    """Build a 409 error."""
    return APIError(
        status_code=status.HTTP_409_CONFLICT,
        detail=detail,
        code=code,
        field=field,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install handlers so every error leaves the API in the documented shape."""

    @app.exception_handler(APIError)
    async def _api_error_handler(_: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_body(),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Pydantic reports a list of errors; surface the first one's message and
        # the field it came from. `loc` looks like ("body", "password"), so the
        # last element is the field name.
        errors: Sequence[Any] = exc.errors()
        first: dict[str, Any] = dict(errors[0]) if errors else {}
        location = first.get("loc") or ()
        field = str(location[-1]) if location else None
        error = APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(first.get("msg", "Invalid request.")),
            code="validation_error",
            field=field,
        )
        return JSONResponse(status_code=error.status_code, content=error.to_body())

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # Catches framework-raised errors (404s, 405s) so even those carry a
        # `code` and clients never have to special-case a second error shape.
        error = APIError(
            status_code=exc.status_code,
            detail=str(exc.detail),
            code="http_error",
        )
        return JSONResponse(
            status_code=error.status_code,
            content=error.to_body(),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:

        error = APIError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal Server Error",
            code="internal_server_error",
        )
        return JSONResponse(status_code=error.status_code, content=error.to_body())
