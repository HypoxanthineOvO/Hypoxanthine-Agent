from __future__ import annotations

import asyncio
import inspect
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
import structlog
from uuid import uuid4

try:
    from litellm.exceptions import OpenAIError as LiteLLMOpenAIError
except ImportError:  # pragma: no cover - depends on runtime environment
    LiteLLMOpenAIError = None

try:
    from openai import OpenAIError as OpenAIClientError
except ImportError:  # pragma: no cover - optional runtime dependency
    OpenAIClientError = None

from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.core.model_router import ModelFallbackError
from hypo_agent.core.time_utils import utc_isoformat, utc_now
from hypo_agent.models import Message

router = APIRouter()
logger = structlog.get_logger("hypo_agent.gateway.ws")
_LLM_RUNTIME_ERRORS = tuple(
    error_type
    for error_type in (LiteLLMOpenAIError, OpenAIClientError)
    if isinstance(error_type, type)
)


def _error_fields(exc: Exception) -> dict[str, str]:
    message = str(exc).strip()
    if len(message) > 200:
        message = f"{message[:197]}..."
    return {
        "error_type": type(exc).__name__,
        "error_msg": message,
    }


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
            except Exception as exc:
                logger.warning(
                    "ws.broadcast.client_removed",
                    client_id=client_id,
                    **_error_fields(exc),
                )
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

    if isinstance(exc, ModelFallbackError):
        return {
            "type": "error",
            "code": "LLM_FALLBACK_EXHAUSTED",
            "message": exc.user_message(),
            "retryable": exc.retryable,
            "session_id": session_id,
            "requested_model": exc.requested_model,
            "task_type": exc.task_type,
            "attempted_chain": exc.attempted_chain,
        }

    if isinstance(exc, (RuntimeError, *_LLM_RUNTIME_ERRORS)):
        return {
            "type": "error",
            "code": "LLM_RUNTIME_ERROR",
            "message": f"模型调用失败：{str(exc).strip() or '未返回可用回复，请稍后重试。'}",
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
    if not event_type:
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
            has_text = bool(str(inbound.text or "").strip())
            has_legacy_media = any((inbound.image, inbound.file, inbound.audio))
            has_attachments = bool(inbound.attachments)
            if not any((has_text, has_legacy_media, has_attachments)):
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
            proactive_callback = getattr(ws.app.state.pipeline, "on_proactive_message", None)
            if callable(proactive_callback):
                callback_result = proactive_callback(
                    inbound,
                    message_type="user_message",
                    origin_channel="webui",
                    origin_client_id=client_id,
                )
                if asyncio.iscoroutine(callback_result):
                    await callback_result
            pipeline: ChatPipeline = ws.app.state.pipeline

            async def emit_progress(payload: dict[str, object]) -> None:
                await ws.send_json(_normalize_event_timestamp(dict(payload)))

            stream_reply = pipeline.stream_reply
            stream_reply_kwargs: dict[str, object] = {}
            try:
                signature = inspect.signature(stream_reply)
            except (TypeError, ValueError):
                signature = None
            if signature is not None and "event_emitter" in signature.parameters:
                stream_reply_kwargs["event_emitter"] = emit_progress

            async for event in stream_reply(inbound, **stream_reply_kwargs):
                await ws.send_json(_normalize_event_timestamp(dict(event)))
    except (ValidationError, ValueError):
        await ws.close(code=4400)
    except RuntimeError as exc:
        await _send_error_and_close(ws, session_id=session_id, exc=exc)
    except WebSocketDisconnect:
        return
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.exception(
            "ws.error.failed",
            session_id=session_id,
            **_error_fields(exc),
        )
        await _send_error_and_close(ws, session_id=session_id, exc=exc)
    finally:
        await connection_manager.disconnect(ws)
