from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from contextvars import ContextVar
from datetime import datetime
import inspect
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

import structlog

from hypo_agent.core.channel_adapter import ChannelAdapter, WebUIAdapter
from hypo_agent.core.rich_response import RichResponse
from hypo_agent.core.skill_catalog import SkillManifest
from hypo_agent.core.time_utils import utc_isoformat, utc_now
from hypo_agent.core.uploads import guess_mime_type
from hypo_agent.exceptions import HypoAgentError
from hypo_agent.memory.semantic_memory import ChunkResult, estimate_token_count
from hypo_agent.memory.session import SessionMemory
from hypo_agent.models import Attachment, Message, SkillOutput

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
SESSION_FUSED_MESSAGE = "⚠️ 本次对话累计错误过多（5 次），已暂停执行。请检查问题后重新发送消息继续。"
COMPRESSED_MARKER_PREFIX = "[📦 Output compressed"

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
    "web_search": {
        "start": "🔍 正在搜索...",
        "ok": "🔍 搜索完成",
        "fail": "❌ 搜索失败：{error}",
    },
    "_default": {
        "start": "⏳ 正在处理...",
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
    ) -> str: ...

    async def call_with_tools(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]: ...

    async def stream(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        session_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        timeout_seconds: float | None = None,
    ) -> AsyncIterator[str]: ...


class ChatSkillManager(Protocol):
    def get_tools_schema(self) -> list[dict[str, Any]]: ...

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
    ) -> tuple[str, bool]: ...


class ChatPipeline:
    def __init__(
        self,
        router: ChatModelRouter,
        chat_model: str,
        session_memory: SessionMemory,
        history_window: int = 20,
        skill_manager: ChatSkillManager | None = None,
        structured_store: Any | None = None,
        circuit_breaker: Any | None = None,
        max_react_rounds: int = 15,
        heartbeat_max_react_rounds: int | None = None,
        heartbeat_model_timeout_seconds: int | None = 60,
        heartbeat_allowed_tools: set[str] | None = None,
        slash_commands: SlashCommands | None = None,
        output_compressor: ChatOutputCompressor | None = None,
        channel_adapter: ChannelAdapter | None = None,
        event_queue: Any | None = None,
        on_proactive_message: Any | None = None,
        persona_system_prompt: str = "",
        persona_manager: Any | None = None,
        semantic_memory: Any | None = None,
        sop_manager: Any | None = None,
        narration_observer: Any | None = None,
        on_narration: Any | None = None,
        skill_catalog: Any | None = None,
    ) -> None:
        self.router = router
        self.chat_model = chat_model
        self.session_memory = session_memory
        self.history_window = history_window
        self.skill_manager = skill_manager
        self.structured_store = structured_store
        self.circuit_breaker = circuit_breaker
        self.max_react_rounds = max_react_rounds
        self.heartbeat_max_react_rounds = heartbeat_max_react_rounds
        self.heartbeat_model_timeout_seconds = heartbeat_model_timeout_seconds
        self.heartbeat_allowed_tools = set(heartbeat_allowed_tools or set())
        self.slash_commands = slash_commands
        self.output_compressor = output_compressor
        self.channel_adapter = channel_adapter or WebUIAdapter()
        self.event_queue = event_queue
        self.on_proactive_message = on_proactive_message
        self.persona_system_prompt = persona_system_prompt.strip()
        self.persona_manager = persona_manager
        self.semantic_memory = semantic_memory
        self.sop_manager = sop_manager
        self.narration_observer = narration_observer
        self.on_narration = on_narration
        self.skill_catalog = skill_catalog
        self._event_consumer_task: asyncio.Task[None] | None = None
        self._pending_sop_usage: set[str] = set()

    async def start_event_consumer(self) -> None:
        if self.event_queue is None:
            return
        if self._event_consumer_task is not None and not self._event_consumer_task.done():
            return
        self._event_consumer_task = asyncio.create_task(self._consume_event_loop())

    async def stop_event_consumer(self) -> None:
        task = self._event_consumer_task
        if task is None:
            return
        self._event_consumer_task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

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

    def _should_force_final_response(self, round_num: int, *, max_react_rounds: int) -> bool:
        return max_react_rounds > 0 and round_num >= max(1, max_react_rounds - 1)

    def _heartbeat_timeout_for(self, inbound: Message | None) -> float | None:
        if not self._is_heartbeat_request(inbound):
            return None
        timeout_seconds = self.heartbeat_model_timeout_seconds
        if timeout_seconds is None:
            return None
        return float(timeout_seconds)

    def _filter_tools_for_inbound(
        self,
        tools: list[dict[str, Any]],
        *,
        inbound: Message | None,
    ) -> list[dict[str, Any]]:
        if not self._is_heartbeat_request(inbound) or not self.heartbeat_allowed_tools:
            return tools
        allowed = self.heartbeat_allowed_tools
        return [
            tool
            for tool in tools
            if str(tool.get("function", {}).get("name") or "").strip() in allowed
        ]

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
    ) -> str:
        kwargs: dict[str, Any] = {}
        timeout_seconds = self._heartbeat_timeout_for(inbound)
        if timeout_seconds is not None and self._method_supports_kwarg(self.router.call, "timeout_seconds"):
            kwargs["timeout_seconds"] = timeout_seconds
        return await self.router.call(model_name, messages, **kwargs)

    async def _router_call_with_tools(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        inbound: Message | None,
        tools: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self._method_supports_kwarg(self.router.call_with_tools, "tools"):
            kwargs["tools"] = tools
        if self._method_supports_kwarg(self.router.call_with_tools, "session_id"):
            kwargs["session_id"] = session_id
        timeout_seconds = self._heartbeat_timeout_for(inbound)
        if timeout_seconds is not None and self._method_supports_kwarg(self.router.call_with_tools, "timeout_seconds"):
            kwargs["timeout_seconds"] = timeout_seconds
        return await self.router.call_with_tools(model_name, messages, **kwargs)

    async def _router_stream(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        inbound: Message | None,
        tools: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[str]:
        kwargs: dict[str, Any] = {}
        if self._method_supports_kwarg(self.router.stream, "tools"):
            kwargs["tools"] = tools
        if self._method_supports_kwarg(self.router.stream, "session_id"):
            kwargs["session_id"] = session_id
        timeout_seconds = self._heartbeat_timeout_for(inbound)
        if timeout_seconds is not None and self._method_supports_kwarg(self.router.stream, "timeout_seconds"):
            kwargs["timeout_seconds"] = timeout_seconds
        async for chunk in self.router.stream(model_name, messages, **kwargs):
            yield chunk

    async def _generate_round_limit_summary(
        self,
        react_messages: list[dict[str, Any]],
        *,
        session_id: str,
        max_react_rounds: int,
    ) -> str:
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
            if (
                self.heartbeat_model_timeout_seconds is not None
                and self._method_supports_kwarg(self.router.call_with_tools, "timeout_seconds")
            ):
                decision_kwargs["timeout_seconds"] = self.heartbeat_model_timeout_seconds
            decision = await self.router.call_with_tools(
                self.chat_model,
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
                if (
                    self.heartbeat_model_timeout_seconds is not None
                    and self._method_supports_kwarg(self.router.stream, "timeout_seconds")
                ):
                    stream_kwargs["timeout_seconds"] = self.heartbeat_model_timeout_seconds
                async for chunk in self.router.stream(
                    self.chat_model,
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

        return "Stopped due to max ReAct rounds limit."

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
        llm_messages = await self._build_llm_messages(
            inbound,
            candidate_skills=candidate_skills,
        )
        model_name = self._resolve_model_for_inbound(inbound)
        self._append_session_message(inbound)
        text = await self._router_call(model_name, llm_messages, inbound=inbound)
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

    async def stream_reply(self, inbound: Message) -> AsyncIterator[dict[str, Any]]:
        narration_tasks: set[asyncio.Task[None]] = set()
        try:
            collected_attachments: list[Attachment] = []
            slash_result = await self._try_handle_slash(inbound)
            if slash_result is not None:
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

            max_react_rounds = self._effective_max_react_rounds(inbound)
            use_tools = self.skill_manager is not None and max_react_rounds > 0
            candidate_skills = self._match_skill_candidates(inbound)
            llm_messages = await self._build_llm_messages(
                inbound,
                use_tools=use_tools,
                candidate_skills=candidate_skills,
            )
            model_name = self._resolve_model_for_inbound(inbound)
            self._append_session_message(inbound)

            full_text = ""
            killed = False
            session_fused = False
            last_compressed_meta: dict[str, Any] | None = None

            if use_tools:
                assert self.skill_manager is not None
                tools = self._filter_tools_for_inbound(
                    self.skill_manager.get_tools_schema(),
                    inbound=inbound,
                )
                tool_names = [
                    str(tool.get("function", {}).get("name") or "")
                    for tool in tools
                    if str(tool.get("function", {}).get("name") or "").strip()
                ]
                react_messages: list[dict[str, Any]] = list(llm_messages)
                reached_round_limit = True
                logger.info(
                    "react.start",
                    session_id=inbound.session_id,
                    max_rounds=max_react_rounds,
                )
                logger.debug(
                    "react.tools",
                    session_id=inbound.session_id,
                    tool_names=tool_names,
                    tool_count=len(tool_names),
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
                    decision = await self._router_call_with_tools(
                        model_name,
                        react_messages,
                        inbound=inbound,
                        tools=tools,
                        session_id=inbound.session_id,
                    )
                    tool_calls = decision.get("tool_calls") or []
                    logger.info(
                        "react.round",
                        round=round_num,
                        tool_calls=len(tool_calls),
                    )
                    if not tool_calls:
                        reached_round_limit = False
                        text = str(decision.get("text") or "")
                        if text:
                            full_text = text
                            yield await self._format_event(
                                event_type="assistant_chunk",
                                response=RichResponse(text=text),
                                session_id=inbound.session_id,
                            )
                        else:
                            async for chunk in self._router_stream(
                                model_name,
                                react_messages,
                                inbound=inbound,
                                session_id=inbound.session_id,
                                tools=tools,
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
                            if killed:
                                break
                        break

                    react_messages.append(
                        {
                            "role": "assistant",
                            "content": decision.get("text", ""),
                            "tool_calls": tool_calls,
                        }
                    )
                    for tool_call in tool_calls:
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
                        )
                        if narration_task is not None:
                            narration_tasks.add(narration_task)
                            narration_task.add_done_callback(narration_tasks.discard)
                        await self._send_tool_status(
                            tool_name=tool_name,
                            status="start",
                            session_id=inbound.session_id,
                        )
                        yield await self._format_event(
                            event_type="tool_call_start",
                            response=RichResponse(
                                tool_calls=[
                                    {
                                        "tool_name": tool_name,
                                        "tool_call_id": tool_call_id,
                                        "arguments": arguments,
                                    }
                                ]
                            ),
                            session_id=inbound.session_id,
                        )

                        started_at = perf_counter()
                        try:
                            output = await self.skill_manager.invoke(
                                tool_name,
                                arguments,
                                session_id=inbound.session_id,
                                skill_name=active_skill.name if active_skill is not None else "direct",
                            )
                        except HypoAgentError as exc:
                            await self._send_tool_status(
                                tool_name=tool_name,
                                status="fail",
                                session_id=inbound.session_id,
                                error=str(exc),
                            )
                            raise
                        finally:
                            self._cancel_fast_qq_narration(
                                task=narration_task,
                                inbound=inbound,
                                started_at=started_at,
                            )

                        if output.status == "success":
                            self._track_sop_usage_from_tool_output(tool_name, output)
                            await self._send_tool_status(
                                tool_name=tool_name,
                                status="ok",
                                session_id=inbound.session_id,
                            )
                        else:
                            await self._send_tool_status(
                                tool_name=tool_name,
                                status="fail",
                                session_id=inbound.session_id,
                                error=output.error_info,
                            )
                        serialized_output = json.dumps(output.model_dump(mode="json"), ensure_ascii=False)
                        tool_content = self._tool_content_for_llm(
                            output,
                            serialized_output=serialized_output,
                        )
                        tool_result_for_event: Any = output.result
                        tool_metadata_for_event = dict(output.metadata)
                        tool_metadata_for_event["ephemeral"] = True
                        tool_attachments_for_event = [
                            attachment.model_copy()
                            for attachment in output.attachments
                        ]
                        if tool_attachments_for_event:
                            collected_attachments.extend(tool_attachments_for_event)
                        compressed_meta_for_event: dict[str, Any] | None = None
                        if self.output_compressor is not None:
                            compression_metadata: dict[str, Any] = {
                                "session_id": inbound.session_id,
                                "tool_name": tool_name,
                                "tool_call_id": tool_call_id,
                            }
                            tool_content, was_compressed = await self.output_compressor.compress_if_needed(
                                tool_content,
                                compression_metadata,
                            )
                            if was_compressed:
                                tool_result_for_event = tool_content
                                tool_metadata_for_event["compressed"] = True
                                tool_metadata_for_event["original_chars"] = len(
                                    self._tool_content_for_llm(output, serialized_output=serialized_output)
                                )
                                tool_metadata_for_event["compressed_chars"] = len(tool_content)
                                compressed_meta = compression_metadata.get("compressed_meta")
                                if isinstance(compressed_meta, dict):
                                    compressed_meta_for_event = dict(compressed_meta)
                                    last_compressed_meta = dict(compressed_meta)
                                    await self._persist_tool_invocation_compressed_meta(
                                        output=output,
                                        compressed_meta=compressed_meta_for_event,
                                    )
                        yield await self._format_event(
                            event_type="tool_call_result",
                            response=RichResponse(
                                compressed_meta=compressed_meta_for_event,
                                attachments=tool_attachments_for_event,
                                tool_calls=[
                                    {
                                        "tool_name": tool_name,
                                        "tool_call_id": tool_call_id,
                                        "status": output.status,
                                        "result": tool_result_for_event,
                                        "error_info": output.error_info,
                                        "metadata": tool_metadata_for_event,
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
                    ):
                        reached_round_limit = False
                        logger.warning(
                            "react.round_limit.degrading",
                            session_id=inbound.session_id,
                            round=round_num,
                            max_rounds=max_react_rounds,
                        )
                        full_text = await self._generate_round_limit_summary(
                            react_messages,
                            session_id=inbound.session_id,
                            max_react_rounds=max_react_rounds,
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
            else:
                async for chunk in self._router_stream(
                    model_name,
                    llm_messages,
                    inbound=inbound,
                    session_id=inbound.session_id,
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

            extra_chunk = self._apply_compression_marker(
                text=full_text,
                compressed_meta=last_compressed_meta,
            )
            if extra_chunk is not None:
                full_text += extra_chunk
                yield await self._format_event(
                    event_type="assistant_chunk",
                    response=RichResponse(text=extra_chunk),
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
        return str(name) if isinstance(name, str) else ""

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

    async def _build_llm_messages(
        self,
        inbound: Message,
        *,
        use_tools: bool = False,
        candidate_skills: list[SkillManifest] | None = None,
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
        persona_prompt = await self._resolve_persona_prompt(text)
        if persona_prompt:
            llm_messages.append({"role": "system", "content": persona_prompt})
        if use_tools:
            llm_messages.append({"role": "system", "content": TOOL_USE_SYSTEM_PROMPT})
        llm_messages.append({"role": "system", "content": self._system_time_context()})
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
            for item in history:
                llm_item = self._to_llm_message(item)
                if llm_item is not None:
                    llm_messages.append(llm_item)

        llm_messages.append({"role": "user", "content": user_content})
        return llm_messages

    async def _resolve_persona_prompt(self, query: str) -> str:
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

    def _resolve_model_for_inbound(self, inbound: Message) -> str:
        if self._has_image_attachments(inbound):
            getter = getattr(self.router, "get_model_for_task", None)
            if callable(getter):
                return str(getter("vision") or self.chat_model)
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

    def _system_time_context(self) -> str:
        now = datetime.now().astimezone()
        tzinfo = now.tzinfo
        tz_name = None
        if tzinfo is not None:
            tz_name = getattr(tzinfo, "key", None)
            if not tz_name:
                tz_name = tzinfo.tzname(now)
        if not tz_name:
            tz_name = "local"
        return f"[System Context]\n当前时间: {now.isoformat()} ({tz_name})"

    def _apply_compression_marker(
        self,
        *,
        text: str,
        compressed_meta: dict[str, Any] | None,
    ) -> str | None:
        if not compressed_meta:
            return None
        if COMPRESSED_MARKER_PREFIX in text:
            return None
        marker = self._format_compression_marker(compressed_meta)
        if not marker:
            return None
        if not text:
            return marker
        prefix = "\n" if not text.endswith(("\n", "\r")) else ""
        return f"{prefix}{marker}"

    def _format_compression_marker(self, compressed_meta: dict[str, Any]) -> str | None:
        try:
            original_chars = int(compressed_meta.get("original_chars"))
            compressed_chars = int(compressed_meta.get("compressed_chars"))
        except (TypeError, ValueError):
            return None
        if original_chars <= 0 or compressed_chars <= 0:
            return None
        return (
            f"[📦 Output compressed from {original_chars} → {compressed_chars} chars. "
            "Original saved to logs. Ask me for details.]"
        )

    def _kill_switch_active(self) -> bool:
        return bool(
            self.circuit_breaker is not None
            and self.circuit_breaker.get_global_kill_switch()
        )

    async def _try_handle_slash(self, inbound: Message) -> str | None:
        if self.slash_commands is None:
            return None
        return await self.slash_commands.try_handle(inbound)

    async def _format_event(
        self,
        *,
        event_type: str,
        response: RichResponse,
        session_id: str,
    ) -> dict[str, Any]:
        formatted = self.channel_adapter.format(
            response,
            event_type=event_type,
            session_id=session_id,
        )
        if inspect.isawaitable(formatted):
            formatted = await formatted
        return dict(formatted)

    async def _persist_tool_invocation_compressed_meta(
        self,
        *,
        output: SkillOutput,
        compressed_meta: dict[str, Any],
    ) -> None:
        if self.skill_manager is None:
            return

        raw_invocation_id = output.metadata.get("invocation_id")
        try:
            invocation_id = int(raw_invocation_id)
        except (TypeError, ValueError):
            return
        if invocation_id <= 0:
            return

        updater = getattr(self.skill_manager, "attach_invocation_compressed_meta", None)
        if updater is None:
            return

        result = updater(
            invocation_id=invocation_id,
            compressed_meta=compressed_meta,
        )
        if inspect.isawaitable(result):
            await result

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

    async def _send_tool_status(
        self,
        *,
        tool_name: str,
        status: str,
        session_id: str,
        error: str = "",
    ) -> None:
        callback = self.on_proactive_message
        if callback is None:
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
            tokens = [
                (_PIPELINE_INTERNAL_SOURCE, _PIPELINE_INTERNAL_SOURCE.set("heartbeat")),
                (_PIPELINE_SUPPRESS_PERSISTENCE, _PIPELINE_SUPPRESS_PERSISTENCE.set(True)),
                (_PIPELINE_SUPPRESS_BROADCAST, _PIPELINE_SUPPRESS_BROADCAST.set(True)),
                (_PIPELINE_SUPPRESS_TOOL_STATUS, _PIPELINE_SUPPRESS_TOOL_STATUS.set(True)),
                (_PIPELINE_SUPPRESS_HISTORY, _PIPELINE_SUPPRESS_HISTORY.set(True)),
            ]

        try:
            async for payload in self.stream_reply(inbound):
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
                message_tag="tool_status",
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
