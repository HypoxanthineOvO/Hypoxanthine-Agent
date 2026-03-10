from __future__ import annotations

from fastapi import FastAPI

from hypo_agent.gateway.middleware import WsTokenAuthMiddleware
from hypo_agent.gateway.ws import router as ws_router


def create_app(auth_token: str) -> FastAPI:
    app = FastAPI(title="Hypo-Agent Gateway")
    app.add_middleware(WsTokenAuthMiddleware, auth_token=auth_token)
    app.include_router(ws_router)
    return app
