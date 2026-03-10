from __future__ import annotations

from fastapi import FastAPI

from hypo_agent.core.pipeline import ChatPipeline, build_default_pipeline
from hypo_agent.gateway.middleware import WsTokenAuthMiddleware
from hypo_agent.gateway.ws import router as ws_router


def create_app(auth_token: str, pipeline: ChatPipeline | None = None) -> FastAPI:
    app = FastAPI(title="Hypo-Agent Gateway")
    app.add_middleware(WsTokenAuthMiddleware, auth_token=auth_token)
    app.state.pipeline = pipeline or build_default_pipeline()
    app.include_router(ws_router)
    return app
