from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.models import Message

router = APIRouter()


@router.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            payload = await ws.receive_json()
            inbound = Message.model_validate(payload)
            pipeline: ChatPipeline = ws.app.state.pipeline
            async for event in pipeline.stream_reply(inbound):
                await ws.send_json(event)
    except (ValidationError, ValueError):
        await ws.close(code=4400)
    except WebSocketDisconnect:
        return
