"""Configuration for the MCP server process.

Read from the environment only, with no `.env` lookup — unlike `core/config.py`,
which serves a process started from a known working directory. An MCP server is
launched *by its client* (Claude Desktop, Claude Code, someone else's agent) from
whatever directory that client happens to be in, so a relative `.env` path is not
something to rely on. Clients pass configuration through an `env` block instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Generous compared to the chat endpoint's 2KB. That cap exists because those
# bytes are billed as Anthropic input tokens on every turn; an MCP client manages
# its own context window and would rather see a full result set. Still bounded, so
# a thousand-row reply cannot be unbounded in size.
DEFAULT_TOOL_RESULT_MAX_BYTES = 65_536


@dataclass(frozen=True)
class McpSettings:
    api_url: str
    token: str | None
    email: str | None
    password: str | None
    timeout_seconds: float
    tool_result_max_bytes: int

    @property
    def has_credentials(self) -> bool:
        return self.token is not None or (
            self.email is not None and self.password is not None
        )


def _optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def load_settings() -> McpSettings:
    return McpSettings(
        api_url=_optional("DATABULL_API_URL") or "http://localhost:8000",
        token=_optional("DATABULL_TOKEN"),
        email=_optional("DATABULL_EMAIL"),
        password=_optional("DATABULL_PASSWORD"),
        timeout_seconds=float(_optional("DATABULL_TIMEOUT_SECONDS") or "15"),
        tool_result_max_bytes=int(
            _optional("MCP_TOOL_RESULT_MAX_BYTES") or DEFAULT_TOOL_RESULT_MAX_BYTES
        ),
    )
