from __future__ import annotations

from fastapi import APIRouter, Request

from hypo_agent.gateway.auth import require_api_token
from hypo_agent.models import Message

router = APIRouter(prefix="/api")


@router.get("/sessions")
async def list_sessions(request: Request) -> list[dict[str, object]]:
    session_memory = request.app.state.session_memory
    return session_memory.list_sessions()


@router.get("/sessions/{session_id}/messages")
async def list_session_messages(session_id: str, request: Request) -> list[dict]:
    session_memory = request.app.state.session_memory
    messages: list[Message] = session_memory.get_messages(session_id)
    return [message.model_dump(mode="json") for message in messages]


@router.get("/sessions/{session_id}/tool-invocations")
async def list_session_tool_invocations(
    session_id: str,
    request: Request,
) -> list[dict]:
    require_api_token(request)

    structured_store = request.app.state.structured_store
    rows = await structured_store.list_tool_invocations(session_id=session_id)
    return rows
