from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from hypo_agent.core.config_loader import load_runtime_model_config
from hypo_agent.core.model_router import ModelRouter
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.gateway.sessions_api import router as sessions_api_router
from hypo_agent.gateway.middleware import WsTokenAuthMiddleware
from hypo_agent.gateway.ws import router as ws_router
from hypo_agent.memory import SessionMemory, StructuredStore


@dataclass(slots=True)
class AppDeps:
    session_memory: SessionMemory
    structured_store: StructuredStore


def _build_default_deps() -> AppDeps:
    return AppDeps(
        session_memory=SessionMemory(sessions_dir="memory/sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path="memory/hypo.db"),
    )


def _build_default_pipeline(deps: AppDeps) -> ChatPipeline:
    async def on_stream_success(event: dict[str, Any]) -> None:
        session_id = event.get("session_id")
        requested_model = event.get("requested_model")
        resolved_model = event.get("resolved_model")
        if not session_id or not requested_model or not resolved_model:
            return

        await deps.structured_store.record_token_usage(
            session_id=session_id,
            requested_model=str(requested_model),
            resolved_model=str(resolved_model),
            input_tokens=event.get("input_tokens"),
            output_tokens=event.get("output_tokens"),
            total_tokens=event.get("total_tokens"),
        )

    runtime_config = load_runtime_model_config()
    router = ModelRouter(runtime_config, on_stream_success=on_stream_success)
    chat_model = runtime_config.task_routing.get("chat", runtime_config.default_model)
    return ChatPipeline(
        router=router,
        chat_model=chat_model,
        session_memory=deps.session_memory,
        history_window=20,
    )


def create_app(
    auth_token: str,
    pipeline: ChatPipeline | None = None,
    deps: AppDeps | None = None,
) -> FastAPI:
    resolved_deps = deps or _build_default_deps()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await resolved_deps.structured_store.init()
        yield

    app = FastAPI(title="Hypo-Agent Gateway", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(WsTokenAuthMiddleware, auth_token=auth_token)
    app.state.session_memory = resolved_deps.session_memory
    app.state.structured_store = resolved_deps.structured_store
    app.state.pipeline = pipeline or _build_default_pipeline(resolved_deps)

    app.include_router(ws_router)
    app.include_router(sessions_api_router)
    return app
