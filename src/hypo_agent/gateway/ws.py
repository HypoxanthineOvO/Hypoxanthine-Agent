from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
import structlog

from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.models import Message

router = APIRouter()
logger = structlog.get_logger("hypo_agent.gateway.ws")


def _build_error_event(session_id: str, exc: Exception) -> dict[str, object]:
    if isinstance(exc, TimeoutError):
        return {
            "type": "error",
            "code": "LLM_TIMEOUT",
            "message": "LLM 调用超时，请稍后重试",
            "retryable": True,
            "session_id": session_id,
        }

    if isinstance(exc, RuntimeError):
        return {
            "type": "error",
            "code": "LLM_RUNTIME_ERROR",
            "message": "LLM 调用失败，请检查配置或稍后重试",
            "retryable": True,
            "session_id": session_id,
        }

    return {
        "type": "error",
        "code": "INTERNAL_ERROR",
        "message": "服务内部错误，请稍后重试",
        "retryable": False,
        "session_id": session_id,
    }


async def _send_error_and_close(ws: WebSocket, session_id: str, exc: Exception) -> None:
    event = _build_error_event(session_id, exc)
    logger.exception(
        "ws.error.failed",
        session_id=session_id,
        code=event.get("code"),
        retryable=event.get("retryable"),
        error=str(exc),
    )
    await ws.send_json(event)
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
