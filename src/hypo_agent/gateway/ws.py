from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
import structlog

from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.models import Message

router = APIRouter()
logger = structlog.get_logger("hypo_agent.gateway.ws")


async def _send_error_and_close(ws: WebSocket, session_id: str, exc: Exception) -> None:
    logger.exception(
        "ws_pipeline_failed",
        session_id=session_id,
        error=str(exc),
    )
    await ws.send_json(
        {
            "type": "error",
            "message": "LLM 调用失败，请检查配置或稍后重试",
            "session_id": session_id,
        }
    )
    await ws.close(code=1011)


@router.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    await ws.accept()
    session_id = ""
    try:
        while True:
            payload = await ws.receive_json()
            inbound = Message.model_validate(payload)
            session_id = inbound.session_id
            pipeline: ChatPipeline = ws.app.state.pipeline
            async for event in pipeline.stream_reply(inbound):
                await ws.send_json(event)
    except (ValidationError, ValueError):
        await ws.close(code=4400)
    except RuntimeError as exc:
        await _send_error_and_close(ws, session_id=session_id, exc=exc)
    except WebSocketDisconnect:
        return
    except Exception as exc:  # pragma: no cover - defensive fallback
        await _send_error_and_close(ws, session_id=session_id, exc=exc)
