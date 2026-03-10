from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import yaml

from hypo_agent.core.config_loader import load_runtime_model_config
from hypo_agent.core.model_router import ModelRouter
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.gateway.kill_switch_api import router as kill_switch_api_router
from hypo_agent.gateway.sessions_api import router as sessions_api_router
from hypo_agent.gateway.middleware import WsTokenAuthMiddleware
from hypo_agent.gateway.ws import router as ws_router
from hypo_agent.memory import SessionMemory, StructuredStore
from hypo_agent.models import SecurityConfig
from hypo_agent.security import CircuitBreaker
from hypo_agent.skills import CodeRunSkill, TmuxSkill


@dataclass(slots=True)
class AppDeps:
    session_memory: SessionMemory
    structured_store: StructuredStore
    skill_manager: SkillManager | None = None
    circuit_breaker: CircuitBreaker | None = None


def _default_security() -> SecurityConfig:
    return SecurityConfig.model_validate(
        {
            "directory_whitelist": {"read": [], "write": [], "execute": []},
            "circuit_breaker": {},
        }
    )


def _build_default_deps(security: SecurityConfig | None = None) -> AppDeps:
    resolved_security = security or _default_security()
    circuit_breaker = CircuitBreaker(resolved_security.circuit_breaker)
    skill_manager = SkillManager(circuit_breaker=circuit_breaker)

    skills_config_path = Path("config/skills.yaml")
    skills_payload: dict[str, Any] = {}
    if skills_config_path.exists():
        loaded = yaml.safe_load(skills_config_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            skills_payload = loaded

    default_timeout = int(skills_payload.get("default_timeout_seconds", 30))
    enabled_skills = (
        SkillManager.find_enabled_skills(skills_config_path)
        if skills_config_path.exists()
        else set()
    )
    per_skill = skills_payload.get("skills", {})
    tmux_cfg = per_skill.get("tmux", {}) if isinstance(per_skill, dict) else {}
    tmux_timeout = int(tmux_cfg.get("timeout_seconds", default_timeout))

    tmux_skill: TmuxSkill | None = None
    if "tmux" in enabled_skills:
        tmux_skill = TmuxSkill(default_timeout_seconds=tmux_timeout)
        skill_manager.register(tmux_skill)

    if "code_run" in enabled_skills:
        if tmux_skill is None:
            tmux_skill = TmuxSkill(default_timeout_seconds=tmux_timeout)
        skill_manager.register(CodeRunSkill(tmux_skill=tmux_skill))

    return AppDeps(
        session_memory=SessionMemory(sessions_dir="memory/sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path="memory/hypo.db"),
        skill_manager=skill_manager,
        circuit_breaker=circuit_breaker,
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
        skill_manager=deps.skill_manager,
        max_react_rounds=5,
    )


def create_app(
    auth_token: str,
    pipeline: ChatPipeline | None = None,
    deps: AppDeps | None = None,
    security: SecurityConfig | None = None,
) -> FastAPI:
    resolved_deps = deps or _build_default_deps(security)
    if resolved_deps.circuit_breaker is None:
        resolved_deps.circuit_breaker = CircuitBreaker(
            (security or _default_security()).circuit_breaker
        )
    if resolved_deps.skill_manager is None:
        resolved_deps.skill_manager = SkillManager(
            circuit_breaker=resolved_deps.circuit_breaker
        )

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
    app.state.deps = resolved_deps
    app.state.session_memory = resolved_deps.session_memory
    app.state.structured_store = resolved_deps.structured_store
    app.state.skill_manager = resolved_deps.skill_manager
    app.state.circuit_breaker = resolved_deps.circuit_breaker
    app.state.pipeline = pipeline or _build_default_pipeline(resolved_deps)

    app.include_router(ws_router)
    app.include_router(sessions_api_router)
    app.include_router(kill_switch_api_router)
    return app
