"""The chat adapter: HTTP in, SSE out. No agent logic lives here.

SPEC § Boundary rules #4 — this router receives the request, builds
`AgentServices`, calls `run_agent`, and turns the event stream into SSE frames.
Anything more than translation belongs in `/agent/`.

This is also the only file that bridges auth into the agent: it resolves the
caller with the standard `CurrentUser` dependency and passes a plain `user_id`
inward. The agent module never sees the JWT — the same shape an extracted
agent service would receive from an authenticated upstream call.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

from app.agent.llm_client import AnthropicLLMClient, LLMConfigurationError
from app.agent.runner import ChatMessage, run_agent
from app.agent.services import (
    AgentServices,
    SqlalchemyAlertRepository,
    SqlalchemyDeviceRepository,
    SqlalchemyReadingRepository,
)
from app.core.deps import CurrentUser, DbSession
from app.core.errors import APIError
from app.schemas.chat import ChatRequest

router = APIRouter(tags=["chat"])


def build_agent_services(
    session: DbSession, current_user: CurrentUser
) -> AgentServices:
    """Wire the live repositories for the authenticated caller.

    The repositories are scoped to `current_user` at construction, so the agent
    physically cannot reach another user's rows even if a tool were handed the
    wrong id — it would raise rather than answer.
    """
    try:
        llm = AnthropicLLMClient()
    except LLMConfigurationError as exc:
        # A deployment without an Anthropic key still serves devices and
        # readings; only this endpoint is unavailable, and it says so plainly
        # rather than surfacing as a generic 500.
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            code="assistant_unavailable",
        ) from exc

    return AgentServices(
        devices=SqlalchemyDeviceRepository(session, current_user),
        readings=SqlalchemyReadingRepository(session, current_user),
        alerts=SqlalchemyAlertRepository(session, current_user),
        llm=llm,
        now=lambda: datetime.now(UTC),
    )


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    services: Annotated[AgentServices, Depends(build_agent_services)],
    current_user: CurrentUser,
) -> StreamingResponse:
    """Stream one agent turn as Server-Sent Events."""

    async def sse() -> AsyncIterator[str]:
        async for event in run_agent(
            user_id=current_user.id,
            messages=[
                ChatMessage(role=m.role, content=m.content) for m in body.messages
            ],
            services=services,
        ):
            yield f"data: {event.model_dump_json()}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={
            # Without these an intermediary proxy buffers the whole response and
            # delivers it at once, which defeats streaming entirely.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
