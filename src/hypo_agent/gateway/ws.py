from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from hypo_agent.models import Message

router = APIRouter()


@router.websocket("/ws")
async def websocket_echo(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            payload = await ws.receive_json()
            inbound = Message.model_validate(payload)
            outbound = Message(
                text=inbound.text,
                image=inbound.image,
                file=inbound.file,
                audio=inbound.audio,
                sender="assistant",
                session_id=inbound.session_id,
            )
            await ws.send_json(outbound.model_dump(mode="json"))
    except ValidationError:
        await ws.close(code=4400)
    except WebSocketDisconnect:
        return
