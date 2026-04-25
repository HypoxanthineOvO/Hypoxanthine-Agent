from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from contextvars import ContextVar
from datetime import datetime, timedelta
import inspect
import json
from pathlib import Path
import re
from math import ceil
from time import perf_counter
from typing import Any, Protocol

import structlog

from hypo_agent.core.channel_adapter import ChannelAdapter, WebUIAdapter
from hypo_agent.core.config_loader import get_memory_dir
from hypo_agent.core.model_runtime_context import build_runtime_model_context
from hypo_agent.core.notion_todo_binding import (
    confirm_pending_notion_todo_candidate,
    get_pending_notion_todo_candidate,
    message_confirms_notion_todo_candidate,
    message_rejects_notion_todo_candidate,
    reject_pending_notion_todo_candidate,
)
from hypo_agent.core.rich_response import RichResponse
from hypo_agent.core.skill_catalog import SkillManifest
from hypo_agent.core.time_utils import utc_isoformat, utc_now
from hypo_agent.core.uploads import guess_mime_type, sanitize_upload_filename
from hypo_agent.exceptions import HypoAgentError
from hypo_agent.memory.semantic_memory import estimate_token_count
from hypo_agent.memory.session import SessionMemory
from hypo_agent.models import Attachment, Message, SkillOutput
from hypo_agent.utils.timeutil import now_local

logger = structlog.get_logger("hypo_agent.core.pipeline")

_PIPELINE_INTERNAL_SOURCE: ContextVar[str] = ContextVar(
    "pipeline_internal_source",
    default="",
)
_PIPELINE_SUPPRESS_PERSISTENCE: ContextVar[bool] = ContextVar(
    "pipeline_suppress_persistence",
    default=False,
)
_PIPELINE_SUPPRESS_BROADCAST: ContextVar[bool] = ContextVar(
    "pipeline_suppress_broadcast",
    default=False,
)
_PIPELINE_SUPPRESS_TOOL_STATUS: ContextVar[bool] = ContextVar(
    "pipeline_suppress_tool_status",
    default=False,
)
_PIPELINE_SUPPRESS_HISTORY: ContextVar[bool] = ContextVar(
    "pipeline_suppress_history",
    default=False,
)

TOOL_USE_SYSTEM_PROMPT = (
    "You are an assistant with access to tools. "
    "When the user asks you to execute a command or run code, you MUST use "
    "the provided tools instead of describing the action in text. "
    "Always prefer using tools over explaining what you would do. "
    "For filesystem tasks, do not assume permission is missing before trying. "
    "If the user asks you to read or inspect files/directories, call tools such as "
    "read_file or list_directory first. Only tell the user that access is denied "
    "after a tool actually returns a permission error. "
    "In this environment, ordinary file reads are usually allowed, while writes "
    "require explicit permission. "
    "When the user expresses stable preferences/habits/personal details, "
    "you MUST call update_persona_memory(key, value) to persist them in long-term memory. "
    "If the user explicitly asks you to remember a preference, profile detail, or reply style, "
    "do not only answer with text like '好的，我记住了'; call update_persona_memory first. "
    "Use save_preference(key, value) and get_preference(key) for structured key-value memory when needed. "
    "When you derive a reusable workflow or troubleshooting playbook, use save_sop(title, content) "
    "only after the user has explicitly approved saving it in a prior turn. "
    "You must first ask for confirmation, wait for the user's explicit approval, "
    "and never call save_sop in the same turn as the confirmation question. "
    "When a task looks repetitive or operational, use search_sop(query, top_k) to retrieve saved SOPs."
    " When the user reports a broken feature or you detect repeated tool failures, "
    "first diagnose with get_error_summary(hours=24) and get_tool_history(success=false, hours=24). "
    "If the issue is likely code/config related instead of a transient network problem, "
    "submit a repair task with coder_submit_task only after diagnosis. "
    "After submitting the repair, tell the user you have escalated it and wait for Codex/Coder completion. "
    "Do not submit blind repair tasks without diagnostic evidence. "
    " After a tool returns, write the final user-facing reply in natural language. "
    "Do not dump raw JSON, serialized tool payloads, or internal status wrappers unless the user explicitly asks for raw data."
)

REPLY_BOUNDARY_SYSTEM_PROMPT = (
    "High priority reply rule: answer the user's direct request and then stop. "
    "Do not append unsolicited follow-up questions, next-step suggestions, offers, or "
    "service-style closings such as '要不要我帮你……'、'需要我继续……吗？'、'还有什么我可以帮你'. "
    "Only provide options/next steps when the user explicitly asks for them. "
    "Only ask a follow-up question when missing information blocks a correct answer, and "
    "then ask at most one precise question."
)

KILL_SWITCH_MESSAGE = "⚠️ Kill Switch 已激活。所有执行已停止。发送 /resume 恢复。"
SESSION_FUSED_MESSAGE = "⚠️ 本次对话累计错误过多，已暂停执行。请检查问题后重新发送消息继续。"
LONG_OUTPUT_EXPORT_THRESHOLD_CHARS = 20_000

TOOL_STATUS_TEMPLATES: dict[str, dict[str, str]] = {
    "create_reminder": {
        "start": "🔔 正在创建提醒...",
        "ok": "✅ 提醒创建成功",
        "fail": "❌ 创建提醒失败：{error}",
    },
    "list_reminders": {
        "start": "📋 正在查询提醒列表...",
        "ok": "📋 提醒列表已获取",
        "fail": "❌ 查询提醒失败：{error}",
    },
    "delete_reminder": {
        "start": "🗑️ 正在删除提醒...",
        "ok": "✅ 提醒已删除",
        "fail": "❌ 删除提醒失败：{error}",
    },
    "update_reminder": {
        "start": "✏️ 正在更新提醒...",
        "ok": "✅ 提醒已更新",
        "fail": "❌ 更新提醒失败：{error}",
    },
    "snooze_reminder": {
        "start": "💤 正在延后提醒...",
        "ok": "✅ 提醒已延后",
        "fail": "❌ 延后提醒失败：{error}",
    },
    "run_code": {
        "start": "⚡ 正在执行代码...",
        "ok": "✅ 代码执行完成",
        "fail": "❌ 代码执行失败：{error}",
    },
    "exec_command": {
        "start": "⚡ 正在执行命令...",
        "ok": "✅ 命令执行完成",
        "fail": "❌ 命令执行失败：{error}",
    },
    "search_web": {
        "start": "🔍 正在搜索...",
        "ok": "🔍 搜索完成",
        "fail": "❌ 搜索失败：{error}",
    },
    "web_search": {
        "start": "🔍 正在搜索...",
        "ok": "🔍 搜索完成",
        "fail": "❌ 搜索失败：{error}",
    },
    "_default": {
        "start": "",
        "ok": "✅ 处理完成",
        "fail": "❌ 处理失败：{error}",
    },
}

_PIPELINE_RECOVERABLE_ERRORS = (
    HypoAgentError,
    asyncio.TimeoutError,
    TimeoutError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)

_HISTORY_SKIP_MESSAGE_TAGS = {
    "reminder",
    "heartbeat",
    "email_scan",
    "scheduler",
    "tool_status",
    "subscription",
}

_CORE_TOOL_WHITELIST = {
    "update_persona_memory",
    "save_sop",
    "search_sop",
    "search_web",
    "web_search",
    "web_read",
    "exec_command",
    "run_code",
    "read_file",
    "write_file",
    "list_directory",
    "create_reminder",
    "list_reminders",
    "update_reminder",
    "delete_reminder",
    "snooze_reminder",
    "save_preference",
    "get_preference",
}

_RETRYABLE_TOOLS = {
    "search_web",
    "web_search",
    "read_file",
    "get_recent_logs",
    "get_session_history",
    "get_tool_history",
    "get_mail_snapshot",
    "get_reminder_snapshot",
    "get_notion_todo_snapshot",
    "get_heartbeat_snapshot",
}

LEGACY_TOOL_NAME_REMAP = {
    "web_search": "search_web",
}

_ACCESS_DENIED_REPLY_PATTERNS = (
    "无法访问",
    "无法读取",
    "没有权限",
    "access denied",
    "cannot access",
    "permission denied",
)


def _error_fields(exc: Exception) -> dict[str, str]:
    message = str(exc).strip()
    if len(message) > 200:
        message = f"{message[:197]}..."
    return {
        "error_type": type(exc).__name__,
        "error_msg": message,
    }


class ChatModelRouter(Protocol):
    def get_model_for_task(self, task_type: str) -> str: ...

    async def call(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        timeout_seconds: float | None = None,
        task_type: str | None = None,
        event_emitter: Any | None = None,
    ) -> str: ...

    async def call_with_tools(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
        timeout_seconds: float | None = None,
        task_type: str | None = None,
        event_emitter: Any | None = None,
    ) -> dict[str, Any]: ...

    async def stream(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        session_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        timeout_seconds: float | None = None,
        task_type: str | None = None,
        event_emitter: Any | None = None,
    ) -> AsyncIterator[str]: ...


class ChatSkillManager(Protocol):
    def get_tools_schema(
        self,
        *,
        tool_names: set[str] | None = None,
        skill_names: set[str] | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_skill_catalog(self) -> str: ...

    def get_skill_tools_schema(self, skill_name: str) -> list[dict[str, Any]]: ...

    def find_skill_by_tool_name(self, tool_name: str) -> Any | None: ...

    def match_skills_for_text(self, text: str) -> list[str]: ...

    async def invoke(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        session_id: str | None = None,
        skill_name: str | None = None,
    ) -> SkillOutput: ...


class SlashCommands(Protocol):
    async def try_handle(self, inbound: Message) -> str | None: ...


class ChatOutputCompressor(Protocol):
    async def compress_if_needed(
        self,
        output: str,
        metadata: dict[str, Any],
        *,
        tool_name: str | None = None,
    ) -> tuple[str, bool]: ...


class ChatPipeline:
    def __init__(
        self,
        router: ChatModelRouter,
        chat_model: str,
        session_memory: SessionMemory,
        heartbeat_chat_model: str | None = None,
        history_window: int = 100,
        history_token_budget: int = 7000,
        skill_manager: ChatSkillManager | None = None,
        structured_store: Any | None = None,
        circuit_breaker: Any | None = None,
        max_react_rounds: int = 8,
        max_react_timeout_seconds: int | None = 120,
        heartbeat_max_react_rounds: int | None = None,
        heartbeat_react_timeout_seconds: int | None = 180,
        heartbeat_model_timeout_seconds: int | None = 60,
        heartbeat_allowed_tools: set[str] | None = None,
        slash_commands: SlashCommands | None = None,
        output_compressor: ChatOutputCompressor | None = None,
        long_output_export_dir: Path | str | None = None,
        long_output_threshold_chars: int = LONG_OUTPUT_EXPORT_THRESHOLD_CHARS,
        channel_adapter: ChannelAdapter | None = None,
        event_queue: Any | None = None,
        event_emitter: Any | None = None,
        on_proactive_message: Any | None = None,
        persona_system_prompt: str = "",
        persona_manager: Any | None = None,
        semantic_memory: Any | None = None,
        sop_manager: Any | None = None,
        narration_observer: Any | None = None,
        on_narration: Any | None = None,
        skill_catalog: Any | None = None,
        coder_task_service: Any | None = None,
        wewe_rss_monitor: Any | None = None,
    ) -> None:
        self.router = router
        self.chat_model = chat_model
        self.session_memory = session_memory
        self.history_window = history_window
        self.history_token_budget = max(0, int(history_token_budget))
        self.skill_manager = skill_manager
        self.structured_store = structured_store
        self.circuit_breaker = circuit_breaker
        self.max_react_rounds = max_react_rounds
        self.max_react_timeout_seconds = (
            None
            if max_react_timeout_seconds is None
            else max(1, int(max_react_timeout_seconds))
        )
        self.heartbeat_chat_model = str(heartbeat_chat_model or "").strip() or None
        self.heartbeat_max_react_rounds = heartbeat_max_react_rounds
        self.heartbeat_react_timeout_seconds = (
            None
            if heartbeat_react_timeout_seconds is None
            else max(1, int(heartbeat_react_timeout_seconds))
        )
        self.heartbeat_model_timeout_seconds = heartbeat_model_timeout_seconds
        self.heartbeat_allowed_tools = set(heartbeat_allowed_tools or set())
        self.slash_commands = slash_commands
        self.output_compressor = output_compressor
        self.long_output_threshold_chars = max(1, int(long_output_threshold_chars))
        self.long_output_export_dir = Path(
            long_output_export_dir or (get_memory_dir() / "exports")
        ).expanduser().resolve(strict=False)
        self.long_output_export_dir.mkdir(parents=True, exist_ok=True)
        self.channel_adapter = channel_adapter or WebUIAdapter()
        self.event_queue = event_queue
        self.event_emitter = event_emitter
        self.on_proactive_message = on_proactive_message
        self.persona_system_prompt = persona_system_prompt.strip()
        self.persona_manager = persona_manager
        self.semantic_memory = semantic_memory
        self.sop_manager = sop_manager
        self.narration_observer = narration_observer
        self.on_narration = on_narration
        self.skill_catalog = skill_catalog
        self.coder_task_service = coder_task_service
        self.wewe_rss_monitor = wewe_rss_monitor
        self._event_consumer_task: asyncio.Task[None] | None = None
        self._pending_sop_usage: set[str] = set()
        self._last_activity_at = utc_isoformat(utc_now())
        self._last_activity_monotonic = perf_counter()
        self._session_loaded_skills: dict[str, set[str]] = {}

    async def start_event_consumer(self) -> None:
        if self.event_queue is None:
            return
        if self._event_consumer_task is not None and not self._event_consumer_task.done():
            return
        self._mark_activity(reason="event_consumer_start")
        self._event_consumer_task = asyncio.create_task(self._consume_event_loop())

    async def stop_event_consumer(self) -> None:
        task = self._event_consumer_task
        if task is None:
            return
        self._event_consumer_task = None
        self._mark_activity(reason="event_consumer_stop")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _resolve_event_emitter(self, override: Any | None = None) -> Any | None:
        return override or self.event_emitter

    def _model_fallback_window_pref_key(self, provider: str) -> str:
        return f"model_fallback_window.{provider}"

    def _model_fallback_alert_pref_key(self, provider: str) -> str:
        return f"model_fallback_alert_last.{provider}"

    async def _record_model_fallback_observability(self, event: dict[str, Any]) -> None:
        if self.structured_store is None:
            return
        provider = str(event.get("provider") or "").strip().lower()
        if not provider:
            return

        now = utc_now()
        window_key = self._model_fallback_window_pref_key(provider)
        raw_window = await self.structured_store.get_preference(window_key)
        entries: list[str] = []
        if raw_window:
            try:
                parsed = json.loads(raw_window)
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                entries = [str(item).strip() for item in parsed if str(item).strip()]

        threshold = now - timedelta(minutes=30)
        retained_entries: list[str] = []
        for item in entries:
            try:
                parsed_at = datetime.fromisoformat(item)
            except ValueError:
                continue
            if parsed_at >= threshold:
                retained_entries.append(item)
        retained_entries.append(utc_isoformat(now))
        await self.structured_store.set_preference(
            window_key,
            json.dumps(retained_entries, ensure_ascii=False),
        )

        if len(retained_entries) < 3:
            return

        alert_key = self._model_fallback_alert_pref_key(provider)
        raw_last_alert = await self.structured_store.get_preference(alert_key)
        if raw_last_alert:
            try:
                last_alert_at = datetime.fromisoformat(raw_last_alert)
            except ValueError:
                last_alert_at = None
            else:
                if now - last_alert_at < timedelta(hours=24):
                    return

        message = (
            f"Provider '{provider}' triggered {len(retained_entries)} model fallbacks "
            "within 30 minutes."
        )
        await self.structured_store.record_alert(
            category="model_fallback",
            signature=f"model_fallback:{provider}",
            message=message,
            metadata={
                "provider": provider,
                "count_30m": len(retained_entries),
                "requested_model": str(event.get('requested_model') or ''),
                "failed_model": str(event.get('failed_model') or ''),
                "fallback_model": str(event.get('fallback_model') or ''),
                "reason": str(event.get('reason') or ''),
            },
        )
        await self.structured_store.set_preference(alert_key, utc_isoformat(now))

    async def _emit_progress_event(
        self,
        payload: dict[str, Any],
        *,
        event_emitter: Any | None = None,
    ) -> None:
        event = dict(payload)
        event.setdefault("timestamp", utc_isoformat(utc_now()))
        if str(event.get("type") or "").strip() == "model_fallback":
            try:
                await self._record_model_fallback_observability(event)
            except _PIPELINE_RECOVERABLE_ERRORS:
                logger.warning("pipeline.model_fallback_observability_failed", exc_info=True)
        emitter = self._resolve_event_emitter(event_emitter)
        if emitter is None:
            return
        try:
            result = emitter(event)
            if inspect.isawaitable(result):
                await result
        except _PIPELINE_RECOVERABLE_ERRORS:
            logger.warning("pipeline.progress_emit_failed", event_type=event.get("type"), exc_info=True)

    async def enqueue_user_message(
        self,
        inbound: Message,
        *,
        emit: Any,
    ) -> None:
        if self.event_queue is None:
            raise RuntimeError("event_queue is required for queued user messages")
        await self.event_queue.put(
            {
                "event_type": "user_message",
                "message": inbound,
                "emit": emit,
            }
        )

    def _is_internal_heartbeat_message(self, inbound: Message) -> bool:
        source = str(inbound.metadata.get("source") or "").strip().lower()
        event_source = str(inbound.metadata.get("event_source") or "").strip().lower()
        tag = str(inbound.message_tag or "").strip().lower()
        return source == "heartbeat" or event_source == "heartbeat" or tag == "heartbeat"

    def _current_internal_source(self) -> str:
        return str(_PIPELINE_INTERNAL_SOURCE.get()).strip().lower()

    def _session_persistence_suppressed(self) -> bool:
        return bool(_PIPELINE_SUPPRESS_PERSISTENCE.get())

    def _broadcast_suppressed(self) -> bool:
        return bool(_PIPELINE_SUPPRESS_BROADCAST.get())

    def _tool_status_context_suppressed(self) -> bool:
        return bool(_PIPELINE_SUPPRESS_TOOL_STATUS.get())

    def _history_suppressed(self) -> bool:
        return bool(_PIPELINE_SUPPRESS_HISTORY.get())

    def _augment_tool_arguments(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        skill_manifest: SkillManifest | None = None,
    ) -> dict[str, Any]:
        updated = dict(arguments)
        if tool_name == "scan_emails" and self._current_internal_source() == "heartbeat":
            updated.setdefault("triggered_by", "heartbeat")
        if (
            skill_manifest is not None
            and tool_name in {"exec_command", "exec_script"}
            and skill_manifest.exec_profile
        ):
            updated.setdefault("exec_profile", skill_manifest.exec_profile)
            if skill_manifest.exec_profile == "cli-json":
                if skill_manifest.cli_commands:
                    updated.setdefault("allowed_commands", list(skill_manifest.cli_commands))
                if skill_manifest.io_format:
                    updated.setdefault("io_format", skill_manifest.io_format)
        return updated

    def _metadata_flag_enabled(self, metadata: dict[str, Any], key: str) -> bool:
        raw = metadata.get(key)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
        if isinstance(raw, (int, float)):
            return bool(raw)
        return False

    def _is_heartbeat_request(self, inbound: Message | None = None) -> bool:
        if self._current_internal_source() == "heartbeat":
            return True
        if inbound is None:
            return False
        return self._is_internal_heartbeat_message(inbound)

    def _should_skip_semantic_memory(self, inbound: Message) -> bool:
        return self._is_heartbeat_request(inbound) or self._metadata_flag_enabled(
            inbound.metadata,
            "skip_memory_search",
        )

    def _effective_max_react_rounds(self, inbound: Message | None = None) -> int:
        if self._is_heartbeat_request(inbound):
            heartbeat_max_rounds = self.heartbeat_max_react_rounds
            if heartbeat_max_rounds is not None and heartbeat_max_rounds > 0:
                return heartbeat_max_rounds
        return self.max_react_rounds

    def _should_force_final_response(
        self,
        round_num: int,
        *,
        max_react_rounds: int,
        inbound: Message | None = None,
    ) -> bool:
        if max_react_rounds <= 0:
            return False
        if self._is_heartbeat_request(inbound):
            return round_num >= max_react_rounds
        return round_num >= max(1, max_react_rounds - 1)

    def _heartbeat_timeout_for(self, inbound: Message | None) -> float | None:
        if not self._is_heartbeat_request(inbound):
            return None
        timeout_seconds = self.heartbeat_model_timeout_seconds
        if timeout_seconds is None:
            return None
        return float(timeout_seconds)

    def _react_timeout_for(self, inbound: Message | None) -> float | None:
        if self._is_heartbeat_request(inbound):
            timeout_seconds = self.heartbeat_react_timeout_seconds
        else:
            timeout_seconds = self.max_react_timeout_seconds
        if timeout_seconds is None:
            return None
        return float(timeout_seconds)

    def _remaining_react_timeout(
        self,
        react_deadline: float | None,
    ) -> float | None:
        if react_deadline is None:
            return None
        remaining = react_deadline - perf_counter()
        if remaining <= 0:
            raise TimeoutError("ReAct timeout exceeded")
        return float(ceil(remaining))

    def _resolve_heartbeat_model(self) -> str:
        configured = str(self.heartbeat_chat_model or "").strip()
        if configured:
            return configured
        getter = getattr(self.router, "get_model_for_task", None)
        if callable(getter):
            try:
                candidate = str(getter("lightweight") or "").strip()
            except Exception:
                candidate = ""
            if candidate:
                return candidate
        return self.chat_model

    def _session_loaded_skill_names(self, session_id: str | None) -> set[str]:
        key = str(session_id or "").strip()
        if not key:
            return set()
        return set(self._session_loaded_skills.get(key, set()))

    def _remember_loaded_skills(
        self,
        session_id: str | None,
        skill_names: set[str],
    ) -> None:
        key = str(session_id or "").strip()
        normalized = {str(name).strip() for name in skill_names if str(name).strip()}
        if not key or not normalized:
            return
        current = self._session_loaded_skills.setdefault(key, set())
        current.update(normalized)

    def _skill_manager_get_tools_schema(
        self,
        *,
        tool_names: set[str] | None = None,
        skill_names: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        if self.skill_manager is None:
            return []
        filter_by_tool_names = tool_names is not None
        filter_by_skill_names = skill_names is not None
        normalized_tool_names = {str(name).strip() for name in (tool_names or set()) if str(name).strip()}
        normalized_skill_names = {str(name).strip() for name in (skill_names or set()) if str(name).strip()}
        if filter_by_tool_names and not normalized_tool_names:
            return []
        if filter_by_skill_names and not normalized_skill_names:
            return []
        getter = getattr(self.skill_manager, "get_tools_schema", None)
        if not callable(getter):
            return []

        try:
            if self._method_supports_kwarg(getter, "tool_names") or self._method_supports_kwarg(getter, "skill_names"):
                return getter(
                    tool_names=normalized_tool_names if filter_by_tool_names else None,
                    skill_names=normalized_skill_names if filter_by_skill_names else None,
                )
            tools = getter()
        except TypeError:
            tools = getter()
        if not isinstance(tools, list):
            return []

        filtered = list(tools)
        if filter_by_skill_names:
            explicit = self._skill_manager_get_skill_tools_schema_bulk(normalized_skill_names)
            if explicit:
                filtered = explicit
            else:
                filtered = [
                    tool
                    for tool in filtered
                    if (
                        (owner := self._skill_manager_find_skill_by_tool_name(
                            str(tool.get("function", {}).get("name") or "").strip()
                        ))
                        is not None
                        and owner.name in normalized_skill_names
                    )
                ]
        if filter_by_tool_names:
            filtered = [
                tool
                for tool in filtered
                if str(tool.get("function", {}).get("name") or "").strip() in normalized_tool_names
            ]
        return filtered

    def _skill_manager_get_skill_tools_schema_bulk(
        self,
        skill_names: set[str],
    ) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        seen: set[str] = set()
        for skill_name in skill_names:
            for tool in self._skill_manager_get_skill_tools_schema(skill_name):
                tool_name = str(tool.get("function", {}).get("name") or "").strip()
                if not tool_name or tool_name in seen:
                    continue
                seen.add(tool_name)
                tools.append(tool)
        return tools

    def _skill_manager_get_skill_tools_schema(self, skill_name: str) -> list[dict[str, Any]]:
        if self.skill_manager is None:
            return []
        getter = getattr(self.skill_manager, "get_skill_tools_schema", None)
        if callable(getter):
            try:
                tools = getter(skill_name)
            except TypeError:
                tools = []
            if isinstance(tools, list):
                return tools
        return []

    def _skill_manager_find_skill_by_tool_name(self, tool_name: str) -> Any | None:
        if self.skill_manager is None:
            return None
        finder = getattr(self.skill_manager, "find_skill_by_tool_name", None)
        if not callable(finder):
            return None
        try:
            return finder(tool_name)
        except TypeError:
            return None

    def _skill_manager_match_skills_for_text(self, text: str) -> list[str]:
        if self.skill_manager is None:
            return []
        matcher = getattr(self.skill_manager, "match_skills_for_text", None)
        if not callable(matcher):
            return []
        try:
            matched = matcher(text)
        except Exception:
            logger.warning("tool_exposure.preload_match_failed", exc_info=True)
            return []
        if not isinstance(matched, list):
            return []
        return [str(name).strip() for name in matched if str(name).strip()]

    def _supports_progressive_tool_disclosure(self) -> bool:
        if self.skill_manager is None:
            return False
        return callable(getattr(self.skill_manager, "find_skill_by_tool_name", None)) and callable(
            getattr(self.skill_manager, "get_skill_tools_schema", None)
        )

    def _match_preloaded_skill_names(
        self,
        inbound: Message,
        *,
        candidate_skills: list[SkillManifest] | None = None,
    ) -> set[str]:
        if self.skill_manager is None:
            return set()

        skill_names: set[str] = set()
        text = self._message_text_for_llm(inbound)
        skill_names.update(self._skill_manager_match_skills_for_text(text))

        for manifest in candidate_skills or []:
            manifest_name = str(getattr(manifest, "name", "") or "").strip()
            if manifest_name and self._skill_manager_get_skill_tools_schema(manifest_name):
                skill_names.add(manifest_name)
                continue
            for tool_name in getattr(manifest, "allowed_tools", []) or []:
                owner = self._skill_manager_find_skill_by_tool_name(tool_name)
                if owner is not None:
                    skill_names.add(owner.name)
        return skill_names

    def _core_tool_names_for_inbound(self, inbound: Message | None) -> set[str] | None:
        if self._is_heartbeat_request(inbound):
            if self.heartbeat_allowed_tools:
                return set(self.heartbeat_allowed_tools)
            return None
        return set(_CORE_TOOL_WHITELIST)

    def _merge_tool_schemas(
        self,
        *tool_groups: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in tool_groups:
            for tool in group:
                tool_name = str(tool.get("function", {}).get("name") or "").strip()
                if not tool_name or tool_name in seen:
                    continue
                merged.append(tool)
                seen.add(tool_name)
        return merged

    def _build_exposed_tools(
        self,
        *,
        inbound: Message | None,
        session_id: str | None,
        preloaded_skill_names: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assert self.skill_manager is not None
        core_tool_names = self._core_tool_names_for_inbound(inbound)
        core_tools = (
            self._skill_manager_get_tools_schema(tool_names=core_tool_names)
            if core_tool_names is not None
            else self._skill_manager_get_tools_schema()
        )
        persisted_skill_names = self._session_loaded_skill_names(session_id)
        preloaded_names = {str(name).strip() for name in (preloaded_skill_names or set()) if str(name).strip()}
        persisted_tools = self._skill_manager_get_tools_schema(skill_names=persisted_skill_names)
        preloaded_tools = self._skill_manager_get_tools_schema(skill_names=preloaded_names)
        exposed_tools = self._merge_tool_schemas(core_tools, persisted_tools, preloaded_tools)
        exposure = {
            "core_tool_names": [
                str(tool.get("function", {}).get("name") or "").strip()
                for tool in core_tools
                if str(tool.get("function", {}).get("name") or "").strip()
            ],
            "preloaded_skill_names": sorted(preloaded_names),
            "persisted_skill_names": sorted(persisted_skill_names),
            "tool_names": [
                str(tool.get("function", {}).get("name") or "").strip()
                for tool in exposed_tools
                if str(tool.get("function", {}).get("name") or "").strip()
            ],
        }
        exposure["core_count"] = len(exposure["core_tool_names"])
        exposure["preloaded_count"] = len(preloaded_tools)
        exposure["persisted_dynamic_count"] = len(persisted_tools)
        return exposed_tools, exposure

    def _tool_names(self, tools: list[dict[str, Any]]) -> set[str]:
        return {
            str(tool.get("function", {}).get("name") or "").strip()
            for tool in tools
            if str(tool.get("function", {}).get("name") or "").strip()
        }

    def _legacy_filtered_tools(
        self,
        tools: list[dict[str, Any]],
        *,
        inbound: Message | None,
        candidate_skills: list[SkillManifest] | None = None,
    ) -> list[dict[str, Any]]:
        if not tools:
            return []

        allowed_tool_names: set[str] = set()
        if self._is_heartbeat_request(inbound) and self.heartbeat_allowed_tools:
            allowed_tool_names.update(self.heartbeat_allowed_tools)
        else:
            tool_names = self._tool_names(tools)
            if candidate_skills or (tool_names & _CORE_TOOL_WHITELIST):
                allowed_tool_names.update(_CORE_TOOL_WHITELIST)
                for manifest in candidate_skills or []:
                    allowed_tool_names.update(manifest.allowed_tools)
        if not allowed_tool_names:
            return list(tools)
        filtered = [
            tool
            for tool in tools
            if str(tool.get("function", {}).get("name") or "").strip() in allowed_tool_names
        ]
        return filtered or list(tools)

    def _first_unexposed_tool_call(
        self,
        tool_calls: list[dict[str, Any]],
        *,
        exposed_tool_names: set[str],
        inbound: Message | None,
    ) -> dict[str, Any] | None:
        for tool_call in tool_calls:
            tool_name = self._extract_tool_name(tool_call)
            if not tool_name:
                continue
            if tool_name in exposed_tool_names:
                continue
            if self._is_heartbeat_request(inbound) and self.heartbeat_allowed_tools:
                if tool_name not in self.heartbeat_allowed_tools:
                    return tool_call
            return tool_call
        return None

    def _tool_pruning_snapshot(
        self,
        tools: list[dict[str, Any]],
        filtered_tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        original_names = [
            str(tool.get("function", {}).get("name") or "").strip()
            for tool in tools
            if str(tool.get("function", {}).get("name") or "").strip()
        ]
        filtered_names = [
            str(tool.get("function", {}).get("name") or "").strip()
            for tool in filtered_tools
            if str(tool.get("function", {}).get("name") or "").strip()
        ]
        dropped_names = [name for name in original_names if name not in set(filtered_names)]
        return {
            "tool_names": filtered_names,
            "tool_count": len(filtered_names),
            "dropped_tools": dropped_names,
            "dropped_count": len(dropped_names),
        }

    def _history_message_should_skip(self, message: Message) -> bool:
        tag = str(message.message_tag or "").strip().lower()
        channel = self._normalized_channel_name(message.channel)
        return tag in _HISTORY_SKIP_MESSAGE_TAGS or channel == "system"

    def _estimate_message_tokens(self, message: dict[str, Any]) -> int:
        role = str(message.get("role") or "")
        content = message.get("content")
        if isinstance(content, str):
            payload = content
        else:
            payload = json.dumps(content, ensure_ascii=False, default=str)
        return estimate_token_count(role) + estimate_token_count(payload) + 8

    def _estimate_prompt_tokens(self, messages: list[dict[str, Any]]) -> int:
        return sum(self._estimate_message_tokens(message) for message in messages)

    def _select_history_messages(self, history: list[Message]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        budget = self.history_token_budget
        selected: list[dict[str, Any]] = []
        used_tokens = 0
        skipped_system = 0
        dropped_budget = 0

        for item in reversed(history):
            if self._history_message_should_skip(item):
                skipped_system += 1
                continue
            llm_item = self._to_llm_message(item)
            if llm_item is None:
                continue
            message_tokens = self._estimate_message_tokens(llm_item)
            if selected and budget > 0 and used_tokens + message_tokens > budget:
                dropped_budget += 1
                continue
            if budget > 0 and used_tokens + message_tokens > budget and not selected:
                dropped_budget += 1
                continue
            selected.append(llm_item)
            used_tokens += message_tokens

        selected.reverse()
        return selected, {
            "history_messages_seen": len(history),
            "history_messages_selected": len(selected),
            "history_messages_skipped_system": skipped_system,
            "history_messages_dropped_budget": dropped_budget,
            "history_tokens": used_tokens,
            "history_token_budget": budget,
        }

    def _budget_warning_round(self, max_react_rounds: int) -> int:
        if max_react_rounds <= 0:
            return 0
        return max(1, int(max_react_rounds * 0.7))

    def _apply_budget_pressure(
        self,
        react_messages: list[dict[str, Any]],
        *,
        round_num: int,
        max_react_rounds: int,
    ) -> list[dict[str, Any]]:
        warning_round = self._budget_warning_round(max_react_rounds)
        if round_num < warning_round:
            return react_messages
        budget_left = max(0, max_react_rounds - round_num + 1)
        pressured = list(react_messages)
        pressured.append(
            {
                "role": "system",
                "content": (
                    f"Budget warning: you are in round {round_num}/{max_react_rounds}. "
                    f"At most {budget_left} tool round(s) remain. "
                    "Only call another tool if it is strictly necessary; otherwise finalize."
                ),
            }
        )
        return pressured

    def _resolve_router_timeout(
        self,
        *,
        inbound: Message | None,
        react_deadline: float | None = None,
    ) -> float | None:
        timeout_candidates = [
            value
            for value in (
                self._remaining_react_timeout(react_deadline),
                self._heartbeat_timeout_for(inbound),
            )
            if value is not None
        ]
        if not timeout_candidates:
            return None
        return min(timeout_candidates)

    def _method_supports_kwarg(self, method: Any, name: str) -> bool:
        try:
            return name in inspect.signature(method).parameters
        except (TypeError, ValueError):
            return False

    async def _router_call(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        inbound: Message | None,
        react_deadline: float | None = None,
        task_type: str | None = None,
        event_emitter: Any | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {}
        timeout_seconds = self._resolve_router_timeout(
            inbound=inbound,
            react_deadline=react_deadline,
        )
        if timeout_seconds is not None and self._method_supports_kwarg(self.router.call, "timeout_seconds"):
            kwargs["timeout_seconds"] = timeout_seconds
        if task_type is not None and self._method_supports_kwarg(self.router.call, "task_type"):
            kwargs["task_type"] = task_type
        resolved_event_emitter = self._resolve_event_emitter(event_emitter)
        if resolved_event_emitter is not None and self._method_supports_kwarg(self.router.call, "event_emitter"):
            kwargs["event_emitter"] = resolved_event_emitter
        return await self.router.call(model_name, messages, **kwargs)

    async def _router_call_with_tools(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        inbound: Message | None,
        tools: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
        react_deadline: float | None = None,
        task_type: str | None = None,
        event_emitter: Any | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self._method_supports_kwarg(self.router.call_with_tools, "tools"):
            kwargs["tools"] = tools
        if self._method_supports_kwarg(self.router.call_with_tools, "session_id"):
            kwargs["session_id"] = session_id
        timeout_seconds = self._resolve_router_timeout(
            inbound=inbound,
            react_deadline=react_deadline,
        )
        if timeout_seconds is not None and self._method_supports_kwarg(self.router.call_with_tools, "timeout_seconds"):
            kwargs["timeout_seconds"] = timeout_seconds
        if task_type is not None and self._method_supports_kwarg(self.router.call_with_tools, "task_type"):
            kwargs["task_type"] = task_type
        resolved_event_emitter = self._resolve_event_emitter(event_emitter)
        if (
            resolved_event_emitter is not None
            and self._method_supports_kwarg(self.router.call_with_tools, "event_emitter")
        ):
            kwargs["event_emitter"] = resolved_event_emitter
        return await self.router.call_with_tools(model_name, messages, **kwargs)

    def _assistant_message_from_decision(
        self,
        decision: dict[str, Any],
        *,
        tool_calls: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raw_assistant_message = decision.get("assistant_message")
        if isinstance(raw_assistant_message, dict):
            assistant_message = dict(raw_assistant_message)
            assistant_message["role"] = "assistant"
            assistant_message["content"] = str(assistant_message.get("content") or "")
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            return assistant_message

        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": str(decision.get("text") or ""),
        }
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        reasoning_content = decision.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content:
            assistant_message["reasoning_content"] = reasoning_content
        return assistant_message

    async def _router_stream(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        inbound: Message | None,
        tools: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
        react_deadline: float | None = None,
        task_type: str | None = None,
        event_emitter: Any | None = None,
    ) -> AsyncIterator[str]:
        kwargs: dict[str, Any] = {}
        if self._method_supports_kwarg(self.router.stream, "tools"):
            kwargs["tools"] = tools
        if self._method_supports_kwarg(self.router.stream, "session_id"):
            kwargs["session_id"] = session_id
        timeout_seconds = self._resolve_router_timeout(
            inbound=inbound,
            react_deadline=react_deadline,
        )
        if timeout_seconds is not None and self._method_supports_kwarg(self.router.stream, "timeout_seconds"):
            kwargs["timeout_seconds"] = timeout_seconds
        if task_type is not None and self._method_supports_kwarg(self.router.stream, "task_type"):
            kwargs["task_type"] = task_type
        resolved_event_emitter = self._resolve_event_emitter(event_emitter)
        if resolved_event_emitter is not None and self._method_supports_kwarg(self.router.stream, "event_emitter"):
            kwargs["event_emitter"] = resolved_event_emitter
        async for chunk in self.router.stream(model_name, messages, **kwargs):
            yield chunk

    async def _generate_round_limit_summary(
        self,
        react_messages: list[dict[str, Any]],
        *,
        model_name: str,
        session_id: str,
        max_react_rounds: int,
        inbound: Message | None = None,
        heartbeat_mode: bool = False,
        react_deadline: float | None = None,
        task_type: str | None = None,
    ) -> str:
        if heartbeat_mode:
            return self._build_heartbeat_round_limit_summary(
                react_messages,
                max_react_rounds=max_react_rounds,
            )

        final_messages = list(react_messages)
        final_messages.append(
            {
                "role": "system",
                "content": (
                    "You have reached the tool-use round limit. Do not call any more tools. "
                    "Provide the best possible final answer based only on the information already gathered. "
                    "If anything remains uncertain, state it briefly."
                ),
            }
        )

        try:
            decision_kwargs: dict[str, Any] = {}
            if self._method_supports_kwarg(self.router.call_with_tools, "tools"):
                decision_kwargs["tools"] = None
            if self._method_supports_kwarg(self.router.call_with_tools, "session_id"):
                decision_kwargs["session_id"] = session_id
            timeout_seconds = self._resolve_router_timeout(
                inbound=inbound,
                react_deadline=react_deadline,
            )
            if timeout_seconds is not None and self._method_supports_kwarg(
                self.router.call_with_tools,
                "timeout_seconds",
            ):
                decision_kwargs["timeout_seconds"] = timeout_seconds
            if task_type is not None and self._method_supports_kwarg(
                self.router.call_with_tools,
                "task_type",
            ):
                decision_kwargs["task_type"] = task_type
            decision = await self.router.call_with_tools(
                model_name,
                final_messages,
                **decision_kwargs,
            )
            text = str(decision.get("text") or "").strip()
            if text:
                return text
            if not (decision.get("tool_calls") or []):
                streamed = ""
                stream_kwargs: dict[str, Any] = {}
                if self._method_supports_kwarg(self.router.stream, "session_id"):
                    stream_kwargs["session_id"] = session_id
                if self._method_supports_kwarg(self.router.stream, "tools"):
                    stream_kwargs["tools"] = None
                if timeout_seconds is not None and self._method_supports_kwarg(
                    self.router.stream,
                    "timeout_seconds",
                ):
                    stream_kwargs["timeout_seconds"] = timeout_seconds
                if task_type is not None and self._method_supports_kwarg(
                    self.router.stream,
                    "task_type",
                ):
                    stream_kwargs["task_type"] = task_type
                async for chunk in self.router.stream(
                    model_name,
                    final_messages,
                    **stream_kwargs,
                ):
                    if not chunk:
                        continue
                    streamed += chunk
                if streamed.strip():
                    return streamed.strip()
        except _PIPELINE_RECOVERABLE_ERRORS:
            logger.exception(
                "react.round_limit.degrading_failed",
                session_id=session_id,
                max_rounds=max_react_rounds,
            )

        return "Based on the information gathered so far, here is the best-effort summary."

    def _build_heartbeat_round_limit_summary(
        self,
        react_messages: list[dict[str, Any]],
        *,
        max_react_rounds: int,
    ) -> str:
        tool_names: dict[str, str] = {}
        summaries: list[str] = []
        last_assistant_text = ""

        for message in react_messages:
            role = str(message.get("role") or "").strip().lower()
            if role == "assistant":
                text = str(message.get("content") or "").strip()
                if text:
                    last_assistant_text = text
                for tool_call in message.get("tool_calls") or []:
                    tool_call_id = str(tool_call.get("id") or "").strip()
                    tool_name = self._extract_tool_name(tool_call)
                    if tool_call_id and tool_name:
                        tool_names[tool_call_id] = tool_name
                continue
            if role != "tool":
                continue
            tool_call_id = str(message.get("tool_call_id") or "").strip()
            tool_name = tool_names.get(tool_call_id) or "tool"
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            summaries.append(f"- {tool_name}: {content}")

        lines = [
            f"Heartbeat 已达到工具轮次上限（{max_react_rounds} 轮），以下是基于已收集信息的当前总结：",
        ]
        if last_assistant_text:
            lines.append(last_assistant_text)
        if summaries:
            lines.extend(summaries[-6:])
        else:
            lines.append("当前没有拿到足够的工具结果，请下一次心跳继续补充检查。")
        return "\n".join(lines)

    def _append_session_message(self, message: Message) -> None:
        if self._session_persistence_suppressed():
            return
        self.session_memory.append(message)

    async def run_once(self, inbound: Message) -> Message:
        slash_result = await self._try_handle_slash(inbound)
        if slash_result is not None:
            outbound = Message(
                text=slash_result,
                sender="assistant",
                session_id=inbound.session_id,
                channel=inbound.channel,
                sender_id=inbound.sender_id,
            )
            await self._broadcast_message(
                outbound,
                origin_channel=inbound.channel,
                origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
            )
            return outbound

        pre_llm_message = await self._try_handle_pre_llm_message(inbound)
        if pre_llm_message is not None:
            self._append_session_message(inbound)
            self._append_session_message(pre_llm_message)
            await self._broadcast_message(
                pre_llm_message,
                origin_channel=inbound.channel,
                origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
            )
            return pre_llm_message

        pre_llm_text = await self._try_handle_pre_llm_shortcuts(inbound)
        if pre_llm_text is not None:
            self._append_session_message(inbound)
            outbound = Message(
                text=pre_llm_text,
                sender="assistant",
                session_id=inbound.session_id,
                channel=inbound.channel,
                sender_id=inbound.sender_id,
            )
            self._append_session_message(outbound)
            await self._broadcast_message(
                outbound,
                origin_channel=inbound.channel,
                origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
            )
            return outbound

        if self._kill_switch_active():
            self._append_session_message(inbound)
            outbound = Message(
                text=KILL_SWITCH_MESSAGE,
                sender="assistant",
                session_id=inbound.session_id,
                channel=inbound.channel,
                sender_id=inbound.sender_id,
            )
            self._append_session_message(outbound)
            await self._broadcast_message(
                outbound,
                origin_channel=inbound.channel,
                origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
            )
            return outbound

        candidate_skills = self._match_skill_candidates(inbound)
        task_type = self._resolve_task_type_for_inbound(
            inbound,
            use_tools=False,
            candidate_skills=candidate_skills,
        )
        model_name = self._resolve_model_for_inbound(
            inbound,
            use_tools=False,
            candidate_skills=candidate_skills,
        )
        llm_messages = await self._build_llm_messages(
            inbound,
            candidate_skills=candidate_skills,
            model_name=model_name,
            task_type=task_type,
        )
        self._append_session_message(inbound)
        text = await self._router_call(
            model_name,
            llm_messages,
            inbound=inbound,
            task_type=task_type,
            event_emitter=self.event_emitter,
        )
        text = await self._append_codex_status_bar(text, session_id=inbound.session_id)
        outbound = Message(
            text=text,
            sender="assistant",
            session_id=inbound.session_id,
            channel=inbound.channel,
            sender_id=inbound.sender_id,
        )
        self._append_session_message(outbound)
        await self._mark_retrieved_sops_used()
        await self._broadcast_message(
            outbound,
            origin_channel=inbound.channel,
            origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
        )
        return outbound

    async def stream_reply(
        self,
        inbound: Message,
        *,
        event_emitter: Any | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        narration_tasks: set[asyncio.Task[None]] = set()
        try:
            collected_attachments: list[Attachment] = []
            slash_result = await self._try_handle_slash(inbound)
            if slash_result is not None:
                raw_slash_text = str(inbound.text or "").strip().lower()
                persist_slash = raw_slash_text == "/codex" or raw_slash_text.startswith("/codex ")
                if persist_slash:
                    self._append_session_message(inbound)
                yield await self._format_event(
                    event_type="assistant_chunk",
                    response=RichResponse(text=slash_result),
                    session_id=inbound.session_id,
                )
                outbound = Message(
                    text=slash_result,
                    sender="assistant",
                    session_id=inbound.session_id,
                    channel=inbound.channel,
                    sender_id=inbound.sender_id,
                )
                if persist_slash:
                    self._append_session_message(outbound)
                await self._broadcast_message(
                    outbound,
                    origin_channel=inbound.channel,
                    origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
                )
                yield await self._format_event(
                    event_type="assistant_done",
                    response=RichResponse(),
                    session_id=inbound.session_id,
                )
                return

            pre_llm_message = await self._try_handle_pre_llm_message(inbound)
            if pre_llm_message is not None:
                self._append_session_message(inbound)
                yield await self._format_event(
                    event_type="assistant_chunk",
                    response=RichResponse(text=pre_llm_message.text or ""),
                    session_id=inbound.session_id,
                )
                self._append_session_message(pre_llm_message)
                await self._broadcast_message(
                    pre_llm_message,
                    origin_channel=inbound.channel,
                    origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
                )
                yield await self._format_event(
                    event_type="assistant_done",
                    response=RichResponse(
                        attachments=[attachment.model_copy() for attachment in pre_llm_message.attachments],
                    ),
                    session_id=inbound.session_id,
                )
                return

            pre_llm_text = await self._try_handle_pre_llm_shortcuts(inbound)
            if pre_llm_text is not None:
                self._append_session_message(inbound)
                yield await self._format_event(
                    event_type="assistant_chunk",
                    response=RichResponse(text=pre_llm_text),
                    session_id=inbound.session_id,
                )
                outbound = Message(
                    text=pre_llm_text,
                    sender="assistant",
                    session_id=inbound.session_id,
                    channel=inbound.channel,
                    sender_id=inbound.sender_id,
                )
                self._append_session_message(outbound)
                await self._broadcast_message(
                    outbound,
                    origin_channel=inbound.channel,
                    origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
                )
                yield await self._format_event(
                    event_type="assistant_done",
                    response=RichResponse(),
                    session_id=inbound.session_id,
                )
                return

            if self._kill_switch_active():
                self._append_session_message(inbound)
                kill_text = KILL_SWITCH_MESSAGE
                yield await self._format_event(
                    event_type="assistant_chunk",
                    response=RichResponse(text=kill_text),
                    session_id=inbound.session_id,
                )
                outbound = Message(
                    text=kill_text,
                    sender="assistant",
                    session_id=inbound.session_id,
                    channel=inbound.channel,
                    sender_id=inbound.sender_id,
                )
                self._append_session_message(outbound)
                await self._broadcast_message(
                    outbound,
                    origin_channel=inbound.channel,
                    origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
                )
                yield await self._format_event(
                    event_type="assistant_done",
                    response=RichResponse(),
                    session_id=inbound.session_id,
                )
                return

            await self._emit_progress_event(
                {
                    "type": "pipeline_stage",
                    "stage": "preprocessing",
                    "detail": "正在分析你的消息...",
                    "session_id": inbound.session_id,
                },
                event_emitter=event_emitter,
            )
            max_react_rounds = self._effective_max_react_rounds(inbound)
            use_tools = self.skill_manager is not None and max_react_rounds > 0
            candidate_skills = self._match_skill_candidates(inbound)
            await self._emit_progress_event(
                {
                    "type": "pipeline_stage",
                    "stage": "memory_injection",
                    "detail": "正在检索相关记忆...",
                    "session_id": inbound.session_id,
                },
                event_emitter=event_emitter,
            )
            task_type = self._resolve_task_type_for_inbound(
                inbound,
                use_tools=use_tools,
                candidate_skills=candidate_skills,
            )
            model_name = self._resolve_model_for_inbound(
                inbound,
                use_tools=use_tools,
                candidate_skills=candidate_skills,
            )
            llm_messages = await self._build_llm_messages(
                inbound,
                use_tools=use_tools,
                candidate_skills=candidate_skills,
                model_name=model_name,
                task_type=task_type,
            )
            await self._emit_progress_event(
                {
                    "type": "pipeline_stage",
                    "stage": "model_routing",
                    "detail": f"选择模型: {model_name}",
                    "model": model_name,
                    "task_type": task_type,
                    "session_id": inbound.session_id,
                },
                event_emitter=event_emitter,
            )
            prompt_tokens = self._estimate_prompt_tokens(llm_messages)
            logger.info(
                "pipeline.model_selected",
                session_id=inbound.session_id,
                model_name=model_name,
                use_tools=use_tools,
                prompt_tokens=prompt_tokens,
                candidate_skills=[manifest.name for manifest in candidate_skills],
            )
            self._append_session_message(inbound)

            full_text = ""
            killed = False
            session_fused = False
            last_tool_fallback_text: str | None = None
            react_iterations_completed = 0
            react_tool_calls_total = 0

            if use_tools:
                assert self.skill_manager is not None
                all_tools = self._skill_manager_get_tools_schema()
                progressive_disclosure_enabled = self._supports_progressive_tool_disclosure()
                preloaded_skill_names: set[str] = set()
                if progressive_disclosure_enabled:
                    preloaded_skill_names = self._match_preloaded_skill_names(
                        inbound,
                        candidate_skills=candidate_skills,
                    )
                    tools, exposure_snapshot = self._build_exposed_tools(
                        inbound=inbound,
                        session_id=inbound.session_id,
                        preloaded_skill_names=preloaded_skill_names,
                    )
                else:
                    tools = self._legacy_filtered_tools(
                        all_tools,
                        inbound=inbound,
                        candidate_skills=candidate_skills,
                    )
                    exposure_snapshot = {
                        "core_count": len(self._tool_names(tools)),
                        "preloaded_count": 0,
                        "persisted_dynamic_count": 0,
                    }
                tool_snapshot = self._tool_pruning_snapshot(all_tools, tools)
                tool_names = self._tool_names(tools)
                react_messages: list[dict[str, Any]] = list(llm_messages)
                reached_round_limit = True
                react_deadline = None
                react_timeout_seconds = self._react_timeout_for(inbound)
                if react_timeout_seconds is not None:
                    react_deadline = perf_counter() + react_timeout_seconds
                logger.info(
                    "react.start",
                    session_id=inbound.session_id,
                    max_rounds=max_react_rounds,
                    react_timeout_seconds=react_timeout_seconds,
                )
                logger.debug(
                    "react.tools",
                    session_id=inbound.session_id,
                    **tool_snapshot,
                )
                logger.info(
                    "tool_exposure.snapshot",
                    session_id=inbound.session_id,
                    core_count=exposure_snapshot["core_count"],
                    preloaded_count=exposure_snapshot["preloaded_count"],
                    dynamic_count=exposure_snapshot["persisted_dynamic_count"],
                )
                if preloaded_skill_names:
                    self._remember_loaded_skills(inbound.session_id, preloaded_skill_names)
                    logger.info(
                        "tool_exposure.preloaded",
                        session_id=inbound.session_id,
                        skill_names=sorted(preloaded_skill_names),
                    )

                for round_num in range(1, max_react_rounds + 1):
                    if self._kill_switch_active():
                        full_text = KILL_SWITCH_MESSAGE
                        yield await self._format_event(
                            event_type="assistant_chunk",
                            response=RichResponse(text=full_text),
                            session_id=inbound.session_id,
                        )
                        killed = True
                        break
                    react_iterations_completed = round_num
                    await self._emit_progress_event(
                        {
                            "type": "react_iteration",
                            "iteration": round_num,
                            "max_iterations": max_react_rounds,
                            "status": "继续推理...",
                            "session_id": inbound.session_id,
                        },
                        event_emitter=event_emitter,
                    )
                    round_messages = self._apply_budget_pressure(
                        react_messages,
                        round_num=round_num,
                        max_react_rounds=max_react_rounds,
                    )
                    tool_not_found_handled = False
                    while True:
                        decision = await self._router_call_with_tools(
                            model_name,
                            round_messages,
                            inbound=inbound,
                            tools=tools,
                            session_id=inbound.session_id,
                            react_deadline=react_deadline,
                            task_type=task_type,
                            event_emitter=event_emitter,
                        )
                        tool_calls = decision.get("tool_calls") or []
                        missing_tool_call = (
                            self._first_unexposed_tool_call(
                                tool_calls,
                                exposed_tool_names=tool_names,
                                inbound=inbound,
                            )
                            if progressive_disclosure_enabled
                            else None
                        )
                        if missing_tool_call is None:
                            break

                        missing_tool_name = self._extract_tool_name(missing_tool_call)
                        owning_skill = self._skill_manager_find_skill_by_tool_name(missing_tool_name)
                        if (
                            owning_skill is not None
                            and self._is_heartbeat_request(inbound)
                            and self.heartbeat_allowed_tools
                            and missing_tool_name not in self.heartbeat_allowed_tools
                        ):
                            owning_skill = None
                        if owning_skill is not None:
                            self._remember_loaded_skills(inbound.session_id, {owning_skill.name})
                            logger.info(
                                "skill.dynamic_load",
                                session_id=inbound.session_id,
                                skill_name=owning_skill.name,
                                tool_name=missing_tool_name,
                            )
                            logger.info(
                                "tool_exposure.dynamic",
                                session_id=inbound.session_id,
                                skill_name=owning_skill.name,
                                tool_name=missing_tool_name,
                            )
                            tools, exposure_snapshot = self._build_exposed_tools(
                                inbound=inbound,
                                session_id=inbound.session_id,
                            )
                            tool_snapshot = self._tool_pruning_snapshot(all_tools, tools)
                            tool_names = self._tool_names(tools)
                            logger.debug(
                                "react.tools",
                                session_id=inbound.session_id,
                                **tool_snapshot,
                            )
                            logger.info(
                                "tool_exposure.snapshot",
                                session_id=inbound.session_id,
                                core_count=exposure_snapshot["core_count"],
                                preloaded_count=exposure_snapshot["preloaded_count"],
                                dynamic_count=exposure_snapshot["persisted_dynamic_count"],
                            )
                            continue

                        tool_calls = [missing_tool_call]
                        react_messages.append(
                            self._assistant_message_from_decision(
                                decision,
                                tool_calls=tool_calls,
                            )
                        )
                        tool_name = missing_tool_name
                        tool_call_id = str(missing_tool_call.get("id") or "")
                        arguments = self._parse_tool_arguments(missing_tool_call)
                        output = self._tool_not_found_output(tool_name)
                        await self._send_tool_status(
                            tool_name=tool_name,
                            status="fail",
                            session_id=inbound.session_id,
                            error=output.error_info or "",
                            event_emitter=event_emitter,
                            origin_channel=inbound.channel,
                            origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
                        )
                        yield await self._format_event(
                            event_type="tool_call_start",
                            response=RichResponse(
                                tool_calls=[
                                    {
                                        "tool_name": tool_name,
                                        "tool_call_id": tool_call_id,
                                        "arguments": arguments,
                                        "iteration": round_num,
                                    }
                                ]
                            ),
                            session_id=inbound.session_id,
                        )
                        yield await self._format_event(
                            event_type="tool_call_result",
                            response=RichResponse(
                                tool_calls=[
                                    {
                                        "tool_name": tool_name,
                                        "tool_call_id": tool_call_id,
                                        "status": output.status,
                                        "result": output.result,
                                        "error_info": output.error_info,
                                        "metadata": {"ephemeral": True},
                                        "summary": self._tool_fallback_text(output),
                                        "duration_ms": 0,
                                        "iteration": round_num,
                                    }
                                ],
                            ),
                            session_id=inbound.session_id,
                        )
                        react_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": json.dumps(output.model_dump(mode="json"), ensure_ascii=False),
                            }
                        )
                        tool_not_found_handled = True
                        tool_calls = []
                        break

                    react_tool_calls_total += len(tool_calls)
                    logger.info(
                        "react.round",
                        round=round_num,
                        tool_calls=len(tool_calls),
                    )
                    if tool_not_found_handled:
                        continue
                    if not tool_calls:
                        reached_round_limit = False
                        text = str(decision.get("text") or "")
                        if text:
                            text = self._sanitize_false_access_denied_reply(
                                text=text,
                                fallback_text=last_tool_fallback_text,
                                session_id=inbound.session_id,
                            )
                            full_text = text
                            yield await self._format_event(
                                event_type="assistant_chunk",
                                response=RichResponse(text=text),
                                session_id=inbound.session_id,
                            )
                        else:
                            stream_chunks: list[str] | None = (
                                []
                                if str(last_tool_fallback_text or "").strip()
                                else None
                            )
                            async for chunk in self._router_stream(
                                model_name,
                                react_messages,
                                inbound=inbound,
                                session_id=inbound.session_id,
                                tools=tools,
                                react_deadline=react_deadline,
                                task_type=task_type,
                                event_emitter=event_emitter,
                            ):
                                if self._kill_switch_active():
                                    full_text = KILL_SWITCH_MESSAGE
                                    yield await self._format_event(
                                        event_type="assistant_chunk",
                                        response=RichResponse(text=full_text),
                                        session_id=inbound.session_id,
                                    )
                                    killed = True
                                    break
                                if not chunk:
                                    continue
                                if stream_chunks is not None:
                                    stream_chunks.append(chunk)
                                    continue
                                full_text += chunk
                                yield await self._format_event(
                                    event_type="assistant_chunk",
                                    response=RichResponse(text=chunk),
                                    session_id=inbound.session_id,
                                )
                            if stream_chunks is not None and not killed:
                                streamed_text = "".join(stream_chunks)
                                full_text = self._sanitize_false_access_denied_reply(
                                    text=streamed_text,
                                    fallback_text=last_tool_fallback_text,
                                    session_id=inbound.session_id,
                                )
                                if full_text:
                                    yield await self._format_event(
                                        event_type="assistant_chunk",
                                        response=RichResponse(text=full_text),
                                        session_id=inbound.session_id,
                                    )
                            if killed:
                                break
                        break

                    react_messages.append(
                        self._assistant_message_from_decision(
                            decision,
                            tool_calls=tool_calls,
                        )
                    )
                    for tool_index, tool_call in enumerate(tool_calls, start=1):
                        tool_name = self._extract_tool_name(tool_call)
                        tool_call_id = str(tool_call.get("id") or "")
                        active_skill = self._select_active_skill_manifest(candidate_skills, tool_name)
                        arguments = self._augment_tool_arguments(
                            tool_name,
                            self._parse_tool_arguments(tool_call),
                            skill_manifest=active_skill,
                        )
                        narration_task = self._schedule_narration_task(
                            inbound=inbound,
                            tool_name=tool_name,
                            arguments=arguments,
                            iteration_number=round_num,
                            total_tools_called=react_tool_calls_total - len(tool_calls) + tool_index,
                        )
                        if narration_task is not None:
                            narration_tasks.add(narration_task)
                            narration_task.add_done_callback(narration_tasks.discard)
                        self._record_narration_trace_event(
                            session_id=inbound.session_id,
                            event_type="tool_call_start",
                            tool_name=tool_name,
                            summary="开始处理",
                            elapsed_ms=0,
                        )
                        await self._send_tool_status(
                            tool_name=tool_name,
                            status="start",
                            session_id=inbound.session_id,
                            event_emitter=event_emitter,
                            origin_channel=inbound.channel,
                            origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
                        )
                        yield await self._format_event(
                            event_type="tool_call_start",
                            response=RichResponse(
                                tool_calls=[
                                    {
                                        "tool_name": tool_name,
                                        "tool_call_id": tool_call_id,
                                        "arguments": arguments,
                                        "iteration": round_num,
                                    }
                                ]
                            ),
                            session_id=inbound.session_id,
                        )

                        started_at = perf_counter()
                        try:
                            output = await self._invoke_tool_with_retry(
                                tool_name=tool_name,
                                arguments=arguments,
                                session_id=inbound.session_id,
                                skill_name=self._resolve_tool_skill_name(candidate_skills, tool_name),
                                event_emitter=event_emitter,
                                iteration=round_num,
                            )
                        except HypoAgentError as exc:
                            await self._send_tool_status(
                                tool_name=tool_name,
                                status="fail",
                                session_id=inbound.session_id,
                                error=str(exc),
                                event_emitter=event_emitter,
                                origin_channel=inbound.channel,
                                origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
                            )
                            await self._emit_progress_event(
                                {
                                    "type": "tool_call_error",
                                    "tool": tool_name,
                                    "error": str(exc),
                                    "will_retry": False,
                                    "iteration": round_num,
                                    "session_id": inbound.session_id,
                                },
                                event_emitter=event_emitter,
                            )
                            self._record_narration_trace_event(
                                session_id=inbound.session_id,
                                event_type="tool_call_error",
                                tool_name=tool_name,
                                summary=str(exc),
                                elapsed_ms=int(max(0.0, perf_counter() - started_at) * 1000),
                            )
                            raise
                        finally:
                            self._cancel_fast_qq_narration(
                                task=narration_task,
                                inbound=inbound,
                                started_at=started_at,
                            )

                        duration_ms = int(max(0.0, perf_counter() - started_at) * 1000)

                        if output.status == "success":
                            self._track_sop_usage_from_tool_output(tool_name, output)
                            await self._send_tool_status(
                                tool_name=tool_name,
                                status="ok",
                                session_id=inbound.session_id,
                                event_emitter=event_emitter,
                                origin_channel=inbound.channel,
                                origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
                            )
                        else:
                            await self._send_tool_status(
                                tool_name=tool_name,
                                status="fail",
                                session_id=inbound.session_id,
                                error=output.error_info,
                                event_emitter=event_emitter,
                                origin_channel=inbound.channel,
                                origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
                            )
                            await self._emit_progress_event(
                                {
                                    "type": "tool_call_error",
                                    "tool": tool_name,
                                    "error": str(output.error_info or output.status),
                                    "will_retry": False,
                                    "iteration": round_num,
                                    "session_id": inbound.session_id,
                                },
                                event_emitter=event_emitter,
                            )
                            self._record_narration_trace_event(
                                session_id=inbound.session_id,
                                event_type="tool_call_error",
                                tool_name=tool_name,
                                summary=str(output.error_info or output.status),
                                elapsed_ms=duration_ms,
                            )
                        serialized_output = json.dumps(output.model_dump(mode="json"), ensure_ascii=False)
                        tool_summary = self._tool_fallback_text(output)
                        last_tool_fallback_text = tool_summary or last_tool_fallback_text
                        if output.status == "success":
                            self._record_narration_trace_event(
                                session_id=inbound.session_id,
                                event_type="tool_call_result",
                                tool_name=tool_name,
                                summary=tool_summary or output.status,
                                elapsed_ms=duration_ms,
                            )
                        tool_content = self._tool_content_for_react(
                            output,
                            serialized_output=serialized_output,
                            inbound=inbound,
                        )
                        tool_result_for_event: Any = output.result
                        tool_metadata_for_event = dict(output.metadata)
                        tool_metadata_for_event["ephemeral"] = True
                        tool_attachments_for_event = [
                            attachment.model_copy()
                            for attachment in output.attachments
                        ]
                        original_tool_content = tool_content
                        if self._should_export_long_output(original_tool_content):
                            exported_attachment = self._export_long_tool_output_attachment(
                                content=original_tool_content,
                                tool_name=tool_name,
                                tool_call_id=tool_call_id,
                                session_id=inbound.session_id,
                            )
                            export_notice = self._externalized_tool_output_notice(
                                attachment=exported_attachment,
                                tool_name=tool_name,
                            )
                            tool_content = export_notice
                            tool_result_for_event = export_notice
                            tool_summary = export_notice
                            last_tool_fallback_text = export_notice
                            tool_metadata_for_event["externalized"] = True
                            tool_metadata_for_event["original_chars"] = len(original_tool_content)
                            tool_metadata_for_event["externalized_filename"] = exported_attachment.filename
                            tool_attachments_for_event.append(exported_attachment)
                        if tool_attachments_for_event:
                            collected_attachments.extend(tool_attachments_for_event)
                        yield await self._format_event(
                            event_type="tool_call_result",
                            response=RichResponse(
                                attachments=tool_attachments_for_event,
                                tool_calls=[
                                    {
                                        "tool_name": tool_name,
                                        "tool_call_id": tool_call_id,
                                        "status": output.status,
                                        "result": tool_result_for_event,
                                        "error_info": output.error_info,
                                        "metadata": tool_metadata_for_event,
                                        "summary": tool_summary,
                                        "duration_ms": duration_ms,
                                        "iteration": round_num,
                                    }
                                ],
                            ),
                            session_id=inbound.session_id,
                        )
                        react_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": tool_content,
                            }
                        )
                        if (
                            output.status == "fused"
                            and "session circuit breaker" in output.error_info.lower()
                        ):
                            session_fused = True
                            full_text = SESSION_FUSED_MESSAGE
                            yield await self._format_event(
                                event_type="assistant_chunk",
                                response=RichResponse(text=full_text),
                                session_id=inbound.session_id,
                            )
                            break
                    if session_fused:
                        break
                    if self._should_force_final_response(
                        round_num,
                        max_react_rounds=max_react_rounds,
                        inbound=inbound,
                    ):
                        reached_round_limit = False
                        logger.info(
                            "react.round_limit.degrading",
                            session_id=inbound.session_id,
                            round=round_num,
                            max_rounds=max_react_rounds,
                        )
                        full_text = await self._generate_round_limit_summary(
                            react_messages,
                            model_name=model_name,
                            session_id=inbound.session_id,
                            max_react_rounds=max_react_rounds,
                            inbound=inbound,
                            heartbeat_mode=self._is_heartbeat_request(inbound),
                            react_deadline=react_deadline,
                            task_type=task_type,
                        )
                        yield await self._format_event(
                            event_type="assistant_chunk",
                            response=RichResponse(text=full_text),
                            session_id=inbound.session_id,
                        )
                        break

                if reached_round_limit:
                    logger.warning("react.round_limit", session_id=inbound.session_id)
                    full_text = "Stopped due to max ReAct rounds limit."
                    yield await self._format_event(
                        event_type="assistant_chunk",
                        response=RichResponse(text=full_text),
                        session_id=inbound.session_id,
                    )
                await self._emit_progress_event(
                    {
                        "type": "react_complete",
                        "total_iterations": react_iterations_completed,
                        "total_tool_calls": react_tool_calls_total,
                        "session_id": inbound.session_id,
                    },
                    event_emitter=event_emitter,
                )
            else:
                async for chunk in self._router_stream(
                    model_name,
                    llm_messages,
                    inbound=inbound,
                    session_id=inbound.session_id,
                    task_type=task_type,
                    event_emitter=event_emitter,
                ):
                    if self._kill_switch_active():
                        full_text = KILL_SWITCH_MESSAGE
                        yield await self._format_event(
                            event_type="assistant_chunk",
                            response=RichResponse(text=full_text),
                            session_id=inbound.session_id,
                        )
                        killed = True
                        break
                    if not chunk:
                        continue
                    full_text += chunk
                    yield await self._format_event(
                        event_type="assistant_chunk",
                        response=RichResponse(text=chunk),
                        session_id=inbound.session_id,
                    )

            if session_fused:
                outbound = Message(
                    text=full_text,
                    attachments=[attachment.model_copy() for attachment in collected_attachments],
                    sender="assistant",
                    session_id=inbound.session_id,
                    channel=inbound.channel,
                    sender_id=inbound.sender_id,
                )
                self._append_session_message(outbound)
                await self._broadcast_message(
                    outbound,
                    origin_channel=inbound.channel,
                    origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
                )
                yield await self._format_event(
                    event_type="assistant_done",
                    response=RichResponse(
                        attachments=[attachment.model_copy() for attachment in collected_attachments],
                    ),
                    session_id=inbound.session_id,
                )
                return

            if killed:
                outbound = Message(
                    text=full_text,
                    attachments=[attachment.model_copy() for attachment in collected_attachments],
                    sender="assistant",
                    session_id=inbound.session_id,
                    channel=inbound.channel,
                    sender_id=inbound.sender_id,
                )
                self._append_session_message(outbound)
                await self._broadcast_message(outbound, origin_channel=inbound.channel)
                yield await self._format_event(
                    event_type="assistant_done",
                    response=RichResponse(
                        attachments=[attachment.model_copy() for attachment in collected_attachments],
                    ),
                    session_id=inbound.session_id,
                )
                return

            if not full_text.strip() and last_tool_fallback_text:
                full_text = last_tool_fallback_text
                yield await self._format_event(
                    event_type="assistant_chunk",
                    response=RichResponse(text=full_text),
                    session_id=inbound.session_id,
                )

            codex_chunk = await self._codex_status_bar_text(session_id=inbound.session_id)
            if codex_chunk is not None:
                if full_text:
                    codex_chunk = f"\n{codex_chunk}"
                full_text += codex_chunk
                yield await self._format_event(
                    event_type="assistant_chunk",
                    response=RichResponse(text=codex_chunk),
                    session_id=inbound.session_id,
                )

            outbound = Message(
                text=full_text,
                attachments=[attachment.model_copy() for attachment in collected_attachments],
                sender="assistant",
                session_id=inbound.session_id,
                channel=inbound.channel,
                sender_id=inbound.sender_id,
            )
            self._append_session_message(outbound)
            await self._mark_retrieved_sops_used()
            await self._broadcast_message(
                outbound,
                origin_channel=inbound.channel,
                origin_client_id=str(inbound.metadata.get("webui_client_id") or ""),
            )
            yield await self._format_event(
                event_type="assistant_done",
                response=RichResponse(
                    attachments=[attachment.model_copy() for attachment in collected_attachments],
                ),
                session_id=inbound.session_id,
            )
        finally:
            await self._cancel_pending_narration_tasks(narration_tasks)

    def _extract_tool_name(self, tool_call: dict[str, Any]) -> str:
        function_payload = tool_call.get("function") or {}
        name = function_payload.get("name")
        if not isinstance(name, str):
            return ""
        return self._remap_legacy_tool_name(name)

    def _remap_legacy_tool_name(self, tool_name: str) -> str:
        normalized = str(tool_name or "").strip()
        if not normalized:
            return ""
        return LEGACY_TOOL_NAME_REMAP.get(normalized, normalized)

    def _remap_legacy_tool_names_in_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        remapped: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                remapped.append(message)
                continue
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                remapped.append(message)
                continue
            updated_calls: list[Any] = []
            changed = False
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    updated_calls.append(tool_call)
                    continue
                function_payload = tool_call.get("function")
                if not isinstance(function_payload, dict):
                    updated_calls.append(tool_call)
                    continue
                original_name = str(function_payload.get("name") or "").strip()
                remapped_name = self._remap_legacy_tool_name(original_name)
                if remapped_name == original_name or not remapped_name:
                    updated_calls.append(tool_call)
                    continue
                updated_calls.append(
                    {
                        **tool_call,
                        "function": {
                            **function_payload,
                            "name": remapped_name,
                        },
                    }
                )
                changed = True
            if changed:
                remapped.append({**message, "tool_calls": updated_calls})
            else:
                remapped.append(message)
        return remapped

    def _parse_tool_arguments(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        function_payload = tool_call.get("function") or {}
        raw_arguments = function_payload.get("arguments")
        if isinstance(raw_arguments, dict):
            return raw_arguments
        if isinstance(raw_arguments, str):
            try:
                parsed = json.loads(raw_arguments)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, dict):
                return parsed
        return {}

    def _tool_not_found_output(self, tool_name: str) -> SkillOutput:
        normalized = str(tool_name or "").strip() or "unknown_tool"
        return SkillOutput(
            status="error",
            error_info=f"tool_not_found: {normalized}",
            result={
                "error": "tool_not_found",
                "tool_name": normalized,
            },
        )

    async def _build_llm_messages(
        self,
        inbound: Message,
        *,
        use_tools: bool = False,
        candidate_skills: list[SkillManifest] | None = None,
        model_name: str | None = None,
        task_type: str | None = None,
    ) -> list[dict[str, Any]]:
        text = self._message_text_for_llm(inbound)
        if not text and not self._has_image_attachments(inbound):
            raise ValueError("text is required for M2 chat pipeline")

        user_content: str | list[dict[str, Any]]
        if self._has_image_attachments(inbound):
            user_content = self._build_multimodal_user_content(inbound, text=text)
        else:
            user_content = text

        llm_messages: list[dict[str, Any]] = []
        persona_prompt = await self._resolve_persona_prompt(text, inbound=inbound)
        if persona_prompt:
            llm_messages.append({"role": "system", "content": persona_prompt})
        skill_catalog = self._skill_catalog_context()
        if skill_catalog:
            llm_messages.append({"role": "system", "content": skill_catalog})
        if use_tools:
            llm_messages.append({"role": "system", "content": TOOL_USE_SYSTEM_PROMPT})
        llm_messages.append({"role": "system", "content": self._system_time_context()})
        resolved_task_type = task_type or self._resolve_task_type_for_inbound(
            inbound,
            use_tools=use_tools,
            candidate_skills=candidate_skills,
        )
        resolved_model_name = model_name or self._resolve_model_for_inbound(
            inbound,
            use_tools=use_tools,
            candidate_skills=candidate_skills,
        )
        llm_messages.append(
            {
                "role": "system",
                "content": self._runtime_model_context(
                    model_name=resolved_model_name,
                    task_type=resolved_task_type,
                ),
            }
        )
        inbound_context = self._current_message_context(inbound)
        if inbound_context:
            llm_messages.append({"role": "system", "content": inbound_context})
        semantic_context = await self._semantic_memory_context(
            text,
            skip_search=self._should_skip_semantic_memory(inbound),
        )
        if semantic_context:
            llm_messages.append({"role": "system", "content": semantic_context})
        prefs_context = self._preferences_context()
        if prefs_context:
            llm_messages.append({"role": "system", "content": prefs_context})
        skill_context = self._skill_instructions_context(candidate_skills or [])
        if skill_context:
            llm_messages.append({"role": "system", "content": skill_context})
        llm_messages.append({"role": "system", "content": REPLY_BOUNDARY_SYSTEM_PROMPT})

        if not self._history_suppressed():
            history = self.session_memory.get_recent_messages(
                inbound.session_id,
                limit=self.history_window,
            )
            selected_history, history_stats = self._select_history_messages(history)
            llm_messages.extend(self._remap_legacy_tool_names_in_messages(selected_history))
            logger.info(
                "pipeline.history_window",
                session_id=inbound.session_id,
                **history_stats,
            )

        llm_messages.append({"role": "user", "content": user_content})
        return llm_messages

    def _runtime_model_context(
        self,
        *,
        model_name: str,
        task_type: str,
        primary_model_name: str | None = None,
    ) -> str:
        model_id = self._model_identifier(model_name)
        return build_runtime_model_context(
            model_display_name=model_name,
            model_id=model_id,
            task_type=task_type,
            primary_model_display_name=primary_model_name,
        )

    def _model_identifier(self, model_name: str) -> str:
        config = getattr(self.router, "config", None)
        models = getattr(config, "models", None)
        if isinstance(models, dict):
            cfg = models.get(model_name)
            model_id = getattr(cfg, "litellm_model", None)
            if isinstance(model_id, str) and model_id.strip():
                return model_id
        return model_name

    def _skill_catalog_context(self) -> str:
        if self.skill_manager is None:
            return ""
        getter = getattr(self.skill_manager, "get_skill_catalog", None)
        if not callable(getter):
            return ""
        try:
            return str(getter() or "").strip()
        except TypeError:
            return ""
        except Exception:
            logger.warning("pipeline.skill_catalog_failed", exc_info=True)
            return ""

    async def _resolve_persona_prompt(self, query: str, *, inbound: Message | None = None) -> str:
        if inbound is not None and self._is_heartbeat_request(inbound):
            return self.persona_system_prompt
        if self.persona_manager is not None:
            getter = getattr(self.persona_manager, "get_system_prompt_section", None)
            if callable(getter):
                result = getter(query=query)
                if inspect.isawaitable(result):
                    result = await result
                prompt = str(result or "").strip()
                if prompt:
                    return prompt
        return self.persona_system_prompt

    def _match_skill_candidates(self, inbound: Message) -> list[SkillManifest]:
        if self.skill_catalog is None:
            return []
        matcher = getattr(self.skill_catalog, "match_candidates", None)
        if not callable(matcher):
            return []
        try:
            result = matcher(self._message_text_for_llm(inbound))
        except Exception:
            logger.warning("skill_catalog.match_failed", exc_info=True)
            return []
        return [item for item in result if isinstance(item, SkillManifest)]

    def _skill_instructions_context(self, candidate_skills: list[SkillManifest]) -> str:
        if not candidate_skills or self.skill_catalog is None:
            return ""
        loader = getattr(self.skill_catalog, "load_body", None)
        if not callable(loader):
            return ""
        sections: list[str] = []
        for manifest in candidate_skills:
            try:
                body = str(loader(manifest.name) or "").strip()
            except Exception:
                logger.warning("skill_catalog.load_body_failed", skill_name=manifest.name, exc_info=True)
                continue
            if body:
                sections.append(f"[Skill: {manifest.name}]\n{body}")
        if not sections:
            return ""
        return "Skill instructions:\n\n" + "\n\n".join(sections)

    def _select_active_skill_manifest(
        self,
        candidate_skills: list[SkillManifest],
        tool_name: str,
    ) -> SkillManifest | None:
        matching = [
            manifest
            for manifest in candidate_skills
            if (not manifest.allowed_tools) or tool_name in manifest.allowed_tools
        ]
        if len(matching) == 1:
            return matching[0]
        return None

    def _resolve_tool_skill_name(
        self,
        candidate_skills: list[SkillManifest],
        tool_name: str,
    ) -> str:
        active_skill = self._select_active_skill_manifest(candidate_skills, tool_name)
        if active_skill is not None:
            return active_skill.name
        if self.skill_manager is None:
            return "direct"
        owner = self._skill_manager_find_skill_by_tool_name(tool_name)
        if owner is not None:
            return owner.name
        return "direct"

    async def _semantic_memory_context(self, query: str, *, skip_search: bool = False) -> str:
        if skip_search:
            self._pending_sop_usage = set()
            return ""
        if self.semantic_memory is None:
            self._pending_sop_usage = set()
            return ""

        search = getattr(self.semantic_memory, "search", None)
        if not callable(search):
            self._pending_sop_usage = set()
            return ""

        try:
            results = search(query, top_k=5)
            if inspect.isawaitable(results):
                results = await results
        except _PIPELINE_RECOVERABLE_ERRORS:
            logger.exception("pipeline.semantic_memory.search_failed")
            self._pending_sop_usage = set()
            return ""

        if not results:
            self._pending_sop_usage = set()
            return ""

        budget = 2000
        used_tokens = estimate_token_count("[相关记忆]\n")
        chunks: list[str] = []
        sop_paths: set[str] = set()
        for item in results:
            chunk_text = str(getattr(item, "chunk_text", "") or "").strip()
            if not chunk_text:
                continue
            chunk_tokens = estimate_token_count(chunk_text) + estimate_token_count("\n---\n")
            if chunks and used_tokens + chunk_tokens > budget:
                break
            chunks.append(chunk_text)
            used_tokens += chunk_tokens
            file_path = str(getattr(item, "file_path", "") or "").strip()
            if file_path and self._is_sop_result(file_path):
                sop_paths.add(file_path)

        if not chunks:
            self._pending_sop_usage = set()
            return ""
        self._pending_sop_usage = sop_paths
        return "[相关记忆]\n" + "\n---\n".join(chunks)

    def _is_sop_result(self, file_path: str) -> bool:
        manager = self.sop_manager
        if manager is not None and hasattr(manager, "is_sop_path"):
            try:
                return bool(manager.is_sop_path(file_path))
            except _PIPELINE_RECOVERABLE_ERRORS as exc:
                # FALLBACK: SOP path detection failure only disables metadata enrichment.
                logger.warning(
                    "pipeline.sop_read.degraded",
                    file_path=file_path,
                    **_error_fields(exc),
                )
                return False
        return "/knowledge/sop/" in file_path.replace("\\", "/")

    async def _mark_retrieved_sops_used(self) -> None:
        pending = set(self._pending_sop_usage)
        self._pending_sop_usage = set()
        if not pending:
            return
        manager = self.sop_manager
        if manager is None:
            return
        toucher = getattr(manager, "touch_files", None)
        if not callable(toucher):
            return
        try:
            result = toucher(sorted(pending))
            if inspect.isawaitable(result):
                await result
        except _PIPELINE_RECOVERABLE_ERRORS:
            logger.exception("pipeline.sop_metadata_update_failed")

    def _track_sop_usage_from_tool_output(
        self,
        tool_name: str,
        output: SkillOutput,
    ) -> None:
        if tool_name != "search_sop" or output.status != "success":
            return
        payload = output.result
        if not isinstance(payload, dict):
            return
        items = payload.get("items")
        if not isinstance(items, list):
            return

        tracked = set(self._pending_sop_usage)
        for item in items:
            if not isinstance(item, dict):
                continue
            file_path = str(item.get("file_path") or "").strip()
            if file_path and self._is_sop_result(file_path):
                tracked.add(file_path)
        self._pending_sop_usage = tracked

    def _preferences_context(self) -> str:
        store = self.structured_store
        if store is None:
            return ""

        lister = getattr(store, "list_preferences_sync", None)
        if not callable(lister):
            return ""

        try:
            rows = lister(limit=20)
        except _PIPELINE_RECOVERABLE_ERRORS as exc:
            # FALLBACK: preference context is optional and can be omitted for this turn.
            logger.warning(
                "pipeline.preference_read.degraded",
                **_error_fields(exc),
            )
            return ""

        if not rows:
            return ""

        lines = ["[High Priority User Preferences]"]
        lines.append(
            "These preferences override any generic tendency to be overly helpful, proactive, or suggest next steps."
        )
        for key, value in rows[:20]:
            if not key:
                continue
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    def _to_llm_message(self, message: Message) -> dict[str, Any] | None:
        text = self._message_text_for_llm(message)
        if not text:
            return None

        if message.sender == "user":
            role = "user"
        elif message.sender == "assistant":
            role = "assistant"
        else:
            return None
        history_context = self._history_message_context(message)
        if history_context:
            text = f"{history_context}\n\n{text}"
        return {"role": role, "content": text}

    def _resolve_task_type_for_inbound(
        self,
        inbound: Message,
        *,
        use_tools: bool,
        candidate_skills: list[SkillManifest] | None = None,
    ) -> str:
        if self._is_heartbeat_request(inbound):
            return "heartbeat"
        if self._has_image_attachments(inbound):
            return "vision"
        if use_tools and candidate_skills:
            getter = getattr(self.router, "get_model_for_task", None)
            if callable(getter):
                try:
                    candidate = str(getter("reasoning") or "").strip()
                except Exception:
                    candidate = ""
                if candidate:
                    return "reasoning"
        return "chat"

    def _resolve_model_for_inbound(
        self,
        inbound: Message,
        *,
        use_tools: bool,
        candidate_skills: list[SkillManifest] | None = None,
    ) -> str:
        task_type = self._resolve_task_type_for_inbound(
            inbound,
            use_tools=use_tools,
            candidate_skills=candidate_skills,
        )
        if task_type == "heartbeat":
            return self._resolve_heartbeat_model()
        if task_type == "vision":
            getter = getattr(self.router, "get_model_for_task", None)
            if callable(getter):
                return str(getter("vision") or self.chat_model)
            return self.chat_model
        if task_type == "reasoning":
            getter = getattr(self.router, "get_model_for_task", None)
            if callable(getter):
                try:
                    candidate = str(getter("reasoning") or "").strip()
                except Exception:
                    candidate = ""
                if candidate:
                    return candidate
        return self.chat_model

    def _message_text_for_llm(self, message: Message) -> str:
        text = (message.text or "").strip()
        if text:
            return text

        attachment_summary = self._attachments_summary_text(message.attachments)
        if attachment_summary:
            return attachment_summary

        legacy_items: list[str] = []
        if message.image:
            legacy_items.append(f"image: {message.image}")
        if message.file:
            legacy_items.append(f"file: {message.file}")
        if message.audio:
            legacy_items.append(f"audio: {message.audio}")
        if not legacy_items:
            return ""
        return "[Attachments]\n- " + "\n- ".join(legacy_items)

    def _current_message_context(self, inbound: Message) -> str:
        channel = self._normalized_channel_name(inbound.channel)
        channel_label = self._channel_label(channel)
        lines = ["[Current Message Context]"]
        lines.append(f"- 当前消息渠道: {channel_label} ({channel})")
        lines.append(f"- 当前会话: {inbound.session_id}")
        if inbound.sender_id:
            lines.append(f"- 当前发送者ID: {inbound.sender_id}")
        if inbound.timestamp is not None:
            lines.append(f"- 当前消息时间: {utc_isoformat(inbound.timestamp)}")
        lines.append("- 这条消息能从上述渠道到达你，说明该渠道到 Agent 的入站链路当前是可用的。")
        lines.append("- 当用户在排查 QQ/微信/WebUI 等渠道问题时，必须结合这条上下文一起判断。")
        return "\n".join(lines)

    def _history_message_context(self, message: Message) -> str:
        channel = self._normalized_channel_name(message.channel)
        if channel in {"", "webui"}:
            return ""

        include_sender = bool(str(message.sender_id or "").strip())
        include_time = message.timestamp is not None
        if not any((include_sender, include_time)):
            return ""

        lines = ["[Historical Message Context]"]
        lines.append(f"- 渠道: {self._channel_label(channel)} ({channel})")
        if include_sender:
            lines.append(f"- 发送者ID: {message.sender_id}")
        if include_time and message.timestamp is not None:
            lines.append(f"- 时间: {utc_isoformat(message.timestamp)}")
        return "\n".join(lines)

    def _normalized_channel_name(self, value: str | None) -> str:
        normalized = str(value or "").strip().lower()
        return normalized or "webui"

    def _channel_label(self, channel: str) -> str:
        mapping = {
            "webui": "WebUI",
            "qq": "QQ",
            "weixin": "微信",
            "system": "系统",
        }
        return mapping.get(channel, channel.upper() if channel else "WebUI")

    def _attachments_summary_text(self, attachments: list[Attachment]) -> str:
        if not attachments:
            return ""
        lines = ["[Attachments]"]
        for attachment in attachments:
            label = attachment.filename or Path(attachment.url).name or attachment.url
            details: list[str] = []
            if attachment.type:
                details.append(attachment.type)
            if attachment.mime_type:
                details.append(attachment.mime_type)
            if attachment.size_bytes is not None:
                details.append(f"{attachment.size_bytes} bytes")
            suffix = f" ({', '.join(details)})" if details else ""
            lines.append(f"- {label}{suffix}")
        return "\n".join(lines)

    def _has_image_attachments(self, message: Message) -> bool:
        return any(attachment.type == "image" for attachment in message.attachments)

    def _build_multimodal_user_content(
        self,
        inbound: Message,
        *,
        text: str,
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        if text:
            content.append({"type": "text", "text": text})
        for attachment in inbound.attachments:
            if attachment.type != "image":
                continue
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": self._image_attachment_url(attachment),
                    },
                }
            )
        if not content:
            raise ValueError("image attachment is required for multimodal content")
        return content

    def _image_attachment_url(self, attachment: Attachment) -> str:
        raw_url = str(attachment.url or "").strip()
        if not raw_url:
            raise ValueError("attachment url is required")
        lowered = raw_url.lower()
        if lowered.startswith(("http://", "https://", "data:")):
            return raw_url

        file_path = Path(raw_url).expanduser().resolve(strict=False)
        payload = file_path.read_bytes()
        mime_type = guess_mime_type(file_path.name, attachment.mime_type)
        encoded = base64.b64encode(payload).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    async def _broadcast_message(
        self,
        message: Message,
        *,
        origin_channel: str | None,
        origin_client_id: str | None = None,
    ) -> None:
        if self._broadcast_suppressed():
            return
        callback = self.on_proactive_message
        if callback is None:
            return
        try:
            result = callback(
                message,
                message_type="ai_reply",
                origin_channel=origin_channel,
                origin_client_id=origin_client_id,
            )
        except TypeError:
            result = callback(message)
        if inspect.isawaitable(result):
            await result

    async def _append_codex_status_bar(self, text: str, *, session_id: str) -> str:
        status_bar = await self._codex_status_bar_text(session_id=session_id)
        if not status_bar:
            return text
        if not text:
            return status_bar
        if text.endswith("\n"):
            return text + status_bar
        return f"{text}\n{status_bar}"

    async def _codex_status_bar_text(self, *, session_id: str) -> str | None:
        if self.coder_task_service is None:
            return None
        getter = getattr(self.coder_task_service, "get_attached_task", None)
        if not callable(getter):
            return None
        try:
            attached = getter(session_id)
            if inspect.isawaitable(attached):
                attached = await attached
        except Exception:
            logger.warning("pipeline.codex_status_bar_failed", exc_info=True)
            return None
        if not isinstance(attached, dict):
            return None
        task_id = str(attached.get("task_id") or "").strip()
        status = str(attached.get("status") or "").strip().lower()
        if not task_id or not status:
            return None
        status_label = {
            "queued": "🕒 QUEUED",
            "running": "⏳ RUNNING",
            "in_progress": "⏳ RUNNING",
            "completed": "✅ COMPLETED",
            "failed": "❌ FAILED",
            "aborted": "⛔ ABORTED",
        }.get(status, f"ℹ️ {status.upper()}")
        status_bar = "\n".join(
            [
                "─────────────────────────────────",
                f"🤖 Codex · {task_id} | {status_label}",
                "   /codex send · /codex status · /codex abort · /codex detach",
                "─────────────────────────────────",
            ]
        )
        return status_bar

    def _system_time_context(self) -> str:
        now = now_local()
        tzinfo = now.tzinfo
        tz_name = getattr(tzinfo, "key", None) if tzinfo is not None else None
        if not tz_name:
            tz_name = "Asia/Shanghai"
        current_time = now.strftime("%Y年%m月%d日 %H:%M (%A)")
        return f"[System Context]\n当前时间: {current_time}\n时区: {tz_name}"

    def _tool_fallback_text(self, output: SkillOutput) -> str:
        result = output.result
        if isinstance(result, dict):
            human_summary = str(result.get("human_summary") or "").strip()
            if human_summary:
                return human_summary
            summary = str(result.get("summary") or "").strip()
            if summary:
                return summary
        if output.status == "success" and isinstance(result, str):
            content = result.strip()
            if content:
                return content
        error_text = str(output.error_info or "").strip()
        if error_text:
            return error_text
        return ""

    def _sanitize_false_access_denied_reply(
        self,
        *,
        text: str,
        fallback_text: str | None,
        session_id: str | None,
    ) -> str:
        normalized_text = str(text or "").strip()
        normalized_fallback = str(fallback_text or "").strip()
        if not normalized_text or not normalized_fallback:
            return text
        if not self._contains_access_denied_reply(normalized_text):
            return text
        if self._contains_access_denied_reply(normalized_fallback):
            return text
        logger.warning(
            "pipeline.false_access_denied_reply_overridden",
            session_id=session_id,
            reply=normalized_text,
            fallback=normalized_fallback,
        )
        return normalized_fallback

    def _contains_access_denied_reply(self, text: str) -> bool:
        lowered = str(text or "").casefold()
        return any(pattern in lowered for pattern in _ACCESS_DENIED_REPLY_PATTERNS)

    def _kill_switch_active(self) -> bool:
        return bool(
            self.circuit_breaker is not None
            and self.circuit_breaker.get_global_kill_switch()
        )

    async def _try_handle_slash(self, inbound: Message) -> str | None:
        if self.slash_commands is None:
            return None
        return await self.slash_commands.try_handle(inbound)

    async def _try_handle_pre_llm_shortcuts(self, inbound: Message) -> str | None:
        if str(inbound.sender or "").strip().lower() != "user":
            return None
        confirmation = await self._try_handle_notion_todo_binding_confirmation(inbound)
        if confirmation is not None:
            return confirmation
        return await self._try_handle_notion_todo_snapshot_request(inbound)

    async def _try_handle_pre_llm_message(self, inbound: Message) -> Message | None:
        if str(inbound.sender or "").strip().lower() != "user":
            return None
        monitor = self.wewe_rss_monitor
        if monitor is None:
            return None
        try:
            is_login_request = bool(monitor.is_login_request(inbound.text))
        except Exception:
            logger.warning("pipeline.wewe_shortcut_match_failed", exc_info=True)
            return None
        if not is_login_request:
            return None
        try:
            message = await monitor.start_login_flow(
                session_id=inbound.session_id,
                channel=inbound.channel,
                sender_id=inbound.sender_id,
            )
        except _PIPELINE_RECOVERABLE_ERRORS:
            logger.warning("pipeline.wewe_shortcut_failed", exc_info=True)
            return Message(
                text="WeWe RSS 二维码获取失败，请稍后重试。",
                sender="assistant",
                session_id=inbound.session_id,
                channel=inbound.channel,
                sender_id=inbound.sender_id,
                message_tag="tool_status",
                metadata={"target_channels": [str(inbound.channel or "webui")]},
            )
        return message

    async def _try_handle_notion_todo_binding_confirmation(self, inbound: Message) -> str | None:
        pending = await get_pending_notion_todo_candidate(self.structured_store)
        if pending is None:
            return None
        text = str(inbound.text or "").strip()
        if not text:
            return None
        if message_confirms_notion_todo_candidate(text, pending):
            confirmed = await confirm_pending_notion_todo_candidate(self.structured_store)
            if confirmed is None:
                return None
            title = str(confirmed.get("title") or "").strip() or "HYX的计划通"
            database_id = str(confirmed.get("database_id") or "").strip()
            return (
                f"已绑定 Notion 待办数据库：{title}（ID: {database_id}）。"
                "后续 heartbeat 将直接使用这个数据库。"
            )
        if message_rejects_notion_todo_candidate(text):
            rejected = await reject_pending_notion_todo_candidate(self.structured_store)
            if rejected is None:
                return None
            title = str(rejected.get("title") or "").strip() or "HYX的计划通"
            return f"已取消绑定候选数据库：{title}。当前 heartbeat 仍不会读取 Notion 待办。"
        return None

    async def _try_handle_notion_todo_snapshot_request(self, inbound: Message) -> str | None:
        if not self._is_notion_todo_snapshot_request(inbound):
            return None
        if self.skill_manager is None:
            return None
        if not self._tool_is_available("get_notion_todo_snapshot"):
            return None
        try:
            output = await self.skill_manager.invoke(
                "get_notion_todo_snapshot",
                {},
                session_id=inbound.session_id,
                skill_name="direct",
            )
        except _PIPELINE_RECOVERABLE_ERRORS:
            logger.warning("pipeline.notion_todo_shortcut_failed", exc_info=True)
            return "Notion 待办查询失败，请稍后重试。"

        text = self._tool_fallback_text(output)
        if text:
            return text
        if output.status != "success":
            return str(output.error_info or "").strip() or "Notion 待办查询失败，请稍后重试。"
        return "Notion 待办查询已执行，但没有返回可展示的结果。"

    def _is_notion_todo_snapshot_request(self, inbound: Message) -> bool:
        text = self._message_text_for_llm(inbound)
        normalized = str(text or "").strip()
        if not normalized:
            return False
        if self._is_notion_todo_followup_request(inbound, normalized):
            return True
        if "绑定" in normalized:
            return False
        if self._has_notion_todo_mutation_intent(normalized):
            return False
        if normalized in {"/计划", "/计划通"}:
            return True
        if normalized.casefold() == "/todo":
            return True
        compact = self._compact_notion_todo_shortcut_text(normalized)
        if compact in {
            "今日计划",
            "列出计划",
            "查看计划通",
            "查看一下计划通",
            "查看今天计划",
            "查看一下今天计划",
            "查看今日计划",
            "查看一下今日计划",
            "查看今天的计划通待办事项",
            "查看一下今天的计划通待办事项",
            "查看今天的计划通事项",
            "查看一下今天的计划通事项",
            "今日计划通事项",
        }:
            return True
        compact_casefold = compact.casefold()
        mentions_todo = any(token in compact for token in ("计划通", "待办")) or "todo" in compact_casefold
        if not mentions_todo:
            return False
        has_view_intent = any(token in compact for token in ("看", "查看", "看看", "列出", "读", "读取", "显示"))
        has_day_hint = any(token in compact for token in ("今日", "今天"))
        return has_view_intent or has_day_hint

    def _has_notion_todo_mutation_intent(self, text: str) -> bool:
        normalized = self._compact_notion_todo_shortcut_text(text)
        return any(
            token in normalized
            for token in (
                "改",
                "改成",
                "改到",
                "删除",
                "删掉",
                "删",
                "推迟",
                "新增",
                "取消",
                "修改",
                "调整",
                "挪到",
                "换到",
                "添加",
                "加一条",
                "创建",
            )
        )

    def _compact_notion_todo_shortcut_text(self, text: str) -> str:
        return re.sub(r"[\s，。！？、,.!?:：;；~～]+", "", str(text or "").strip())

    def _is_notion_todo_followup_request(self, inbound: Message, normalized_text: str) -> bool:
        compact = re.sub(r"[\s，。！？、,.!?:：;；~～]+", "", normalized_text)
        if compact in {"好", "好的", "行", "可以", "继续", "开始吧", "继续吧", "看吧", "查看吧", "查吧"}:
            return self._recent_history_mentions_notion_todo(inbound.session_id)
        return compact in {"好看吧", "好查看吧", "好查吧", "好继续吧", "看一下吧", "查一下吧", "看看吧"} and (
            self._recent_history_mentions_notion_todo(inbound.session_id)
        )

    def _recent_history_mentions_notion_todo(self, session_id: str) -> bool:
        getter = getattr(self.session_memory, "get_recent_messages", None)
        if not callable(getter):
            return False
        try:
            history = getter(session_id, limit=6)
        except Exception:
            logger.warning("pipeline.get_recent_messages_failed", exc_info=True)
            return False
        for item in reversed(history or []):
            if not isinstance(item, Message):
                continue
            text = str(item.text or "").strip()
            if not text:
                continue
            if any(token in text for token in ("Notion 待办数据库", "计划通", "待办事项", "今日待办", "待办")):
                return True
        return False

    def _tool_is_available(self, tool_name: str) -> bool:
        if self.skill_manager is None:
            return False
        getter = getattr(self.skill_manager, "get_tools_schema", None)
        if not callable(getter):
            return False
        try:
            tools = getter()
        except Exception:
            logger.warning("pipeline.get_tools_schema_failed", exc_info=True)
            return False
        return any(
            str(tool.get("function", {}).get("name") or "").strip() == tool_name
            for tool in tools
            if isinstance(tool, dict)
        )

    async def _format_event(
        self,
        *,
        event_type: str,
        response: RichResponse,
        session_id: str,
    ) -> dict[str, Any]:
        self._mark_activity(reason=event_type, session_id=session_id)
        formatted = self.channel_adapter.format(
            response,
            event_type=event_type,
            session_id=session_id,
        )
        if inspect.isawaitable(formatted):
            formatted = await formatted
        return dict(formatted)

    def get_last_activity_at(self) -> str:
        return self._last_activity_at

    def last_activity_age_seconds(self) -> float:
        return max(0.0, perf_counter() - self._last_activity_monotonic)

    def _mark_activity(self, *, reason: str, session_id: str | None = None) -> None:
        del reason, session_id
        self._last_activity_monotonic = perf_counter()
        self._last_activity_at = utc_isoformat(utc_now())

    def _tool_content_for_llm(
        self,
        output: SkillOutput,
        *,
        serialized_output: str,
    ) -> str:
        result = output.result
        if output.status == "success" and isinstance(result, str):
            stripped = result.strip()
            if stripped:
                return stripped
        return serialized_output

    def _tool_content_for_react(
        self,
        output: SkillOutput,
        *,
        serialized_output: str,
        inbound: Message | None,
    ) -> str:
        del inbound
        return self._tool_content_for_llm(output, serialized_output=serialized_output)

    def _should_export_long_output(self, content: str) -> bool:
        return len(str(content or "")) > self.long_output_threshold_chars

    def _externalized_tool_output_notice(
        self,
        *,
        attachment: Attachment,
        tool_name: str,
    ) -> str:
        normalized_tool = str(tool_name or "").strip() or "tool"
        return (
            f"{normalized_tool} 输出过长，已导出为 Markdown 文件附件："
            f"{attachment.filename or Path(attachment.url).name}"
        )

    def _export_long_tool_output_attachment(
        self,
        *,
        content: str,
        tool_name: str,
        tool_call_id: str,
        session_id: str,
    ) -> Attachment:
        stem = sanitize_upload_filename(
            f"tool-output-{session_id}-{tool_name or 'tool'}-{tool_call_id or 'call'}"
        )
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_path = (self.long_output_export_dir / f"{timestamp}_{Path(stem).stem}.md").resolve(strict=False)
        target_path.write_text(str(content or "") + "\n", encoding="utf-8")
        return Attachment(
            type="file",
            url=str(target_path),
            filename=target_path.name,
            mime_type="text/markdown",
            size_bytes=target_path.stat().st_size,
        )

    def _tool_is_retryable(self, tool_name: str) -> bool:
        return tool_name in _RETRYABLE_TOOLS

    async def _invoke_tool_with_retry(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        session_id: str,
        skill_name: str,
        event_emitter: Any | None = None,
        iteration: int | None = None,
    ) -> SkillOutput:
        assert self.skill_manager is not None
        attempts = 2 if self._tool_is_retryable(tool_name) else 1
        last_output: SkillOutput | None = None
        for attempt in range(1, attempts + 1):
            output = await self.skill_manager.invoke(
                tool_name,
                arguments,
                session_id=session_id,
                skill_name=skill_name,
            )
            last_output = output
            if output.status == "success" or attempt >= attempts:
                if attempt > 1:
                    logger.info(
                        "tool.retry.completed",
                        session_id=session_id,
                        tool_name=tool_name,
                        attempts=attempt,
                        final_status=output.status,
                    )
                return output
            logger.warning(
                "tool.retrying",
                session_id=session_id,
                tool_name=tool_name,
                attempt=attempt,
                error_info=output.error_info,
            )
            await self._emit_progress_event(
                {
                    "type": "tool_call_error",
                    "tool": tool_name,
                    "error": str(output.error_info or "tool failed"),
                    "will_retry": True,
                    "iteration": iteration,
                    "session_id": session_id,
                },
                event_emitter=event_emitter,
            )
        assert last_output is not None
        return last_output

    async def _send_tool_status(
        self,
        *,
        tool_name: str,
        status: str,
        session_id: str,
        error: str = "",
        event_emitter: Any | None = None,
        origin_channel: str | None = None,
        origin_client_id: str | None = None,
    ) -> None:
        callback = self.on_proactive_message
        if callback is None:
            return
        if self._resolve_event_emitter(event_emitter) is not None:
            return
        if self._tool_status_suppressed():
            return

        templates = TOOL_STATUS_TEMPLATES.get(tool_name) or TOOL_STATUS_TEMPLATES["_default"]
        template = templates.get(status)
        if not template:
            return
        text = template.format(error=error)
        if not text:
            return

        status_message = Message(
            text=text,
            sender="assistant",
            session_id=session_id,
            message_tag="tool_status",
            metadata={"ephemeral": True},
        )
        try:
            callback_result = callback(
                status_message,
                message_type="ai_reply",
                origin_channel=origin_channel,
                origin_client_id=origin_client_id,
                exclude_channels={str(origin_channel).strip()} if str(origin_channel or "").strip() else None,
                exclude_client_ids={str(origin_client_id).strip()} if str(origin_client_id or "").strip() else None,
            )
        except TypeError:
            callback_result = callback(status_message)
        if inspect.isawaitable(callback_result):
            await callback_result

    def _tool_status_suppressed(self) -> bool:
        if self._tool_status_context_suppressed():
            return True
        observer = self.narration_observer
        if observer is None:
            return False
        return bool(getattr(observer, "enabled", False))

    def _schedule_narration_task(
        self,
        *,
        inbound: Message,
        tool_name: str,
        arguments: dict[str, Any],
        iteration_number: int,
        total_tools_called: int,
    ) -> asyncio.Task[None] | None:
        if self._broadcast_suppressed():
            return None
        observer = self.narration_observer
        callback = self.on_narration
        if observer is None or callback is None:
            return None

        user_message = str(inbound.text or "").strip()
        if not user_message:
            return None

        async def _runner() -> None:
            try:
                narration = await observer.maybe_narrate(
                    tool_name=tool_name,
                    tool_args=arguments,
                    user_message_context=user_message,
                    session_id=inbound.session_id,
                    iteration_number=iteration_number,
                    total_tools_called=total_tools_called,
                )
            except asyncio.CancelledError:
                raise
            except _PIPELINE_RECOVERABLE_ERRORS:
                logger.exception(
                    "narration.emit.failed",
                    session_id=inbound.session_id,
                    tool_name=tool_name,
                )
                return

            if not narration:
                return

            payload = {
                "type": "narration",
                "text": narration,
                "session_id": inbound.session_id,
                "timestamp": utc_isoformat(utc_now()),
            }

            try:
                result = callback(
                    payload,
                    origin_channel=inbound.channel,
                    sender_id=inbound.sender_id,
                )
            except TypeError:
                try:
                    result = callback(payload)
                except _PIPELINE_RECOVERABLE_ERRORS:
                    logger.exception(
                        "narration.callback.failed",
                        session_id=inbound.session_id,
                        tool_name=tool_name,
                    )
                    return
            except _PIPELINE_RECOVERABLE_ERRORS:
                logger.exception(
                    "narration.callback.failed",
                    session_id=inbound.session_id,
                    tool_name=tool_name,
                )
                return
            if inspect.isawaitable(result):
                try:
                    await result
                except _PIPELINE_RECOVERABLE_ERRORS:
                    logger.exception(
                        "narration.callback.failed",
                        session_id=inbound.session_id,
                        tool_name=tool_name,
                    )

        return asyncio.create_task(_runner())

    def _record_narration_trace_event(
        self,
        *,
        session_id: str | None,
        event_type: str,
        tool_name: str,
        summary: str,
        elapsed_ms: int,
    ) -> None:
        observer = self.narration_observer
        recorder = getattr(observer, "record_trace_event", None)
        if not callable(recorder):
            return
        try:
            recorder(
                session_id=session_id,
                event_type=event_type,
                tool_name=tool_name,
                summary=summary,
                elapsed_ms=elapsed_ms,
            )
        except _PIPELINE_RECOVERABLE_ERRORS:
            logger.exception(
                "narration.trace_record.failed",
                session_id=session_id,
                tool_name=tool_name,
                event_type=event_type,
            )

    def _cancel_fast_qq_narration(
        self,
        *,
        task: asyncio.Task[None] | None,
        inbound: Message,
        started_at: float,
    ) -> None:
        if task is None or task.done():
            return
        if str(inbound.channel or "").strip().lower() != "qq":
            return
        if perf_counter() - started_at >= 1.0:
            return
        task.cancel()

    async def _cancel_pending_narration_tasks(
        self,
        tasks: set[asyncio.Task[None]],
    ) -> None:
        if not tasks:
            return
        pending = [task for task in tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _consume_event_loop(self) -> None:
        logger.info("event_consumer.started")
        assert self.event_queue is not None
        while True:
            event: dict[str, Any] = await self.event_queue.get()
            try:
                self._mark_activity(reason="event_queue_message")
                logger.info(
                    "event_consumer.processing",
                    event_type=event.get("event_type"),
                )
                if str(event.get("event_type") or "").strip().lower() == "user_message":
                    await self._consume_user_message_event(event)
                    continue
                message = self._event_to_message(event)
                if message is None:
                    continue
                self._append_session_message(message)
                if self.on_proactive_message is not None:
                    callback_result = self.on_proactive_message(message)
                    if inspect.isawaitable(callback_result):
                        await callback_result
            except Exception:
                logger.exception("pipeline.event_consumer.failed", queue_event=event)
            finally:
                task_done = getattr(self.event_queue, "task_done", None)
                if callable(task_done):
                    task_done()

    async def _consume_user_message_event(self, event: dict[str, Any]) -> None:
        raw_message = event.get("message")
        if isinstance(raw_message, Message):
            inbound = raw_message
        elif isinstance(raw_message, dict):
            inbound = Message.model_validate(raw_message)
        else:
            raise ValueError("user_message event missing 'message'")

        emitter = event.get("emit")
        if not callable(emitter):
            raise ValueError("user_message event missing callable 'emit'")

        tokens: list[tuple[ContextVar[Any], object]] = []
        if self._is_internal_heartbeat_message(inbound):
            inbound = inbound.model_copy(
                update={
                    "channel": "system",
                    "message_tag": "heartbeat",
                    "metadata": {
                        **dict(inbound.metadata),
                        "source": "heartbeat",
                        "skip_memory_search": True,
                    },
                }
            )
            tokens = [
                (_PIPELINE_INTERNAL_SOURCE, _PIPELINE_INTERNAL_SOURCE.set("heartbeat")),
                (_PIPELINE_SUPPRESS_PERSISTENCE, _PIPELINE_SUPPRESS_PERSISTENCE.set(True)),
                (_PIPELINE_SUPPRESS_BROADCAST, _PIPELINE_SUPPRESS_BROADCAST.set(True)),
                (_PIPELINE_SUPPRESS_TOOL_STATUS, _PIPELINE_SUPPRESS_TOOL_STATUS.set(True)),
                (_PIPELINE_SUPPRESS_HISTORY, _PIPELINE_SUPPRESS_HISTORY.set(True)),
            ]

        try:
            stream_kwargs: dict[str, Any] = {}
            if self._method_supports_kwarg(self.stream_reply, "event_emitter"):
                stream_kwargs["event_emitter"] = emitter
            async for payload in self.stream_reply(inbound, **stream_kwargs):
                emit_result = emitter(payload)
                if inspect.isawaitable(emit_result):
                    await emit_result
        except TimeoutError as exc:
            logger.warning(
                "pipeline.error_converted",
                session_id=inbound.session_id,
                converted_type="LLM_TIMEOUT",
                **_error_fields(exc),
            )
            error_payload = {
                "type": "error",
                "code": "LLM_TIMEOUT",
                "message": "LLM 调用超时，请稍后重试",
                "retryable": True,
                "session_id": inbound.session_id,
            }
            emit_result = emitter(error_payload)
            if inspect.isawaitable(emit_result):
                await emit_result
        except RuntimeError as exc:
            logger.warning(
                "pipeline.error_converted",
                session_id=inbound.session_id,
                converted_type="LLM_RUNTIME_ERROR",
                **_error_fields(exc),
            )
            error_payload = {
                "type": "error",
                "code": "LLM_RUNTIME_ERROR",
                "message": "LLM 调用失败，请检查配置或稍后重试",
                "retryable": True,
                "session_id": inbound.session_id,
            }
            emit_result = emitter(error_payload)
            if inspect.isawaitable(emit_result):
                await emit_result
        except Exception as exc:
            logger.warning(
                "pipeline.error_converted",
                session_id=inbound.session_id,
                converted_type="LLM_RUNTIME_ERROR",
                **_error_fields(exc),
            )
            error_payload = {
                "type": "error",
                "code": "LLM_RUNTIME_ERROR",
                "message": "LLM 调用失败，请检查配置或稍后重试",
                "retryable": True,
                "session_id": inbound.session_id,
            }
            emit_result = emitter(error_payload)
            if inspect.isawaitable(emit_result):
                await emit_result
        finally:
            for context_var, token in reversed(tokens):
                context_var.reset(token)

    def _event_to_message(self, event: dict[str, Any]) -> Message | None:
        event_type = str(event.get("event_type") or "").strip().lower()
        session_id = str(event.get("session_id") or "main")
        title = str(event.get("title") or "").strip()
        description = str(event.get("description") or "").strip()
        summary = str(event.get("summary") or "").strip()

        if event_type == "reminder_trigger":
            text = f"🔔 提醒：{title}" if title else "🔔 提醒"
            if description:
                text += f"\n{description}"
            return Message(
                text=text,
                sender="assistant",
                session_id=session_id,
                message_tag="reminder",
                channel="system",
                metadata=self._event_message_metadata(event),
            )

        if event_type == "heartbeat_trigger":
            if summary:
                text = f"💓 {summary}"
            else:
                text = f"🔔 Heartbeat 异常：{title}" if title else "🔔 Heartbeat 异常"
                if description:
                    text += f"\n{description}"
            return Message(
                text=text,
                sender="assistant",
                session_id=session_id,
                message_tag="heartbeat",
                channel="system",
                metadata=self._event_message_metadata(event),
            )

        if event_type == "email_scan_trigger":
            text = summary or "📧 邮件扫描完成（暂无新增）"
            if not text.startswith(("🔴", "⚪", "📂", "📧")):
                text = f"📧 {text}"
            return Message(
                text=text,
                sender="assistant",
                session_id=session_id,
                message_tag="email_scan",
                channel="system",
                metadata=self._event_message_metadata(event),
            )

        if event_type == "hypo_info_trigger":
            header = title or "Hypo-Info 更新"
            body = summary or description
            text = header
            if body:
                text = f"{header}\n{body}"
            if not text.startswith(("📰", "📡", "ℹ️")):
                text = f"📰 {text}"
            return Message(
                text=text,
                sender="assistant",
                session_id=session_id,
                message_tag="hypo_info",
                channel="system",
                metadata=self._event_message_metadata(event),
            )

        if event_type == "wewe_rss_trigger":
            text = summary or title or "WeWe RSS 状态更新"
            if not text.startswith(("📚", "⚠️", "ℹ️")):
                text = f"📚 {text}"
            return Message(
                text=text,
                sender="assistant",
                session_id=session_id,
                message_tag="tool_status",
                channel="system",
                metadata=self._event_message_metadata(event),
            )

        if event_type == "subscription_trigger":
            text = summary or title or "📡 订阅更新"
            if not text.startswith(("📺", "📢", "📡")):
                text = f"📡 {text}"
            return Message(
                text=text,
                sender="assistant",
                session_id=session_id,
                message_tag="subscription",
                channel="system",
                metadata=self._event_message_metadata(event),
            )

        return None

    def _event_message_metadata(self, event: dict[str, Any]) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        target_channels = self._resolve_target_channels(event.get("channel"))
        if target_channels is not None:
            metadata["target_channels"] = sorted(target_channels)
        raw_channel = str(event.get("channel") or "").strip()
        if raw_channel:
            metadata["delivery_channel"] = raw_channel
        event_type = str(event.get("event_type") or "").strip().lower()
        if event_type:
            metadata["event_source"] = event_type
        return metadata

    def _resolve_target_channels(self, raw_value: Any) -> set[str] | None:
        if raw_value is None:
            return None

        raw_items: list[str] = []
        if isinstance(raw_value, str):
            raw_items = [item.strip().lower() for item in raw_value.split(",")]
        elif isinstance(raw_value, (list, tuple, set)):
            raw_items = [str(item).strip().lower() for item in raw_value]
        else:
            raw_items = [str(raw_value).strip().lower()]

        normalized = {item for item in raw_items if item}
        if not normalized or "all" in normalized:
            return None

        supported = {"webui", "qq", "weixin", "feishu"}
        filtered = {item for item in normalized if item in supported}
        return filtered or None
