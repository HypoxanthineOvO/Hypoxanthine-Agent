from __future__ import annotations

from fastapi import APIRouter, Request

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
