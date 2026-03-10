from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import structlog

logger = structlog.get_logger("hypo_agent.gateway.qq_ws")
router = APIRouter()


@router.websocket("/ws/qq/onebot")
async def qq_onebot_ingress(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            payload = await ws.receive_json()
            service = getattr(ws.app.state, "qq_channel_service", None)
            if service is None:
                continue
            pipeline = ws.app.state.pipeline
            await service.handle_onebot_event(payload, pipeline=pipeline)
    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("qq_ws.ingress.failed")
        await ws.close(code=1011)
