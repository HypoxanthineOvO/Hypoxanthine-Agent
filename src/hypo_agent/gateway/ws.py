from __future__ import annotations

import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
import structlog
from uuid import uuid4

from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.core.time_utils import utc_isoformat, utc_now
from hypo_agent.models import Message

router = APIRouter()
logger = structlog.get_logger("hypo_agent.gateway.ws")


class WsConnectionManager:
    def __init__(self) -> None:
        self._sockets: dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()
        self._last_message_at: str | None = None

    async def connect(self, ws: WebSocket) -> str:
        await ws.accept()
        client_id = uuid4().hex
        async with self._lock:
            self._sockets[client_id] = ws
        return client_id

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            stale_ids = [client_id for client_id, socket in self._sockets.items() if socket is ws]
            for client_id in stale_ids:
                self._sockets.pop(client_id, None)

    async def broadcast(
        self,
        payload: dict[str, object],
        *,
        exclude_client_ids: set[str] | None = None,
    ) -> None:
        excluded = {item for item in (exclude_client_ids or set()) if item}
        async with self._lock:
            sockets = list(self._sockets.items())
        stale: list[str] = []
        sent = False
        for client_id, ws in sockets:
            if client_id in excluded:
                continue
            try:
                await ws.send_json(payload)
                sent = True
            except Exception:
                stale.append(client_id)
        if sent:
            self.record_activity()
        if stale:
            async with self._lock:
                for client_id in stale:
                    self._sockets.pop(client_id, None)

    def record_activity(self) -> None:
        self._last_message_at = utc_isoformat(utc_now())

    def get_status(self) -> dict[str, object]:
        active_connections = len(self._sockets)
        return {
            "status": "connected" if active_connections > 0 else "disconnected",
            "active_connections": active_connections,
            "last_message_at": self._last_message_at,
        }

    def reset(self) -> None:
        self._sockets.clear()
        self._last_message_at = None


connection_manager = WsConnectionManager()


async def broadcast_message(
    payload: dict[str, object],
    *,
    exclude_client_ids: set[str] | None = None,
) -> None:
    await connection_manager.broadcast(payload, exclude_client_ids=exclude_client_ids)


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


def _normalize_event_timestamp(payload: dict[str, object]) -> dict[str, object]:
    event_type = str(payload.get("type") or "")
    if event_type not in {"assistant_chunk", "assistant_done", "narration"}:
        return payload
    if payload.get("timestamp"):
        return payload
    return {
        **payload,
        "timestamp": utc_isoformat(utc_now()),
    }


@router.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    client_id = await connection_manager.connect(ws)
    session_id = ""
    try:
        while True:
            payload = await ws.receive_json()
            if isinstance(payload, dict) and not str(payload.get("session_id") or "").strip():
                payload = {**payload, "session_id": "main"}
            inbound = Message.model_validate(payload)
            if not any((inbound.text, inbound.image, inbound.file, inbound.audio)):
                raise ValueError("message content is required")
            inbound = inbound.model_copy(
                update={
                    "timestamp": utc_now(),
                    "metadata": {
                        **dict(inbound.metadata),
                        "webui_client_id": client_id,
                    }
                }
            )
            session_id = inbound.session_id
            connection_manager.record_activity()
            await broadcast_message(
                inbound.model_dump(mode="json"),
                exclude_client_ids={client_id},
            )
            mirror = getattr(ws.app.state, "mirror_webui_message_to_qq", None)
            if callable(mirror):
                mirror_result = mirror(inbound)
                if asyncio.iscoroutine(mirror_result):
                    await mirror_result
            pipeline: ChatPipeline = ws.app.state.pipeline
            async for event in pipeline.stream_reply(inbound):
                await ws.send_json(_normalize_event_timestamp(dict(event)))
    except (ValidationError, ValueError):
        await ws.close(code=4400)
    except RuntimeError as exc:
        await _send_error_and_close(ws, session_id=session_id, exc=exc)
    except WebSocketDisconnect:
        return
    except Exception as exc:  # pragma: no cover - defensive fallback
        await _send_error_and_close(ws, session_id=session_id, exc=exc)
    finally:
        await connection_manager.disconnect(ws)
