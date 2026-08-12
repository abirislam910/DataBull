"""Smoke test for the ASGI app + test client harness."""

from httpx import AsyncClient


async def test_health_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200, "Health check endpoint did not return 200 OK"
    assert resp.json() == {"status": "ok"}, (
        "Health check endpoint did not return expected JSON response"
    )


async def test_api_throws_correct_validation_errors(client: AsyncClient) -> None:
    """Login has the same validation rules as signup."""
    resp = await client.post(
        "/auth/login",
    )
    print(resp.json())
    assert resp.status_code == 422, "expected a 422 for missing body"
    body = resp.json()
    assert body["code"] == "validation_error", (
        "expected a validation_error code for missing body"
    )
