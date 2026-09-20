"""Request model for POST /chat/stream."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessageIn(BaseModel):
    """One prior turn of the conversation."""

    # The transcript is client-held: the API keeps no chat state, so the client
    # sends the whole history each time. Only user/assistant turns are accepted
    # — a client-supplied "system" turn would be a prompt-injection channel
    # straight into the operator instructions.
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=10_000)


class ChatRequest(BaseModel):
    """`{messages: [{role, content}, ...]}` per SPEC § Chat."""

    messages: list[ChatMessageIn] = Field(min_length=1, max_length=50)
