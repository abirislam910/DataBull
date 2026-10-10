"""DataBull as an MCP server: the four telemetry tools, for any MCP client.

The same handlers the in-app assistant uses, reached over HTTP instead of a
database session. `app/agent/tools.py` is imported unchanged — validation,
serialization, the 200-character summaries, and the never-raise contract all come
from there, so a fix benefits both consumers.

Read-only by design. All four tools are reads, and the write endpoints
(device creation, ingest, delete) are deliberately not exposed: these tools are
invoked by someone else's agent, and an accidental `DELETE /readings` is not
recoverable. Writes, if they ever land, belong behind an explicit opt-in.

**stdout belongs to the protocol.** Under the stdio transport the JSON-RPC stream
*is* this process's stdout, so every diagnostic here goes to stderr. A stray
`print` would corrupt the session.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Final

from mcp.server import MCPServer
from pydantic import Field

from app.agent.services import AgentServices
from app.agent.tool_schemas import TOOL_SCHEMAS
from app.agent.tools import ToolContext, execute_tool
from app.mcp.client import DataBullAuthError, DataBullClient, DataBullError
from app.mcp.repositories import (
    HttpAlertRepository,
    HttpDeviceRepository,
    HttpReadingRepository,
)
from app.mcp.settings import load_settings
from app.schemas.reading import AggregateFn, AggregateWindow

# The descriptions are reused rather than retyped. They are prompt text — the
# phrasing is what steers a model away from `query_readings` when it wants an
# average — and that steering is just as useful to a client's model as to ours.
_SCHEMAS: Final[dict[str, dict[str, Any]]] = {
    schema["name"]: schema for schema in TOOL_SCHEMAS
}


def _tool_description(tool: str) -> str:
    return str(_SCHEMAS[tool]["description"])


def _arg_description(tool: str, arg: str) -> str:
    return str(_SCHEMAS[tool]["input_schema"]["properties"][arg]["description"])


class _UnusedLLM:
    """Placeholder for `AgentServices.llm`, which this server never uses.

    `AgentServices` bundles the model with the repositories because its other
    consumer, `run_agent`, drives a turn loop. An MCP server exposes tools and
    nothing else — the *client's* model is the model — so there is nothing to put
    here. Raising rather than passing `None` keeps the mistake loud if a future
    change starts a turn from inside this process.
    """

    @property
    def model(self) -> str:
        return "unused"

    def stream_turn(self, **_: object) -> object:
        raise NotImplementedError(
            "The MCP server exposes tools only; the connecting client supplies the model."
        )


@dataclass(frozen=True)
class _Runtime:
    client: DataBullClient
    context: ToolContext


_runtime: _Runtime | None = None
_lock = asyncio.Lock()


async def _get_runtime() -> _Runtime:
    """Build the API client and repositories once, on first tool call.

    Lazily rather than at import: a client launches this process eagerly and may
    never call a tool, and failing at import would give the user a dead server
    with no diagnostic. The lock matters because an agent may well fire several
    tool calls in parallel on its first turn, and each would otherwise log in.
    """
    global _runtime
    async with _lock:
        if _runtime is None:
            settings = load_settings()
            if not settings.has_credentials:
                raise DataBullAuthError(
                    "Set DATABULL_TOKEN, or DATABULL_EMAIL and DATABULL_PASSWORD."
                )
            client = DataBullClient(
                settings.api_url,
                token=settings.token,
                email=settings.email,
                password=settings.password,
                timeout=settings.timeout_seconds,
            )
            owner = await client.user_id()
            services = AgentServices(
                devices=HttpDeviceRepository(client, owner),
                readings=HttpReadingRepository(client, owner),
                alerts=HttpAlertRepository(client, owner),
                llm=_UnusedLLM(),  # type: ignore[arg-type]  # see _UnusedLLM
                now=lambda: datetime.now(UTC),
            )
            _runtime = _Runtime(
                client=client,
                context=ToolContext(
                    user_id=owner,
                    services=services,
                    max_result_bytes=settings.tool_result_max_bytes,
                ),
            )
    return _runtime


async def _run(tool: str, arguments: dict[str, Any]) -> str:
    """Dispatch one tool and return its payload, or raise so the client sees an error.

    `execute_tool` never raises — it reports failures as outcomes whose text is
    written to be read by a model. MCP has its own error channel, so a failed
    outcome is re-raised here: a client that cannot tell success from failure
    would feed an error string back to its model as though it were data.
    """
    runtime = await _get_runtime()
    outcome = await execute_tool(runtime.context, tool, arguments)
    if outcome.failed:
        raise DataBullError(outcome.payload)
    return outcome.payload


mcp = MCPServer(
    name="databull",
    title="DataBull telemetry",
    version="0.1.0",
    instructions=(
        "Read-only access to one DataBull account's sensor fleet. Call "
        "list_devices first: the other tools need a device_id, and the names it "
        "returns are the only real ones. Answer only from tool results — never "
        "invent readings or device names."
    ),
)


@mcp.tool(name="list_devices", description=_tool_description("list_devices"))
async def list_devices() -> str:
    return await _run("list_devices", {})


@mcp.tool(name="query_readings", description=_tool_description("query_readings"))
async def query_readings(
    device_id: Annotated[
        uuid.UUID, Field(description=_arg_description("query_readings", "device_id"))
    ],
    start: Annotated[
        datetime, Field(description=_arg_description("query_readings", "start"))
    ],
    end: Annotated[
        datetime, Field(description=_arg_description("query_readings", "end"))
    ],
    limit: Annotated[
        int,
        Field(ge=1, le=1000, description=_arg_description("query_readings", "limit")),
    ] = 1000,
) -> str:
    return await _run(
        "query_readings",
        {
            "device_id": str(device_id),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": limit,
        },
    )


@mcp.tool(name="aggregate_window", description=_tool_description("aggregate_window"))
async def aggregate_window(
    device_id: Annotated[
        uuid.UUID, Field(description=_arg_description("aggregate_window", "device_id"))
    ],
    window: Annotated[
        AggregateWindow,
        Field(description=_arg_description("aggregate_window", "window")),
    ],
    fn: Annotated[
        AggregateFn, Field(description=_arg_description("aggregate_window", "fn"))
    ],
    start: Annotated[
        datetime, Field(description=_arg_description("aggregate_window", "start"))
    ],
    end: Annotated[
        datetime, Field(description=_arg_description("aggregate_window", "end"))
    ],
) -> str:
    return await _run(
        "aggregate_window",
        {
            "device_id": str(device_id),
            "window": window.value,
            "fn": fn.value,
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
    )


@mcp.tool(name="get_recent_alerts", description=_tool_description("get_recent_alerts"))
async def get_recent_alerts(
    since: Annotated[
        datetime, Field(description=_arg_description("get_recent_alerts", "since"))
    ],
    device_id: Annotated[
        uuid.UUID | None,
        Field(description=_arg_description("get_recent_alerts", "device_id")),
    ] = None,
) -> str:
    return await _run(
        "get_recent_alerts",
        {
            "since": since.isoformat(),
            **({} if device_id is None else {"device_id": str(device_id)}),
        },
    )


async def _check() -> int:
    """Connect, list the fleet, and report — without starting the server.

    This exists because a misconfigured stdio server fails *silently* inside the
    host application: the tools simply do not appear, with no error surfaced
    anywhere the user can see. One command that either prints the fleet or prints
    why it cannot is the difference between a five-minute setup and an hour of
    guessing.
    """
    settings = load_settings()
    out = sys.stderr
    out.write(f"api        {settings.api_url}\n")
    out.write(
        "credential "
        + (
            "token (cannot be renewed on expiry)"
            if settings.token and not settings.password
            else "email + password"
            if settings.password
            else "MISSING"
        )
        + "\n"
    )
    try:
        runtime = await _get_runtime()
        payload = await _run("list_devices", {})
        out.write(f"user id    {runtime.context.user_id}\n")
        out.write(f"devices    {payload}\n")
        out.write("\nOK — the server can reach the API and read your fleet.\n")
        await runtime.client.aclose()
        return 0
    except (DataBullError, OSError) as error:
        out.write(f"\nFAILED — {error}\n")
        return 1


def main() -> None:
    """Entry point for the `databull-mcp` console script."""
    argv = sys.argv[1:]

    if "--help" in argv or "-h" in argv:
        sys.stderr.write(
            "databull-mcp [--check] [--transport stdio|streamable-http]\n\n"
            "  --check      verify configuration and exit\n"
            "  --transport  default stdio; streamable-http binds a local port\n\n"
            "Configuration comes from the environment: DATABULL_API_URL, and "
            "either DATABULL_TOKEN or DATABULL_EMAIL + DATABULL_PASSWORD.\n"
        )
        return

    if "--check" in argv:
        raise SystemExit(asyncio.run(_check()))

    transport = "stdio"
    if "--transport" in argv:
        index = argv.index("--transport")
        if index + 1 < len(argv):
            transport = argv[index + 1]
    # Dispatched as two separate calls rather than one with a narrowed variable:
    # `run` is overloaded per transport, each with its own keyword set, so a
    # `Literal[...]` union still matches no single overload.
    #
    # Nothing is written to stdout from here on: it carries the protocol.
    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport == "streamable-http":
        mcp.run(transport="streamable-http")
    else:
        sys.stderr.write(f"Unknown transport {transport!r}.\n")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
