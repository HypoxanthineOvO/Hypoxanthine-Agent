from __future__ import annotations

# DEPRECATED: QQ Bot webhook 接入已被 WebSocket 长连接模式取代（MQ 迁移，2026-03-26）
# 保留此路由文件用于紧急回退，不再默认注册。

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/webhook/qq-bot")
async def qq_bot_webhook(request: Request) -> JSONResponse:
    service = getattr(request.app.state, "qq_bot_channel_service", None)
    if service is None:
        return JSONResponse(status_code=404, content={"detail": "qq bot channel not enabled"})

    body = await request.body()
    signature = str(request.headers.get("X-Signature-Ed25519") or "").strip()
    timestamp = str(request.headers.get("X-Signature-Timestamp") or "").strip()
    status_code, payload = await service.handle_webhook_request(
        body=body,
        signature=signature,
        timestamp=timestamp,
        pipeline=getattr(request.app.state, "pipeline", None),
    )
    return JSONResponse(status_code=status_code, content=payload)
