"""Smoke test for the ASGI app + test client harness."""

from httpx import AsyncClient


async def test_health_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200, "Health check endpoint did not return 200 OK"
    assert resp.json() == {"status": "ok"}, (
        "Health check endpoint did not return expected JSON response"
    )
