"""JSON schemas published to Claude, one per tool in `tools.TOOLS`.

Hand-written rather than generated from the Pydantic models. The models exist
to *validate* what comes back; these exist to *teach* the model when to reach
for each tool, and the descriptions are prompt text — the phrasing is what
stops it from calling `query_readings` when `aggregate_window` is the right
answer. A generator would emit accurate types and useless prose.

Order is fixed: the tool list is part of the cached prompt prefix, so a stable
order keeps cache hits.
"""

from __future__ import annotations

from typing import Any, Final

# `strict: true` guarantees the arguments validate against the schema before
# they ever reach us, which turns a whole class of malformed-input retry into
# something the API handles.
_ISO = "ISO 8601 timestamp in UTC, e.g. 2026-03-01T12:00:00Z"

TOOL_SCHEMAS: Final[list[dict[str, Any]]] = [
    {
        "name": "list_devices",
        "description": (
            "List every sensor device the current user owns, with its unit and "
            "configured alert thresholds. Call this first when the user names a "
            "device — you need the device_id for the other tools, and the names "
            "here are the only real ones."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "query_readings",
        "description": (
            "Fetch raw individual readings for one device in a time window, "
            "newest first. Use for specific values at specific times. For "
            "averages, minimums, maximums or trends over a period, use "
            "aggregate_window instead — it is far cheaper than reading every row."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Device UUID from list_devices.",
                },
                "start": {"type": "string", "description": f"Window start. {_ISO}"},
                "end": {"type": "string", "description": f"Window end. {_ISO}"},
                "limit": {
                    "type": "integer",
                    "description": "Maximum rows to return (1-1000).",
                },
            },
            "required": ["device_id", "start", "end"],
            "additionalProperties": False,
        },
    },
    {
        "name": "aggregate_window",
        "description": (
            "Roll readings up into fixed time buckets for one device. This is "
            "the right tool for 'average', 'minimum', 'maximum', 'p95', or any "
            "question about a trend over time."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {
                    "type": "string",
                    "description": "Device UUID from list_devices.",
                },
                "window": {
                    "type": "string",
                    "enum": ["1h", "1d", "1w"],
                    "description": "Bucket width.",
                },
                "fn": {
                    "type": "string",
                    "enum": ["avg", "min", "max", "p95"],
                    "description": "Rollup function.",
                },
                "start": {"type": "string", "description": f"Window start. {_ISO}"},
                "end": {"type": "string", "description": f"Window end. {_ISO}"},
            },
            "required": ["device_id", "window", "fn", "start", "end"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_recent_alerts",
        "description": (
            "List readings that breached a device's configured thresholds since "
            "a given time. Omit device_id to check every device the user owns — "
            "that is the right call for 'any issues with my devices?'. Devices "
            "with no thresholds configured can never produce an alert."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "since": {
                    "type": "string",
                    "description": f"Only alerts at or after this time. {_ISO}",
                },
                "device_id": {
                    "type": "string",
                    "description": "Optional device UUID to restrict to one device.",
                },
            },
            "required": ["since"],
            "additionalProperties": False,
        },
    },
]
