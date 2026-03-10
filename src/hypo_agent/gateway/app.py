from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import inspect
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import structlog
import yaml

from hypo_agent.core.config_loader import get_memory_dir
from hypo_agent.channels.qq_channel import QQChannelService
from hypo_agent.core.config_loader import load_secrets_config, load_tasks_config
from hypo_agent.core.channel_adapter import WebUIAdapter
from hypo_agent.core.channel_dispatcher import ChannelDispatcher
from hypo_agent.core.config_loader import load_runtime_model_config
from hypo_agent.core.event_queue import EventQueue
from hypo_agent.core.heartbeat import HeartbeatService
from hypo_agent.core.model_router import ModelRouter
from hypo_agent.core.output_compressor import OutputCompressor
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.core.scheduler import SchedulerService
from hypo_agent.core.slash_commands import SlashCommandHandler
from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.gateway.config_api import router as config_api_router
from hypo_agent.gateway.compressed_api import router as compressed_api_router
from hypo_agent.gateway.dashboard_api import router as dashboard_api_router
from hypo_agent.gateway.files_api import router as files_api_router
from hypo_agent.gateway.kill_switch_api import router as kill_switch_api_router
from hypo_agent.gateway.memory_api import router as memory_api_router
from hypo_agent.gateway.sessions_api import router as sessions_api_router
from hypo_agent.gateway.middleware import WsTokenAuthMiddleware
from hypo_agent.gateway.qq_ws import router as qq_ws_router
from hypo_agent.gateway.settings import load_gateway_settings
from hypo_agent.gateway.ws import broadcast_message, router as ws_router
from hypo_agent.memory import SessionMemory, StructuredStore
from hypo_agent.models import Message, SecurityConfig
from hypo_agent.security import CircuitBreaker, PermissionManager
from hypo_agent.skills import (
    CodeRunSkill,
    EmailScannerSkill,
    FileSystemSkill,
    MemorySkill,
    ReminderSkill,
    TmuxSkill,
)

logger = structlog.get_logger("hypo_agent.gateway.app")


@dataclass(slots=True)
class AppDeps:
    session_memory: SessionMemory
    structured_store: StructuredStore
    event_queue: EventQueue | None = None
    scheduler: SchedulerService | None = None
    output_compressor: OutputCompressor | None = None
    skill_manager: SkillManager | None = None
    circuit_breaker: CircuitBreaker | None = None
    permission_manager: PermissionManager | None = None
    heartbeat_service: HeartbeatService | Any | None = None
    reload_config: Any | None = None


def _register_enabled_skills(
    *,
    skill_manager: SkillManager,
    permission_manager: PermissionManager,
    structured_store: StructuredStore | None = None,
    scheduler: SchedulerService | None = None,
    heartbeat_service: HeartbeatService | Any | None = None,
    message_queue: EventQueue | Any | None = None,
    model_router: ModelRouter | None = None,
    skills_config_path: Path = Path("config/skills.yaml"),
) -> None:
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
    code_run_cfg = per_skill.get("code_run", {}) if isinstance(per_skill, dict) else {}
    reminder_cfg = per_skill.get("reminder", {}) if isinstance(per_skill, dict) else {}
    tmux_timeout = int(tmux_cfg.get("timeout_seconds", default_timeout))
    code_run_timeout = int(code_run_cfg.get("timeout_seconds", default_timeout))
    auto_confirm = bool(reminder_cfg.get("auto_confirm", True))

    if "tmux" in enabled_skills:
        skill_manager.register(
            TmuxSkill(default_timeout_seconds=tmux_timeout, permission_manager=permission_manager)
        )

    if "code_run" in enabled_skills:
        skill_manager.register(
            CodeRunSkill(
                permission_manager=permission_manager,
                default_timeout_seconds=code_run_timeout,
            )
        )

    if "filesystem" in enabled_skills:
        skill_manager.register(FileSystemSkill(permission_manager=permission_manager))

    if "reminder" in enabled_skills and structured_store is not None and scheduler is not None:
        skill_manager.register(
            ReminderSkill(
                structured_store=structured_store,
                scheduler=scheduler,
                model_router=model_router,
                auto_confirm=auto_confirm,
            )
        )

    if "email_scanner" in enabled_skills and structured_store is not None:
        email_skill = EmailScannerSkill(
            structured_store=structured_store,
            model_router=model_router,
            message_queue=message_queue,
        )
        skill_manager.register(email_skill)
        if (
            heartbeat_service is not None
            and hasattr(heartbeat_service, "register_event_source")
            and hasattr(email_skill, "_check_new_emails")
        ):
            heartbeat_service.register_event_source("email", email_skill._check_new_emails)

    # Memory tools are safe and expected to be available for preference persistence.
    if structured_store is not None:
        skill_manager.register(MemorySkill(structured_store=structured_store))


def _default_security() -> SecurityConfig:
    return SecurityConfig.model_validate(
        {
            "directory_whitelist": {"rules": [], "default_policy": "readonly"},
            "circuit_breaker": {},
        }
    )


def _build_default_deps(security: SecurityConfig | None = None) -> AppDeps:
    resolved_security = security or _default_security()
    structured_store = StructuredStore()
    event_queue = EventQueue()
    scheduler = SchedulerService(
        structured_store=structured_store,
        event_queue=event_queue,
    )
    heartbeat_service = HeartbeatService(
        structured_store=structured_store,
        model_router=None,
        message_queue=event_queue,
        scheduler=scheduler,
    )
    permission_manager = PermissionManager(resolved_security.directory_whitelist)
    circuit_breaker = CircuitBreaker(resolved_security.circuit_breaker)
    skill_manager = SkillManager(
        circuit_breaker=circuit_breaker,
        permission_manager=permission_manager,
        structured_store=structured_store,
    )
    _register_enabled_skills(
        skill_manager=skill_manager,
        permission_manager=permission_manager,
        structured_store=structured_store,
        scheduler=scheduler,
        heartbeat_service=heartbeat_service,
        message_queue=event_queue,
    )

    return AppDeps(
        session_memory=SessionMemory(buffer_limit=20),
        structured_store=structured_store,
        event_queue=event_queue,
        scheduler=scheduler,
        heartbeat_service=heartbeat_service,
        skill_manager=skill_manager,
        circuit_breaker=circuit_breaker,
        permission_manager=permission_manager,
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
            latency_ms=event.get("latency_ms"),
        )

    runtime_config = load_runtime_model_config()
    router = ModelRouter(runtime_config, on_stream_success=on_stream_success)
    if deps.scheduler is not None:
        deps.scheduler.model_router = router
    if deps.heartbeat_service is not None and hasattr(deps.heartbeat_service, "model_router"):
        deps.heartbeat_service.model_router = router
    reminder_skill = (
        deps.skill_manager._skills.get("reminder")  # type: ignore[attr-defined]
        if deps.skill_manager is not None and hasattr(deps.skill_manager, "_skills")
        else None
    )
    if reminder_skill is not None and hasattr(reminder_skill, "model_router"):
        reminder_skill.model_router = router
    email_skill = (
        deps.skill_manager._skills.get("email_scanner")  # type: ignore[attr-defined]
        if deps.skill_manager is not None and hasattr(deps.skill_manager, "_skills")
        else None
    )
    if email_skill is not None and hasattr(email_skill, "model_router"):
        email_skill.model_router = router
    if deps.output_compressor is None:
        deps.output_compressor = OutputCompressor(router=router)
    chat_model = router.get_model_for_task("chat")
    slash_commands = SlashCommandHandler(
        router=router,
        session_memory=deps.session_memory,
        structured_store=deps.structured_store,
        circuit_breaker=deps.circuit_breaker,
        skill_manager=deps.skill_manager,
    )
    return ChatPipeline(
        router=router,
        chat_model=chat_model,
        session_memory=deps.session_memory,
        history_window=20,
        skill_manager=deps.skill_manager,
        structured_store=deps.structured_store,
        max_react_rounds=5,
        slash_commands=slash_commands,
        output_compressor=deps.output_compressor,
        channel_adapter=WebUIAdapter(),
        event_queue=deps.event_queue,
    )


def _ensure_pipeline_lifecycle_hooks(pipeline_obj: Any) -> None:
    for method_name in ("start_event_consumer", "stop_event_consumer"):
        method = getattr(pipeline_obj, method_name, None)
        if method is None:
            async def _noop() -> None:
                return None

            setattr(pipeline_obj, method_name, _noop)
            continue
        if inspect.iscoroutinefunction(method):
            continue
        if callable(method):
            bound_method = method

            async def _wrapped(_bound_method=bound_method) -> None:
                result = _bound_method()
                if inspect.isawaitable(result):
                    await result

            setattr(pipeline_obj, method_name, _wrapped)


def _build_qq_channel_service(config_dir: Path) -> QQChannelService | None:
    secrets_path = config_dir / "secrets.yaml"
    try:
        secrets = load_secrets_config(secrets_path)
    except FileNotFoundError:
        return None
    except Exception:
        logger.exception("qq.config.load_failed", path=str(secrets_path))
        return None

    services = secrets.services
    qq_cfg = services.qq if services is not None else None
    if qq_cfg is None:
        return None

    allowed_users = {item.strip() for item in qq_cfg.allowed_users if item and item.strip()}
    return QQChannelService(
        napcat_http_url=qq_cfg.napcat_http_url,
        napcat_http_token=qq_cfg.napcat_http_token,
        bot_qq=qq_cfg.bot_qq,
        allowed_users=allowed_users,
    )


def create_app(
    auth_token: str,
    pipeline: ChatPipeline | None = None,
    deps: AppDeps | None = None,
    security: SecurityConfig | None = None,
) -> FastAPI:
    resolved_deps = deps or _build_default_deps(security)
    if resolved_deps.event_queue is None:
        resolved_deps.event_queue = EventQueue()
    if resolved_deps.scheduler is None:
        resolved_deps.scheduler = SchedulerService(
            structured_store=resolved_deps.structured_store,
            event_queue=resolved_deps.event_queue,
        )
    if resolved_deps.heartbeat_service is None:
        resolved_deps.heartbeat_service = HeartbeatService(
            structured_store=resolved_deps.structured_store,
            model_router=None,
            message_queue=resolved_deps.event_queue,
            scheduler=resolved_deps.scheduler,
        )
    if resolved_deps.circuit_breaker is None:
        resolved_deps.circuit_breaker = CircuitBreaker(
            (security or _default_security()).circuit_breaker
        )
    if resolved_deps.permission_manager is None:
        resolved_deps.permission_manager = PermissionManager(
            (security or _default_security()).directory_whitelist
        )
    if resolved_deps.skill_manager is None:
        resolved_deps.skill_manager = SkillManager(
            circuit_breaker=resolved_deps.circuit_breaker,
            permission_manager=resolved_deps.permission_manager,
            structured_store=resolved_deps.structured_store,
        )

    pipeline_instance = pipeline or _build_default_pipeline(resolved_deps)
    _ensure_pipeline_lifecycle_hooks(pipeline_instance)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await resolved_deps.structured_store.init()
        if resolved_deps.scheduler is not None:
            await resolved_deps.scheduler.start()
            tasks_path = Path(getattr(app.state, "config_dir", Path("config"))) / "tasks.yaml"
            if tasks_path.exists():
                try:
                    tasks_cfg = load_tasks_config(tasks_path)
                except Exception:
                    tasks_cfg = None
                if tasks_cfg is not None:
                    if (
                        tasks_cfg.heartbeat.enabled
                        and resolved_deps.heartbeat_service is not None
                        and hasattr(resolved_deps.scheduler, "register_interval_job")
                    ):
                        resolved_deps.scheduler.register_interval_job(
                            "heartbeat",
                            tasks_cfg.heartbeat.interval_minutes,
                            resolved_deps.heartbeat_service.run,
                        )
                    if (
                        tasks_cfg.email_scan.enabled
                        and resolved_deps.skill_manager is not None
                        and hasattr(resolved_deps.skill_manager, "_skills")
                        and hasattr(resolved_deps.scheduler, "register_interval_job")
                    ):
                        email_skill = resolved_deps.skill_manager._skills.get("email_scanner")
                        if email_skill is not None and hasattr(email_skill, "scheduled_scan"):
                            resolved_deps.scheduler.register_interval_job(
                                "email_scan",
                                tasks_cfg.email_scan.interval_minutes,
                                email_skill.scheduled_scan,
                            )
        await app.state.pipeline.start_event_consumer()
        try:
            yield
        finally:
            await app.state.pipeline.stop_event_consumer()
            if resolved_deps.scheduler is not None:
                await resolved_deps.scheduler.stop()

    app = FastAPI(title="Hypo-Agent Gateway", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(WsTokenAuthMiddleware, auth_token=auth_token)

    app.state.auth_token = auth_token
    app.state.started_at = datetime.now(UTC)
    app.state.config_dir = Path("config")
    app.state.knowledge_dir = get_memory_dir() / "knowledge"
    app.state.deps = resolved_deps
    app.state.session_memory = resolved_deps.session_memory
    app.state.structured_store = resolved_deps.structured_store
    app.state.skill_manager = resolved_deps.skill_manager
    app.state.circuit_breaker = resolved_deps.circuit_breaker
    app.state.permission_manager = resolved_deps.permission_manager
    app.state.output_compressor = resolved_deps.output_compressor
    app.state.event_queue = resolved_deps.event_queue
    app.state.scheduler = resolved_deps.scheduler
    app.state.heartbeat_service = resolved_deps.heartbeat_service
    app.state.pipeline = pipeline_instance
    app.state.channel_dispatcher = ChannelDispatcher()
    app.state.qq_channel_service = None

    async def push_ws_message(payload: dict[str, object]) -> None:
        await broadcast_message(payload)

    async def push_webui_message(message: Message) -> None:
        await push_ws_message(message.model_dump(mode="json"))

    app.state.channel_dispatcher.register("webui", push_webui_message)

    def refresh_qq_channel_service() -> None:
        app.state.channel_dispatcher.unregister("qq")
        config_dir = Path(getattr(app.state, "config_dir", Path("config")))
        service = _build_qq_channel_service(config_dir)
        app.state.qq_channel_service = service
        if service is not None:
            app.state.channel_dispatcher.register("qq", service.send_message)

    async def on_proactive_message(
        message: Message,
        *,
        exclude_channels: set[str] | None = None,
    ) -> None:
        await app.state.channel_dispatcher.broadcast(message, exclude_channels=exclude_channels)

    app.state.push_ws_message = push_ws_message
    setattr(app.state.pipeline, "on_proactive_message", on_proactive_message)

    async def reload_config() -> None:
        settings = load_gateway_settings()
        deps = app.state.deps
        previous_pipeline = app.state.pipeline
        deps.permission_manager = PermissionManager(settings.security.directory_whitelist)
        deps.circuit_breaker = CircuitBreaker(settings.security.circuit_breaker)
        deps.skill_manager = SkillManager(
            circuit_breaker=deps.circuit_breaker,
            permission_manager=deps.permission_manager,
            structured_store=deps.structured_store,
        )
        _register_enabled_skills(
            skill_manager=deps.skill_manager,
            permission_manager=deps.permission_manager,
            structured_store=deps.structured_store,
            scheduler=deps.scheduler,
            heartbeat_service=deps.heartbeat_service,
            message_queue=deps.event_queue,
        )

        deps.output_compressor = None
        await previous_pipeline.stop_event_consumer()
        app.state.auth_token = settings.auth_token
        app.state.permission_manager = deps.permission_manager
        app.state.circuit_breaker = deps.circuit_breaker
        app.state.skill_manager = deps.skill_manager
        app.state.pipeline = _build_default_pipeline(deps)
        _ensure_pipeline_lifecycle_hooks(app.state.pipeline)
        setattr(app.state.pipeline, "on_proactive_message", on_proactive_message)
        refresh_qq_channel_service()
        app.state.output_compressor = deps.output_compressor
        await app.state.pipeline.start_event_consumer()

    resolved_deps.reload_config = reload_config
    app.state.reload_config = reload_config
    refresh_qq_channel_service()

    app.include_router(ws_router)
    app.include_router(qq_ws_router)
    app.include_router(sessions_api_router)
    app.include_router(compressed_api_router)
    app.include_router(files_api_router)
    app.include_router(kill_switch_api_router)
    app.include_router(dashboard_api_router)
    app.include_router(config_api_router)
    app.include_router(memory_api_router)
    return app
