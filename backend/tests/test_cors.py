"""CORS for a frontend served from a different origin than the API.

Most cases build a small app with a known allowlist rather than relying on the
process environment, so they behave the same on every machine. The last test
checks the real app's wiring against whatever the environment configures.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.cors import configure_cors
from app.core.errors import AuthErr, register_exception_handlers
from app.main import app as real_app

FRONTEND = "https://frontend.example.app"
STRANGER = "https://evil.example.com"


def build_app(origins: list[str]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    configure_cors(app, origins)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/private")
    async def private() -> None:
        raise AuthErr("Not authenticated.", "not_authenticated")

    @app.post("/stream")
    async def stream() -> StreamingResponse:
        async def frames() -> AsyncIterator[str]:
            yield 'data: {"type":"text","delta":"hi"}\n\n'
            yield 'data: {"type":"done"}\n\n'

        return StreamingResponse(frames(), media_type="text/event-stream")

    return app


def client_for(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def preflight_headers(origin: str) -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    }


def test_allowlist_is_parsed_from_a_comma_separated_setting() -> None:
    settings = Settings(
        secret_key="test",
        cors_allow_origins=" https://a.example.app/ , ,https://b.example.app",
    )
    # Whitespace and empty entries dropped, and the trailing slash stripped: a
    # browser's Origin header never has one, so it would otherwise never match.
    assert settings.cors_origins == ["https://a.example.app", "https://b.example.app"]


def test_no_origins_means_no_cors() -> None:
    assert Settings(secret_key="test").cors_origins == []


async def test_preflight_from_an_allowed_origin_is_approved() -> None:
    async with client_for(build_app([FRONTEND])) as client:
        resp = await client.options("/ping", headers=preflight_headers(FRONTEND))

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == FRONTEND
    allowed = resp.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed and "content-type" in allowed
    # The token travels in a header, not a cookie, so credentials stay off.
    assert "access-control-allow-credentials" not in resp.headers


async def test_preflight_from_another_origin_is_refused() -> None:
    async with client_for(build_app([FRONTEND])) as client:
        resp = await client.options("/ping", headers=preflight_headers(STRANGER))

    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers


async def test_a_normal_response_carries_the_allow_origin_header() -> None:
    async with client_for(build_app([FRONTEND])) as client:
        resp = await client.get("/ping", headers={"Origin": FRONTEND})

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == FRONTEND


async def test_a_401_carries_cors_headers_so_the_frontend_can_read_it() -> None:
    """The frontend signs the user out on a 401, but only if it can read it.

    Without CORS headers on the error, the browser hides the response from the
    page entirely and the app sees a generic network failure instead.
    """
    async with client_for(build_app([FRONTEND])) as client:
        resp = await client.get("/private", headers={"Origin": FRONTEND})

    assert resp.status_code == 401
    assert resp.json()["code"] == "not_authenticated"
    assert resp.headers["access-control-allow-origin"] == FRONTEND


async def test_a_streamed_response_carries_cors_headers_intact() -> None:
    async with client_for(build_app([FRONTEND])) as client:
        resp = await client.post("/stream", headers={"Origin": FRONTEND})

    assert resp.headers["access-control-allow-origin"] == FRONTEND
    assert resp.text.count("data: ") == 2


async def test_an_empty_allowlist_adds_no_middleware_behavior() -> None:
    async with client_for(build_app([])) as client:
        resp = await client.options("/ping", headers=preflight_headers(FRONTEND))

    assert "access-control-allow-origin" not in resp.headers


async def test_the_real_app_follows_the_configured_allowlist() -> None:
    """Checks main.py's wiring against whatever CORS_ALLOW_ORIGINS is set to."""
    origins = get_settings().cors_origins
    probe = origins[0] if origins else FRONTEND

    async with client_for(real_app) as client:
        resp = await client.get("/health", headers={"Origin": probe})

    if origins:
        assert resp.headers["access-control-allow-origin"] == probe
    else:
        assert "access-control-allow-origin" not in resp.headers
