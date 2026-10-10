"""The stdio transport, over a real MCP handshake.

Everything in `test_mcp_server.py` calls the server in-process, which skips the
part a connecting client actually depends on: spawning the process, negotiating
`initialize`, and reading the tool list back over JSON-RPC. This test is the only
one that covers it, and it is where a regression in the transport or the entry
point would show up first.

No API and no credentials are needed — `list_tools` is answered from the
registered tools alone, which is also why a misconfigured server still appears to
"work" in a client until the first tool call. Hence `databull-mcp --check`.

The server is launched as `python -m app.mcp.server` rather than through the
`databull-mcp` console script, so the test does not depend on the package having
been installed with the `mcp` extra registered on PATH.
"""

from __future__ import annotations

import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from app.agent.tools import TOOLS


async def test_a_client_can_connect_over_stdio_and_list_the_tools() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        env={
            **os.environ,
            # Deliberately no credentials: a handshake must not require them.
            "DATABULL_API_URL": "http://localhost:8000",
        },
    )

    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        assert init.server_info.name == "databull"
        # The instructions reach the client's model, so they must survive.
        assert init.instructions is not None
        assert "Read-only" in init.instructions

        listed = await session.list_tools()
        assert {tool.name for tool in listed.tools} == set(TOOLS)

        by_name = {tool.name: tool for tool in listed.tools}
        assert by_name["list_devices"].input_schema.get("required", []) == []
        assert set(by_name["aggregate_window"].input_schema["required"]) == {
            "device_id",
            "window",
            "fn",
            "start",
            "end",
        }
