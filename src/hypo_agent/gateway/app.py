from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
import inspect
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import structlog
import yaml

from hypo_agent.core.config_loader import (
    get_agent_root,
    get_database_path,
    get_memory_dir,
    get_test_sandbox_dir,
    is_test_mode,
)
from hypo_agent.channels.qq_channel import QQChannelService
from hypo_agent.channels.weixin import WeixinAdapter, WeixinChannel
from hypo_agent.core.config_loader import (
    load_persona_config,
    load_secrets_config,
    load_tasks_config,
    load_runtime_model_config,
    render_persona_system_prompt,
)
from hypo_agent.core.channel_adapter import WebUIAdapter
from hypo_agent.core.channel_dispatcher import ChannelDispatcher
from hypo_agent.core.directory_index import refresh_directory_index
from hypo_agent.core.event_queue import EventQueue
from hypo_agent.core.heartbeat import HeartbeatService
from hypo_agent.core.image_renderer import ImageRenderer
from hypo_agent.core.model_router import ModelRouter
from hypo_agent.core.narration_observer import NarrationObserver
from hypo_agent.core.output_compressor import OutputCompressor
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.core.persona import PersonaManager
from hypo_agent.core.scheduler import SchedulerService
from hypo_agent.core.slash_commands import SlashCommandHandler
from hypo_agent.core.sop_manager import SopManager
from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.core.time_utils import utc_isoformat, utc_now
from hypo_agent.gateway.config_api import router as config_api_router
from hypo_agent.gateway.compressed_api import router as compressed_api_router
from hypo_agent.gateway.dashboard_api import router as dashboard_api_router
from hypo_agent.gateway.files_api import router as files_api_router
from hypo_agent.gateway.kill_switch_api import router as kill_switch_api_router
from hypo_agent.gateway.memory_api import router as memory_api_router
from hypo_agent.gateway.sessions_api import router as sessions_api_router
from hypo_agent.gateway.upload_api import router as upload_api_router
from hypo_agent.gateway.middleware import WsTokenAuthMiddleware
from hypo_agent.gateway.qq_ws import router as qq_ws_router
from hypo_agent.gateway.qq_ws_client import NapCatWebSocketClient
from hypo_agent.gateway.settings import load_gateway_settings
from hypo_agent.gateway.ws import broadcast_message, connection_manager, router as ws_router
from hypo_agent.memory import MemoryGC, SemanticMemory, SessionMemory, StructuredStore
from hypo_agent.models import Message, QQServiceConfig, SecurityConfig, SkillOutput, WeixinServiceConfig
from hypo_agent.security import CircuitBreaker, PermissionManager
from hypo_agent.skills import (
    AgentSearchSkill,
    CodeRunSkill,
    EmailScannerSkill,
    ExportSkill,
    FileSystemSkill,
    InfoReachSkill,
    MemorySkill,
    ReminderSkill,
    TmuxSkill,
)

logger = structlog.get_logger("hypo_agent.gateway.app")
TEST_MODE_BANNER = "⚠️  HYPO_TEST_MODE enabled — data isolated to test/sandbox/"


def _parse_fixed_times(raw: str) -> list[tuple[int, int]]:
    parsed: list[tuple[int, int]] = []
    for chunk in str(raw or "").split(","):
        item = chunk.strip()
        if not item:
            continue
        hour_text, _, minute_text = item.partition(":")
        if not minute_text:
            continue
        try:
            hour = int(hour_text)
            minute = int(minute_text)
        except ValueError:
            continue
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            parsed.append((hour, minute))
    return parsed


@dataclass(slots=True)
class AppDeps:
    session_memory: SessionMemory
    structured_store: StructuredStore
    semantic_memory: SemanticMemory | None = None
    image_renderer: ImageRenderer | Any | None = None
    persona_manager: PersonaManager | None = None
    sop_manager: SopManager | None = None
    event_queue: EventQueue | None = None
    scheduler: SchedulerService | None = None
    output_compressor: OutputCompressor | None = None
    skill_manager: SkillManager | None = None
    circuit_breaker: CircuitBreaker | None = None
    permission_manager: PermissionManager | None = None
    heartbeat_service: HeartbeatService | Any | None = None
    memory_gc: MemoryGC | Any | None = None
    reload_config: Any | None = None


def _register_enabled_skills(
    *,
    skill_manager: SkillManager,
    permission_manager: PermissionManager,
    structured_store: StructuredStore | None = None,
    scheduler: SchedulerService | None = None,
    message_queue: EventQueue | Any | None = None,
    model_router: ModelRouter | None = None,
    heartbeat_service: HeartbeatService | Any | None = None,
    image_renderer: ImageRenderer | Any | None = None,
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
    email_scanner_cfg = per_skill.get("email_scanner", {}) if isinstance(per_skill, dict) else {}
    info_reach_cfg = per_skill.get("info_reach", {}) if isinstance(per_skill, dict) else {}
    tmux_timeout = int(tmux_cfg.get("timeout_seconds", default_timeout))
    code_run_timeout = int(code_run_cfg.get("timeout_seconds", default_timeout))
    auto_confirm = bool(reminder_cfg.get("auto_confirm", True))
    email_mark_as_read = bool(email_scanner_cfg.get("mark_as_read", True))
    trendradar_output_root = str(info_reach_cfg.get("output_root", "~/trendradar/output"))

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

    if "agent_search" in enabled_skills:
        skill_manager.register(AgentSearchSkill())

    if "info_reach" in enabled_skills and structured_store is not None:
        skill_manager.register(
            InfoReachSkill(
                structured_store=structured_store,
                permission_manager=permission_manager,
                model_router=model_router,
                message_queue=message_queue,
                heartbeat_service=heartbeat_service,
                output_root=trendradar_output_root,
            )
        )

    if "export" in enabled_skills and image_renderer is not None:
        skill_manager.register(
            ExportSkill(
                image_renderer=image_renderer,
            )
        )

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
            mark_as_read=email_mark_as_read,
        )
        skill_manager.register(email_skill)

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
    image_renderer = ImageRenderer()
    scheduler = SchedulerService(
        structured_store=structured_store,
        event_queue=event_queue,
    )
    heartbeat_service = HeartbeatService(
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
        message_queue=event_queue,
        heartbeat_service=heartbeat_service,
        image_renderer=image_renderer,
    )

    return AppDeps(
        session_memory=SessionMemory(buffer_limit=20, active_window_days=7),
        structured_store=structured_store,
        image_renderer=image_renderer,
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
    persona_path = Path("config/persona.yaml")
    knowledge_dir = get_memory_dir() / "knowledge"
    if deps.semantic_memory is None:
        deps.semantic_memory = SemanticMemory(
            structured_store=deps.structured_store,
            model_router=router,
        )
    else:
        deps.semantic_memory.model_router = router
    if deps.persona_manager is None:
        deps.persona_manager = PersonaManager(
            persona_path=persona_path,
            semantic_memory=deps.semantic_memory,
            knowledge_dir=knowledge_dir,
        )
    else:
        deps.persona_manager.semantic_memory = deps.semantic_memory
    if deps.sop_manager is None:
        deps.sop_manager = SopManager(
            knowledge_dir=knowledge_dir,
            semantic_memory=deps.semantic_memory,
        )
    else:
        deps.sop_manager.semantic_memory = deps.semantic_memory
    if deps.memory_gc is None:
        deps.memory_gc = MemoryGC(
            session_memory=deps.session_memory,
            structured_store=deps.structured_store,
            semantic_memory=deps.semantic_memory,
            model_router=router,
            knowledge_dir=knowledge_dir,
        )
    else:
        deps.memory_gc.model_router = router
        deps.memory_gc.semantic_memory = deps.semantic_memory
        deps.memory_gc.knowledge_dir = knowledge_dir
    persona_system_prompt = ""
    if persona_path.exists() and deps.persona_manager is None:
        try:
            persona_system_prompt = render_persona_system_prompt(load_persona_config(persona_path))
        except Exception:
            logger.exception("persona.render.failed", path=str(persona_path))
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
    info_reach_skill = (
        deps.skill_manager._skills.get("info_reach")  # type: ignore[attr-defined]
        if deps.skill_manager is not None and hasattr(deps.skill_manager, "_skills")
        else None
    )
    if info_reach_skill is not None and hasattr(info_reach_skill, "model_router"):
        info_reach_skill.model_router = router
    chat_model = router.get_model_for_task("chat")
    slash_commands = SlashCommandHandler(
        router=router,
        session_memory=deps.session_memory,
        structured_store=deps.structured_store,
        circuit_breaker=deps.circuit_breaker,
        skill_manager=deps.skill_manager,
        memory_gc=deps.memory_gc,
    )
    async def _update_persona_memory_tool(
        params: dict[str, Any],
        *,
        session_id: str | None = None,
    ):
        del session_id
        payload = await deps.persona_manager.update_persona_memory(
            str(params.get("key") or ""),
            str(params.get("value") or ""),
        )
        return SkillOutput(status="success", result=payload)

    async def _save_sop_tool(
        params: dict[str, Any],
        *,
        session_id: str | None = None,
    ):
        assert deps.sop_manager is not None
        return await deps.sop_manager.save_sop(
            title=str(params.get("title") or ""),
            content=str(params.get("content") or ""),
            confirm=bool(params.get("confirm", False)),
            session_id=session_id,
        )

    async def _search_sop_tool(
        params: dict[str, Any],
        *,
        session_id: str | None = None,
    ):
        assert deps.sop_manager is not None
        return await deps.sop_manager.search_sop(
            query=str(params.get("query") or ""),
            top_k=int(params.get("top_k") or 3),
            session_id=session_id,
        )
    if deps.skill_manager is not None and deps.persona_manager is not None:
        try:
            deps.skill_manager.register_builtin_tool(
                {
                    "type": "function",
                    "function": {
                        "name": "update_persona_memory",
                        "description": (
                            "Persist a stable user preference, habit, profile detail, or reply "
                            "style into L3 semantic memory so it can be retrieved in future "
                            "conversations. Important: if the user explicitly asks you to "
                            "remember a stable preference or personal detail, you must call "
                            "this tool instead of only acknowledging it in text."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["key", "value"],
                        },
                    },
                },
                _update_persona_memory_tool,
                source="builtin_persona",
            )
        except ValueError:
            pass
    if deps.skill_manager is not None and deps.sop_manager is not None:
        try:
            deps.skill_manager.register_builtin_tool(
                {
                    "type": "function",
                    "function": {
                        "name": "save_sop",
                        "description": (
                            "Save a reusable SOP into long-term memory. Important: before calling "
                            "this tool, you must first ask the user for confirmation and wait for "
                            "explicit approval. Do not call this tool in the same turn as the "
                            "confirmation question. Only call it after the user has already agreed, "
                            "and then pass confirm=true."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "content": {"type": "string"},
                                "confirm": {"type": "boolean"},
                            },
                            "required": ["title", "content"],
                        },
                    },
                },
                _save_sop_tool,
                source="builtin_sop",
            )
        except ValueError:
            pass
        try:
            deps.skill_manager.register_builtin_tool(
                {
                    "type": "function",
                    "function": {
                        "name": "search_sop",
                        "description": (
                            "Search saved SOPs for reusable execution procedures, troubleshooting "
                            "steps, or operational playbooks before answering repetitive how-to "
                            "questions."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "top_k": {"type": "integer"},
                            },
                            "required": ["query"],
                        },
                    },
                },
                _search_sop_tool,
                source="builtin_sop",
            )
        except ValueError:
            pass
    return ChatPipeline(
        router=router,
        chat_model=chat_model,
        session_memory=deps.session_memory,
        history_window=20,
        skill_manager=deps.skill_manager,
        structured_store=deps.structured_store,
        max_react_rounds=15,
        slash_commands=slash_commands,
        output_compressor=deps.output_compressor,
        channel_adapter=WebUIAdapter(),
        event_queue=deps.event_queue,
        persona_system_prompt=persona_system_prompt,
        persona_manager=deps.persona_manager,
        semantic_memory=deps.semantic_memory,
        sop_manager=deps.sop_manager,
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


def _load_enabled_qq_service_config(config_dir: Path) -> QQServiceConfig | None:
    enabled_skills = SkillManager.find_enabled_skills(config_dir / "skills.yaml")
    if "qq" not in enabled_skills:
        return None

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

    return qq_cfg


def _build_qq_channel_service(qq_cfg: QQServiceConfig) -> QQChannelService:
    allowed_users = {item.strip() for item in qq_cfg.allowed_users if item and item.strip()}
    return QQChannelService(
        napcat_http_url=qq_cfg.napcat_http_url,
        napcat_http_token=qq_cfg.napcat_http_token,
        bot_qq=qq_cfg.bot_qq,
        allowed_users=allowed_users,
    )


def _validate_test_mode_storage_isolation(*, deps: AppDeps) -> None:
    sandbox_root = get_test_sandbox_dir()
    expected_sessions_dir = (sandbox_root / "memory" / "sessions").resolve(strict=False)
    expected_db_path = (sandbox_root / "hypo.db").resolve(strict=False)

    actual_sessions_dir = Path(getattr(deps.session_memory, "sessions_dir", "")).resolve(strict=False)
    actual_db_path = Path(getattr(deps.structured_store, "db_path", "")).resolve(strict=False)

    violations: list[str] = []
    if actual_sessions_dir != expected_sessions_dir:
        violations.append(
            f"sessions_dir={actual_sessions_dir} (expected {expected_sessions_dir})"
        )
    if actual_db_path != expected_db_path:
        violations.append(
            f"db_path={actual_db_path} (expected {expected_db_path})"
        )

    if violations:
        details = "; ".join(violations)
        raise RuntimeError(
            "HYPO_TEST_MODE requires sandbox-isolated storage. "
            f"Refusing to start with non-sandbox paths: {details}"
        )


def create_app(
    auth_token: str,
    pipeline: ChatPipeline | None = None,
    deps: AppDeps | None = None,
    security: SecurityConfig | None = None,
) -> FastAPI:
    test_mode_enabled = is_test_mode()
    resolved_deps = deps or _build_default_deps(security)
    if test_mode_enabled:
        _validate_test_mode_storage_isolation(deps=resolved_deps)
    if resolved_deps.event_queue is None:
        resolved_deps.event_queue = EventQueue()
    if resolved_deps.scheduler is None:
        resolved_deps.scheduler = SchedulerService(
            structured_store=resolved_deps.structured_store,
            event_queue=resolved_deps.event_queue,
        )
    if resolved_deps.heartbeat_service is None:
        resolved_deps.heartbeat_service = HeartbeatService(
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
    if resolved_deps.image_renderer is None:
        resolved_deps.image_renderer = ImageRenderer()

    pipeline_instance = pipeline or _build_default_pipeline(resolved_deps)
    _ensure_pipeline_lifecycle_hooks(pipeline_instance)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await resolved_deps.structured_store.init()
        image_renderer = resolved_deps.image_renderer
        if image_renderer is not None and callable(getattr(image_renderer, "initialize", None)):
            try:
                await image_renderer.initialize()
            except Exception:
                logger.warning("image_renderer.initialize_failed", exc_info=True)
            else:
                if getattr(image_renderer, "available", False):
                    logger.info("image_renderer.ready")
                else:
                    logger.warning("image_renderer.unavailable")
        semantic_memory = getattr(app.state, "semantic_memory", None)
        knowledge_dir = Path(getattr(app.state, "knowledge_dir", get_memory_dir() / "knowledge"))
        if semantic_memory is not None and callable(getattr(semantic_memory, "build_index", None)):
            try:
                await semantic_memory.build_index(knowledge_dir)
            except Exception:
                logger.exception("semantic_memory.build_index.failed", knowledge_dir=str(knowledge_dir))
        current_config_snapshot = str(
            Path(getattr(app.state, "config_dir", Path("config"))).resolve(strict=False)
        )
        refresh_narration_observer()
        if getattr(app.state, "qq_config_dir_snapshot", None) != current_config_snapshot:
            refresh_qq_channel_service()
        if resolved_deps.scheduler is not None:
            await resolved_deps.scheduler.start()
            tasks_path = Path(getattr(app.state, "config_dir", Path("config"))) / "tasks.yaml"
            if tasks_path.exists():
                try:
                    tasks_cfg = load_tasks_config(tasks_path)
                except Exception:
                    tasks_cfg = None
                app.state.tasks_config = tasks_cfg
                if tasks_cfg is not None:
                    try:
                        setattr(
                            app.state.pipeline,
                            "heartbeat_max_react_rounds",
                            tasks_cfg.heartbeat.max_rounds,
                        )
                    except Exception:
                        pass
                    heartbeat_prompt_path = (
                        Path(getattr(app.state, "config_dir", Path("config")))
                        / "heartbeat_prompt.md"
                    )
                    if (
                        resolved_deps.skill_manager is not None
                        and hasattr(resolved_deps.skill_manager, "_skills")
                    ):
                        email_skill = resolved_deps.skill_manager._skills.get("email_scanner")
                        if email_skill is not None and hasattr(email_skill, "configure_email_store"):
                            email_skill.configure_email_store(
                                max_entries=tasks_cfg.email_store.max_entries,
                                retention_days=tasks_cfg.email_store.retention_days,
                            )
                        info_reach_skill = resolved_deps.skill_manager._skills.get("info_reach")
                        if (
                            getattr(tasks_cfg, "trendradar_summary", None) is not None
                            and info_reach_skill is not None
                            and hasattr(info_reach_skill, "run_scheduled_summary")
                        ):
                            trend_cfg = tasks_cfg.trendradar_summary
                            if bool(getattr(trend_cfg, "enabled", False)):
                                fixed_times = str(getattr(trend_cfg, "time", "") or "").strip()
                                if fixed_times and hasattr(resolved_deps.scheduler, "register_cron_job"):
                                    for hour, minute in _parse_fixed_times(fixed_times):
                                        resolved_deps.scheduler.register_cron_job(
                                            f"trendradar_summary_{hour:02d}{minute:02d}",
                                            f"{minute} {hour} * * *",
                                            info_reach_skill.run_scheduled_summary,
                                        )
                                elif (
                                    getattr(trend_cfg, "mode", "interval") == "cron"
                                    and hasattr(resolved_deps.scheduler, "register_cron_job")
                                ):
                                    resolved_deps.scheduler.register_cron_job(
                                        "trendradar_summary",
                                        str(getattr(trend_cfg, "cron", "") or "").strip(),
                                        info_reach_skill.run_scheduled_summary,
                                    )
                                elif hasattr(resolved_deps.scheduler, "register_interval_job"):
                                    resolved_deps.scheduler.register_interval_job(
                                        "trendradar_summary",
                                        int(getattr(trend_cfg, "interval_minutes", 480) or 480),
                                        info_reach_skill.run_scheduled_summary,
                                    )
                    if (
                        tasks_cfg.heartbeat.enabled
                        and resolved_deps.heartbeat_service is not None
                    ):
                        if hasattr(resolved_deps.heartbeat_service, "prompt_path"):
                            resolved_deps.heartbeat_service.prompt_path = heartbeat_prompt_path
                        if (
                            tasks_cfg.heartbeat.mode == "cron"
                            and hasattr(resolved_deps.scheduler, "register_cron_job")
                        ):
                            resolved_deps.scheduler.register_cron_job(
                                "heartbeat",
                                str(tasks_cfg.heartbeat.cron or "").strip(),
                                resolved_deps.heartbeat_service.run,
                            )
                        elif hasattr(resolved_deps.scheduler, "register_interval_job"):
                            resolved_deps.scheduler.register_interval_job(
                                "heartbeat",
                                tasks_cfg.heartbeat.interval_minutes,
                                resolved_deps.heartbeat_service.run,
                            )
            else:
                app.state.tasks_config = None
            if (
                resolved_deps.memory_gc is not None
                and hasattr(resolved_deps.scheduler, "register_cron_job")
            ):
                resolved_deps.scheduler.register_cron_job(
                    "memory_gc",
                    "0 4 * * *",
                    resolved_deps.memory_gc.run,
                )
        await app.state.pipeline.start_event_consumer()
        await start_weixin_channel()
        await restart_qq_ws_client()
        app.state.directory_index_task = asyncio.create_task(run_directory_index_refresh())
        app.state.email_cache_warmup_task = asyncio.create_task(run_email_cache_warmup())
        try:
            yield
        finally:
            email_cache_warmup_task = getattr(app.state, "email_cache_warmup_task", None)
            if email_cache_warmup_task is not None:
                if not email_cache_warmup_task.done():
                    email_cache_warmup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await email_cache_warmup_task
            directory_index_task = getattr(app.state, "directory_index_task", None)
            if directory_index_task is not None:
                if not directory_index_task.done():
                    directory_index_task.cancel()
                with suppress(asyncio.CancelledError):
                    await directory_index_task
            qq_ws_client = getattr(app.state, "qq_ws_client", None)
            if qq_ws_client is not None:
                await qq_ws_client.stop()
            await stop_weixin_channel()
            await app.state.pipeline.stop_event_consumer()
            if resolved_deps.scheduler is not None:
                await resolved_deps.scheduler.stop()
            if image_renderer is not None and callable(getattr(image_renderer, "shutdown", None)):
                try:
                    await image_renderer.shutdown()
                except Exception:
                    logger.exception("image_renderer.shutdown_failed")

    app = FastAPI(title="Hypo-Agent Gateway", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(WsTokenAuthMiddleware, auth_token=auth_token)

    memory_dir = get_memory_dir()
    knowledge_dir = memory_dir / "knowledge"
    sessions_dir = memory_dir / "sessions"
    uploads_dir = memory_dir / "uploads"
    db_path = Path(getattr(resolved_deps.structured_store, "db_path", get_database_path()))
    memory_dir.mkdir(parents=True, exist_ok=True)
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    app.state.auth_token = auth_token
    app.state.started_at = datetime.now(UTC)
    app.state.config_dir = Path("config")
    app.state.knowledge_dir = knowledge_dir
    app.state.uploads_dir = uploads_dir
    app.state.runtime_mode = "test" if test_mode_enabled else "prod"
    app.state.test_mode = test_mode_enabled
    app.state.db_path = db_path
    app.state.deps = resolved_deps
    app.state.session_memory = resolved_deps.session_memory
    app.state.structured_store = resolved_deps.structured_store
    app.state.semantic_memory = resolved_deps.semantic_memory
    app.state.image_renderer = resolved_deps.image_renderer
    app.state.persona_manager = resolved_deps.persona_manager
    app.state.sop_manager = resolved_deps.sop_manager
    app.state.skill_manager = resolved_deps.skill_manager
    app.state.circuit_breaker = resolved_deps.circuit_breaker
    app.state.permission_manager = resolved_deps.permission_manager
    app.state.output_compressor = resolved_deps.output_compressor
    app.state.event_queue = resolved_deps.event_queue
    app.state.scheduler = resolved_deps.scheduler
    app.state.heartbeat_service = resolved_deps.heartbeat_service
    app.state.memory_gc = resolved_deps.memory_gc
    app.state.pipeline = pipeline_instance
    connection_manager.reset()
    app.state.ws_connection_manager = connection_manager
    app.state.channel_dispatcher = ChannelDispatcher()
    app.state.qq_channel_service = None
    app.state.qq_ws_client = None
    app.state.qq_ws_url = None
    app.state.qq_ws_token = ""
    app.state.weixin_channel = None
    app.state.weixin_adapter = None
    app.state.qq_config_dir_snapshot = None
    app.state.directory_index_task = None
    app.state.email_cache_warmup_task = None
    app.state.narration_observer = None
    app.state.tasks_config = None

    if test_mode_enabled:
        logger.warning(
            "test_mode.enabled",
            banner=TEST_MODE_BANNER,
            sandbox_root=str(get_test_sandbox_dir()),
            mode="test",
        )

    def external_channels_disabled_for(config_dir: Path) -> bool:
        if test_mode_enabled:
            return True
        if not os.getenv("PYTEST_CURRENT_TEST"):
            return False
        repo_config_dir = (get_agent_root() / "config").resolve(strict=False)
        return config_dir.resolve(strict=False) == repo_config_dir

    async def push_ws_message(
        payload: dict[str, object],
        *,
        exclude_client_ids: set[str] | None = None,
    ) -> None:
        await broadcast_message(payload, exclude_client_ids=exclude_client_ids)

    async def push_webui_message(
        message: Message,
        *,
        exclude_client_ids: set[str] | None = None,
    ) -> None:
        await push_ws_message(
            message.model_dump(mode="json"),
            exclude_client_ids=exclude_client_ids,
        )

    app.state.channel_dispatcher.register("webui", push_webui_message)

    def should_sync_webui_session_to_external(message: Message) -> bool:
        if str(message.channel).strip().lower() != "webui":
            return True
        return str(message.session_id or "").strip() == "main"

    def qq_runtime_connected() -> bool:
        service = getattr(app.state, "qq_channel_service", None)
        client = getattr(app.state, "qq_ws_client", None)
        if service is None or client is None:
            return False
        if hasattr(client, "get_status"):
            try:
                status = client.get_status()
            except Exception:
                return False
            transport_connected = str(status.get("status") or "").strip().lower() == "connected"
        else:
            transport_connected = str(getattr(client, "status", "")).strip().lower() == "connected"
        if not transport_connected:
            return False
        if hasattr(service, "is_runtime_online"):
            try:
                runtime_online = service.is_runtime_online()
            except Exception:
                logger.warning("qq.runtime.status_check_failed", exc_info=True)
            else:
                if runtime_online is False:
                    return False
        return True

    async def mirror_webui_message_to_qq(message: Message) -> None:
        if str(message.channel).strip().lower() != "webui":
            return
        if not should_sync_webui_session_to_external(message):
            return
        service = getattr(app.state, "qq_channel_service", None)
        if service is None or not qq_runtime_connected():
            return
        if not callable(getattr(service, "send_message", None)):
            return

        raw_text = str(message.text or "").strip()
        if not raw_text:
            return
        if bool(message.metadata.get("ephemeral")):
            return

        prefix = "[WebUI] Assistant: "
        if message.sender == "user":
            prefix = "[WebUI] User: "
        elif message.sender != "assistant":
            return

        await service.send_message(
            Message(
                text=f"{prefix}{raw_text}",
                sender="assistant",
                session_id=message.session_id,
                channel="webui",
            )
        )

    async def emit_narration(
        payload: dict[str, Any],
        *,
        origin_channel: str | None = None,
        sender_id: str | None = None,
    ) -> None:
        session_id = str(payload.get("session_id") or "main")
        text = str(payload.get("text") or "").strip()
        if not text:
            return

        event_payload = {
            "type": "narration",
            "text": text,
            "session_id": session_id,
            "timestamp": str(payload.get("timestamp") or utc_isoformat(utc_now())),
        }
        await push_ws_message(event_payload)

        if str(origin_channel or "").strip().lower() != "qq":
            return
        service = getattr(app.state, "qq_channel_service", None)
        if service is None or not qq_runtime_connected():
            return
        if not callable(getattr(service, "send_message", None)):
            return
        await service.send_message(
            Message(
                text=text,
                sender="assistant",
                session_id=session_id,
                channel="qq",
                sender_id=sender_id,
            )
        )

    def refresh_narration_observer() -> None:
        observer = None
        router = getattr(app.state.pipeline, "router", None)
        config_path = Path(getattr(app.state, "config_dir", Path("config"))) / "narration.yaml"
        if config_path.exists() and router is not None and callable(getattr(router, "call", None)):
            try:
                candidate = NarrationObserver(router=router, config_path=config_path)
            except Exception:
                logger.exception("narration.config.load_failed", path=str(config_path))
            else:
                if candidate.enabled:
                    observer = candidate
        app.state.narration_observer = observer
        setattr(app.state.pipeline, "narration_observer", observer)
        setattr(app.state.pipeline, "on_narration", emit_narration)

    def refresh_qq_channel_service() -> None:
        app.state.channel_dispatcher.unregister("qq")
        existing_service = getattr(app.state, "qq_channel_service", None)
        existing_snapshot = getattr(app.state, "qq_config_dir_snapshot", None)
        config_dir = Path(getattr(app.state, "config_dir", Path("config")))
        app.state.qq_config_dir_snapshot = str(config_dir.resolve(strict=False))
        if existing_service is not None and existing_snapshot is None:
            app.state.qq_channel_service = existing_service
            app.state.channel_dispatcher.register("qq", existing_service.send_message)
            logger.info("qq.channel.prebound", config_dir=str(config_dir))
            return
        if external_channels_disabled_for(config_dir):
            app.state.qq_channel_service = None
            app.state.qq_ws_url = None
            app.state.qq_ws_token = ""
            logger.info("qq_adapter.skip", reason="test_mode", mode="test")
            return
        qq_cfg = _load_enabled_qq_service_config(config_dir)
        app.state.qq_ws_url = None
        app.state.qq_ws_token = ""
        if qq_cfg is None:
            app.state.qq_channel_service = None
            logger.info("qq.channel.disabled", config_dir=str(config_dir))
            return

        def on_message_sent() -> None:
            client = getattr(app.state, "qq_ws_client", None)
            if client is not None and hasattr(client, "record_message_sent"):
                client.record_message_sent()

        service = QQChannelService(
            napcat_http_url=qq_cfg.napcat_http_url,
            napcat_http_token=qq_cfg.napcat_http_token,
            image_renderer=resolved_deps.image_renderer,
            bot_qq=qq_cfg.bot_qq,
            allowed_users={item.strip() for item in qq_cfg.allowed_users if item and item.strip()},
            on_message_sent=on_message_sent,
        )
        app.state.qq_channel_service = service
        app.state.qq_ws_url = qq_cfg.napcat_ws_url
        app.state.qq_ws_token = qq_cfg.napcat_ws_token
        app.state.channel_dispatcher.register("qq", service.send_message)
        logger.info(
            "qq.channel.enabled",
            config_dir=str(config_dir),
            napcat_ws_url=qq_cfg.napcat_ws_url,
            napcat_http_url=qq_cfg.napcat_http_url,
            allowed_users=len(service.allowed_users),
        )

    def _load_enabled_weixin_service_config(config_dir: Path) -> WeixinServiceConfig | None:
        secrets_path = config_dir / "secrets.yaml"
        try:
            secrets = load_secrets_config(secrets_path)
        except FileNotFoundError:
            return None
        except Exception:
            logger.exception("weixin.config.load_failed", path=str(secrets_path))
            return None

        services = secrets.services
        weixin_cfg = services.weixin if services is not None else None
        if weixin_cfg is None or not weixin_cfg.enabled:
            return None
        return weixin_cfg

    def refresh_weixin_channel() -> None:
        app.state.channel_dispatcher.unregister("weixin")
        app.state.weixin_adapter = None
        config_dir = Path(getattr(app.state, "config_dir", Path("config")))
        if external_channels_disabled_for(config_dir):
            app.state.weixin_channel = None
            logger.info("weixin.channel.disabled", reason="test_mode")
            return
        weixin_cfg = _load_enabled_weixin_service_config(config_dir)
        if weixin_cfg is None:
            app.state.weixin_channel = None
            logger.info("weixin.channel.disabled", config_dir=str(config_dir))
            return

        app.state.weixin_channel = WeixinChannel(
            config=weixin_cfg,
            message_queue=resolved_deps.event_queue,
            build_message=Message,
            inbound_callback_getter=lambda: getattr(app.state.pipeline, "on_proactive_message", None),
        )
        logger.info(
            "weixin.channel.configured",
            config_dir=str(config_dir),
            token_path=weixin_cfg.token_path,
            allowed_users=len(weixin_cfg.allowed_users),
        )

    async def start_weixin_channel() -> None:
        channel = getattr(app.state, "weixin_channel", None)
        if channel is None:
            return
        await channel.start()
        client = getattr(channel, "client", None)
        bot_token = str(getattr(client, "bot_token", "") or "").strip()
        user_id = str(getattr(client, "user_id", "") or "").strip()
        if client is None or not bot_token:
            return
        adapter = WeixinAdapter(
            client=client,
            target_user_id="",
            image_renderer=resolved_deps.image_renderer,
            on_message_sent=channel.record_message_sent,
        )
        app.state.weixin_adapter = adapter
        app.state.channel_dispatcher.register("weixin", adapter.push)
        if not user_id:
            logger.warning(
                "weixin.adapter.target_user_missing",
                hint="Will use the latest inbound weixin sender once available",
            )
        logger.info("weixin.adapter.enabled", user_id=user_id)

    async def stop_weixin_channel() -> None:
        app.state.channel_dispatcher.unregister("weixin")
        app.state.weixin_adapter = None
        channel = getattr(app.state, "weixin_channel", None)
        if channel is not None:
            await channel.stop()

    async def restart_qq_ws_client() -> None:
        existing_client = getattr(app.state, "qq_ws_client", None)
        if existing_client is not None:
            await existing_client.stop()
            app.state.qq_ws_client = None

        service = getattr(app.state, "qq_channel_service", None)
        url = str(getattr(app.state, "qq_ws_url", "") or "").strip()
        if service is None or not url:
            return

        client = NapCatWebSocketClient(
            url=url,
            bot_qq=str(getattr(service, "bot_qq", "") or ""),
            token=str(getattr(app.state, "qq_ws_token", "") or ""),
            service_getter=lambda: getattr(app.state, "qq_channel_service", None),
            pipeline_getter=lambda: getattr(app.state, "pipeline", None),
        )
        app.state.qq_ws_client = client
        await client.start()

    async def run_directory_index_refresh() -> None:
        index_file = Path(app.state.knowledge_dir) / "directory_index.yaml"
        try:
            await refresh_directory_index(index_file=index_file)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "directory_index.refresh.failed",
                index_file=str(index_file),
            )

    async def run_email_cache_warmup() -> None:
        tasks_cfg = getattr(app.state, "tasks_config", None)
        if tasks_cfg is None or not getattr(tasks_cfg.email_store, "enabled", False):
            return
        skill_manager = getattr(app.state, "skill_manager", None)
        if skill_manager is None or not hasattr(skill_manager, "_skills"):
            return
        email_skill = skill_manager._skills.get("email_scanner")
        if email_skill is None:
            return
        email_store = getattr(email_skill, "email_store", None)
        if email_store is None or not hasattr(email_store, "needs_warmup"):
            return
        try:
            should_warm = bool(email_store.needs_warmup(max_age_hours=24))
        except Exception:
            logger.exception("email.cache_warmup.check_failed")
            return
        if not should_warm or not callable(getattr(email_skill, "scan_emails", None)):
            return
        try:
            await email_skill.scan_emails(
                params={
                    "hours_back": tasks_cfg.email_store.warmup_hours,
                    "triggered_by": "cache_warmup",
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("email.cache_warmup.failed")

    async def on_proactive_message(
        message: Message,
        *,
        exclude_channels: set[str] | None = None,
        exclude_client_ids: set[str] | None = None,
    ) -> None:
        merged_exclusions = {item for item in (exclude_channels or set()) if item}
        target_channels_raw = message.metadata.get("target_channels")
        target_channels = {
            str(item).strip().lower()
            for item in target_channels_raw
            if str(item).strip()
        } if isinstance(target_channels_raw, list) else None
        if target_channels is not None:
            external_channels = {"qq", "weixin"}
            merged_exclusions.update(external_channels - target_channels)
        if str(message.channel).strip().lower() == "webui":
            if should_sync_webui_session_to_external(message):
                merged_exclusions.add("qq")
            else:
                merged_exclusions.update({"qq", "weixin"})
        await app.state.channel_dispatcher.broadcast(
            message,
            exclude_channels=merged_exclusions,
            exclude_client_ids=exclude_client_ids,
        )
        await mirror_webui_message_to_qq(message)

    app.state.push_ws_message = push_ws_message
    app.state.mirror_webui_message_to_qq = mirror_webui_message_to_qq
    setattr(app.state.pipeline, "on_proactive_message", on_proactive_message)
    app.state.emit_narration = emit_narration
    refresh_narration_observer()

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
            message_queue=deps.event_queue,
            heartbeat_service=deps.heartbeat_service,
            image_renderer=deps.image_renderer,
        )

        deps.output_compressor = None
        await previous_pipeline.stop_event_consumer()
        app.state.auth_token = settings.auth_token
        app.state.permission_manager = deps.permission_manager
        app.state.circuit_breaker = deps.circuit_breaker
        app.state.skill_manager = deps.skill_manager
        app.state.pipeline = _build_default_pipeline(deps)
        app.state.semantic_memory = deps.semantic_memory
        app.state.persona_manager = deps.persona_manager
        _ensure_pipeline_lifecycle_hooks(app.state.pipeline)
        setattr(app.state.pipeline, "on_proactive_message", on_proactive_message)
        app.state.output_compressor = deps.output_compressor
        refresh_narration_observer()
        await app.state.pipeline.start_event_consumer()
        await stop_weixin_channel()
        refresh_weixin_channel()
        await start_weixin_channel()
        refresh_qq_channel_service()
        await restart_qq_ws_client()

    resolved_deps.reload_config = reload_config
    app.state.reload_config = reload_config
    refresh_qq_channel_service()
    refresh_weixin_channel()

    app.include_router(ws_router)
    app.include_router(qq_ws_router)
    app.include_router(sessions_api_router)
    app.include_router(compressed_api_router)
    app.include_router(files_api_router)
    app.include_router(kill_switch_api_router)
    app.include_router(dashboard_api_router)
    app.include_router(config_api_router)
    app.include_router(memory_api_router)
    app.include_router(upload_api_router)
    return app
