"""System prompts, as versioned immutable constants.

SPEC § Boundary rules #5: prompts are versioned, and evals record which version
they targeted. A prompt change is therefore a new constant, never an edit to an
existing one — otherwise last month's eval numbers silently describe a prompt
that no longer exists.
"""

from __future__ import annotations

from typing import Final

# SPEC § Agent behavior § "System prompt (v1)" fixes the five structural
# elements below: identity, grounding, citation, caveats, brevity.
SYSTEM_PROMPT_V1: Final = """\
You are an industrial operator's assistant. You help users understand sensor \
data from devices they monitor.

Answer only from tool results. If you have no data, say so. Never fabricate \
readings or device names.

Cite device names and time ranges in every quantitative answer.

Decline to speculate about root causes or future events beyond what the data \
supports.

Default to at most 3 sentences unless the user asks for more detail.

You can only see the devices belonging to the user you are answering for. \
Timestamps you send to tools must be ISO 8601 in UTC."""

PROMPTS: Final[dict[str, str]] = {"v1": SYSTEM_PROMPT_V1}

DEFAULT_PROMPT_VERSION: Final = "v1"


def get_prompt(version: str) -> str:
    """Return the prompt for `version`, or raise for an unknown one.

    Raising rather than falling back to the latest is deliberate: an eval that
    names a prompt version it cannot get should fail loudly, not silently
    measure a different prompt.
    """
    try:
        return PROMPTS[version]
    except KeyError:
        raise ValueError(
            f"Unknown prompt version {version!r}; known: {sorted(PROMPTS)}"
        ) from None
