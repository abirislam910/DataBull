"""The MCP surface: what tools are published, and what they do when called.

Tool dispatch is exercised by injecting a runtime whose client points at the live
ASGI app, so a `call_tool` here travels through the real routes, the real auth
dependency, and real SQL — the same path a connected agent would take, minus the
JSON-RPC framing.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from mcp.server.mcpserver.exceptions import ToolError

from app.agent.services import AgentServices
from app.agent.tool_schemas import TOOL_SCHEMAS
from app.agent.tools import TOOLS, ToolContext
from app.main import app
from app.mcp import server as mcp_server
from app.mcp.client import DataBullClient
from app.mcp.repositories import (
    HttpAlertRepository,
    HttpDeviceRepository,
    HttpReadingRepository,
)
from app.models import Device, User

PASSWORD = "a-good-password"
NOW = datetime.now(UTC)


@pytest_asyncio.fixture
async def wired(client: AsyncClient, user: User) -> AsyncGenerator[None, None]:
    """Install a runtime backed by the live app, and tear it down afterwards.

    The module-level runtime is a deliberate singleton in production — one process
    serves one account — so a test must set and clear it rather than build a
    second one.
    """
    databull = DataBullClient(
        "http://test",
        email=user.email,
        password=PASSWORD,
        transport=ASGITransport(app=app),
    )
    owner = await databull.user_id()
    services = AgentServices(
        devices=HttpDeviceRepository(databull, owner),
        readings=HttpReadingRepository(databull, owner),
        alerts=HttpAlertRepository(databull, owner),
        llm=mcp_server._UnusedLLM(),  # type: ignore[arg-type]
        now=lambda: NOW,
    )
    mcp_server._runtime = mcp_server._Runtime(
        client=databull,
        context=ToolContext(user_id=owner, services=services, max_result_bytes=65_536),
    )
    yield
    mcp_server._runtime = None
    await databull.aclose()


def text_of(result: object) -> str:
    """Pull the text content out of a CallToolResult."""
    content = getattr(result, "content", [])
    return "".join(getattr(block, "text", "") for block in content)


# --- published surface ------------------------------------------------------


async def test_publishes_exactly_the_agent_tools() -> None:
    published = {tool.name for tool in await mcp_server.mcp.list_tools()}
    assert published == set(TOOLS)
    assert published == {schema["name"] for schema in TOOL_SCHEMAS}


async def test_no_write_tool_is_exposed() -> None:
    """Read-only by design: nothing here should be able to mutate an account."""
    published = {tool.name for tool in await mcp_server.mcp.list_tools()}
    forbidden = {"create_device", "delete_device", "ingest_reading", "delete_readings"}
    assert published.isdisjoint(forbidden)


async def test_tool_descriptions_are_reused_from_the_agent_schemas() -> None:
    """The prose is prompt engineering; it should not be retyped and drift."""
    by_name = {schema["name"]: schema for schema in TOOL_SCHEMAS}
    for tool in await mcp_server.mcp.list_tools():
        assert tool.description == by_name[tool.name]["description"]


async def test_schemas_are_flat_and_carry_argument_descriptions() -> None:
    """A nested `args` wrapper would be awkward for every connecting client."""
    tools = {tool.name: tool for tool in await mcp_server.mcp.list_tools()}

    aggregate = tools["aggregate_window"].input_schema
    assert set(aggregate["required"]) == {"device_id", "window", "fn", "start", "end"}
    assert aggregate["properties"]["device_id"]["description"]
    # The enums resolve to the backend's real values rather than a restatement.
    assert set(aggregate["$defs"]["AggregateWindow"]["enum"]) == {"1h", "1d", "1w"}
    assert set(aggregate["$defs"]["AggregateFn"]["enum"]) == {
        "avg",
        "min",
        "max",
        "p95",
    }

    assert tools["list_devices"].input_schema["properties"] == {}


# --- dispatch ---------------------------------------------------------------


async def test_list_devices_reads_the_real_account(wired: None, device: Device) -> None:
    result = await mcp_server.mcp.call_tool("list_devices", {})
    rows = json.loads(text_of(result))
    assert [row["name"] for row in rows] == [device.name]
    assert rows[0]["device_id"] == str(device.id)


async def test_query_readings_round_trips_through_http(
    wired: None,
    device: Device,
    make_reading: Callable[..., Awaitable[object]],
) -> None:
    await make_reading(device, time=NOW - timedelta(minutes=10), value=7.5)

    result = await mcp_server.mcp.call_tool(
        "query_readings",
        {
            "device_id": str(device.id),
            "start": (NOW - timedelta(hours=1)).isoformat(),
            "end": (NOW + timedelta(minutes=1)).isoformat(),
        },
    )
    rows = json.loads(text_of(result))
    assert [row["value"] for row in rows] == [7.5]


async def test_aggregate_window_round_trips_through_http(
    wired: None,
    device: Device,
    make_reading: Callable[..., Awaitable[object]],
) -> None:
    await make_reading(device, time=NOW - timedelta(minutes=30), value=10.0)
    await make_reading(device, time=NOW - timedelta(minutes=20), value=20.0)

    result = await mcp_server.mcp.call_tool(
        "aggregate_window",
        {
            "device_id": str(device.id),
            "window": "1d",
            "fn": "avg",
            "start": (NOW - timedelta(hours=2)).isoformat(),
            "end": (NOW + timedelta(minutes=1)).isoformat(),
        },
    )
    rows = json.loads(text_of(result))
    assert rows[0]["value"] == pytest.approx(15.0)


async def test_get_recent_alerts_round_trips_through_http(
    wired: None,
    user: User,
    make_device: Callable[..., Awaitable[Device]],
    make_reading: Callable[..., Awaitable[object]],
) -> None:
    hot = await make_device(user, name="Furnace-9", max_threshold=100.0)
    await make_reading(hot, time=NOW - timedelta(minutes=5), value=300.0)

    result = await mcp_server.mcp.call_tool(
        "get_recent_alerts", {"since": (NOW - timedelta(hours=1)).isoformat()}
    )
    rows = json.loads(text_of(result))
    assert [row["device_name"] for row in rows] == ["Furnace-9"]


async def test_an_unknown_device_is_an_error_not_fabricated_data(
    wired: None, device: Device
) -> None:
    """A failed outcome must reach the client through MCP's error channel.

    `execute_tool` never raises, so without re-raising, the error text would be
    returned as ordinary content and the client's model would read it as data.
    The SDK signals a tool failure by raising `ToolError`, not by returning a
    result flagged as an error.
    """
    with pytest.raises(ToolError, match="query_readings"):
        await mcp_server.mcp.call_tool(
            "query_readings",
            {
                "device_id": str(uuid.uuid4()),
                "start": (NOW - timedelta(hours=1)).isoformat(),
                "end": NOW.isoformat(),
            },
        )


async def test_a_malformed_argument_is_rejected_before_any_request(
    wired: None,
) -> None:
    """Validation happens against the published schema, ahead of any HTTP call."""
    with pytest.raises(ToolError, match="valid UUID"):
        await mcp_server.mcp.call_tool(
            "query_readings",
            {"device_id": "not-a-uuid", "start": "nope", "end": "nope"},
        )


# --- runtime construction ---------------------------------------------------


async def test_missing_credentials_fail_with_an_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("DATABULL_TOKEN", "DATABULL_EMAIL", "DATABULL_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(mcp_server, "_runtime", None)

    from app.mcp.client import DataBullAuthError

    with pytest.raises(DataBullAuthError, match="DATABULL_TOKEN"):
        await mcp_server._get_runtime()


async def test_the_unused_llm_placeholder_raises_if_a_turn_is_ever_started() -> None:
    with pytest.raises(NotImplementedError, match="tools only"):
        mcp_server._UnusedLLM().stream_turn()
