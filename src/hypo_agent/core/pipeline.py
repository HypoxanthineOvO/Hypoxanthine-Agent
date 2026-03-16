from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextvars import ContextVar
from datetime import datetime
import inspect
import json
from time import perf_counter
from typing import Any, Protocol

import structlog

from hypo_agent.core.channel_adapter import ChannelAdapter, WebUIAdapter
from hypo_agent.core.rich_response import RichResponse
from hypo_agent.core.time_utils import utc_isoformat, utc_now
from hypo_agent.memory.semantic_memory import ChunkResult, estimate_token_count
from hypo_agent.memory.session import SessionMemory
from hypo_agent.models import Message, SkillOutput

logger = structlog.get_logger()

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


class ChatModelRouter(Protocol):
    async def call(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> str: ...

    async def call_with_tools(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def stream(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        session_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]: ...


class ChatSkillManager(Protocol):
    def get_tools_schema(self) -> list[dict[str, Any]]: ...

    async def invoke(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        session_id: str | None = None,
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
        max_react_rounds: int = 5,
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
    ) -> None:
        self.router = router
        self.chat_model = chat_model
        self.session_memory = session_memory
        self.history_window = history_window
        self.skill_manager = skill_manager
        self.structured_store = structured_store
        self.circuit_breaker = circuit_breaker
        self.max_react_rounds = max_react_rounds
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
        return source == "heartbeat"

    def _session_persistence_suppressed(self) -> bool:
        return bool(_PIPELINE_SUPPRESS_PERSISTENCE.get())

    def _broadcast_suppressed(self) -> bool:
        return bool(_PIPELINE_SUPPRESS_BROADCAST.get())

    def _tool_status_context_suppressed(self) -> bool:
        return bool(_PIPELINE_SUPPRESS_TOOL_STATUS.get())

    def _history_suppressed(self) -> bool:
        return bool(_PIPELINE_SUPPRESS_HISTORY.get())

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

        llm_messages = await self._build_llm_messages(inbound)
        self._append_session_message(inbound)
        text = await self.router.call(self.chat_model, llm_messages)
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
            slash_result = await self._try_handle_slash(inbound)
            if slash_result is not None:
                yield self._format_event(
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
                yield self._format_event(
                    event_type="assistant_done",
                    response=RichResponse(),
                    session_id=inbound.session_id,
                )
                return

            if self._kill_switch_active():
                self._append_session_message(inbound)
                kill_text = KILL_SWITCH_MESSAGE
                yield self._format_event(
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
                yield self._format_event(
                    event_type="assistant_done",
                    response=RichResponse(),
                    session_id=inbound.session_id,
                )
                return

            use_tools = (
                self.skill_manager is not None
                and self.max_react_rounds > 0
            )
            llm_messages = await self._build_llm_messages(inbound, use_tools=use_tools)
            self._append_session_message(inbound)

            full_text = ""
            killed = False
            session_fused = False
            last_compressed_meta: dict[str, Any] | None = None

            if use_tools:
                assert self.skill_manager is not None
                tools = self.skill_manager.get_tools_schema()
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
                    max_rounds=self.max_react_rounds,
                )
                logger.debug(
                    "react.tools",
                    session_id=inbound.session_id,
                    tool_names=tool_names,
                    tool_count=len(tool_names),
                )

                for round_num in range(1, self.max_react_rounds + 1):
                    if self._kill_switch_active():
                        full_text = KILL_SWITCH_MESSAGE
                        yield self._format_event(
                            event_type="assistant_chunk",
                            response=RichResponse(text=full_text),
                            session_id=inbound.session_id,
                        )
                        killed = True
                        break
                    decision = await self.router.call_with_tools(
                        self.chat_model,
                        react_messages,
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
                            yield self._format_event(
                                event_type="assistant_chunk",
                                response=RichResponse(text=text),
                                session_id=inbound.session_id,
                            )
                        else:
                            async for chunk in self.router.stream(
                                self.chat_model,
                                react_messages,
                                session_id=inbound.session_id,
                                tools=tools,
                            ):
                                if self._kill_switch_active():
                                    full_text = KILL_SWITCH_MESSAGE
                                    yield self._format_event(
                                        event_type="assistant_chunk",
                                        response=RichResponse(text=full_text),
                                        session_id=inbound.session_id,
                                    )
                                    killed = True
                                    break
                                if not chunk:
                                    continue
                                full_text += chunk
                                yield self._format_event(
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
                        arguments = self._parse_tool_arguments(tool_call)
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
                        yield self._format_event(
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
                            )
                        except Exception as exc:
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
                        serialized_output = json.dumps(
                            output.model_dump(mode="json"),
                            ensure_ascii=False,
                        )
                        tool_content = serialized_output
                        tool_result_for_event: Any = output.result
                        tool_metadata_for_event = dict(output.metadata)
                        tool_metadata_for_event["ephemeral"] = True
                        compressed_meta_for_event: dict[str, Any] | None = None
                        if self.output_compressor is not None:
                            compression_metadata: dict[str, Any] = {
                                "session_id": inbound.session_id,
                                "tool_name": tool_name,
                                "tool_call_id": tool_call_id,
                            }
                            tool_content, was_compressed = await self.output_compressor.compress_if_needed(
                                serialized_output,
                                compression_metadata,
                            )
                            if was_compressed:
                                tool_result_for_event = tool_content
                                tool_metadata_for_event["compressed"] = True
                                tool_metadata_for_event["original_chars"] = len(serialized_output)
                                tool_metadata_for_event["compressed_chars"] = len(tool_content)
                                compressed_meta = compression_metadata.get("compressed_meta")
                                if isinstance(compressed_meta, dict):
                                    compressed_meta_for_event = dict(compressed_meta)
                                    last_compressed_meta = dict(compressed_meta)
                                    await self._persist_tool_invocation_compressed_meta(
                                        output=output,
                                        compressed_meta=compressed_meta_for_event,
                                    )
                        yield self._format_event(
                            event_type="tool_call_result",
                            response=RichResponse(
                                compressed_meta=compressed_meta_for_event,
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
                            yield self._format_event(
                                event_type="assistant_chunk",
                                response=RichResponse(text=full_text),
                                session_id=inbound.session_id,
                            )
                            break
                    if session_fused:
                        break

                if reached_round_limit:
                    logger.warning("react.round_limit", session_id=inbound.session_id)
                    full_text = "Stopped due to max ReAct rounds limit."
                    yield self._format_event(
                        event_type="assistant_chunk",
                        response=RichResponse(text=full_text),
                        session_id=inbound.session_id,
                    )
            else:
                async for chunk in self.router.stream(
                    self.chat_model,
                    llm_messages,
                    session_id=inbound.session_id,
                ):
                    if self._kill_switch_active():
                        full_text = KILL_SWITCH_MESSAGE
                        yield self._format_event(
                            event_type="assistant_chunk",
                            response=RichResponse(text=full_text),
                            session_id=inbound.session_id,
                        )
                        killed = True
                        break
                    if not chunk:
                        continue
                    full_text += chunk
                    yield self._format_event(
                        event_type="assistant_chunk",
                        response=RichResponse(text=chunk),
                        session_id=inbound.session_id,
                    )

            if session_fused:
                outbound = Message(
                    text=full_text,
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
                yield self._format_event(
                    event_type="assistant_done",
                    response=RichResponse(),
                    session_id=inbound.session_id,
                )
                return

            if killed:
                outbound = Message(
                    text=full_text,
                    sender="assistant",
                    session_id=inbound.session_id,
                    channel=inbound.channel,
                    sender_id=inbound.sender_id,
                )
                self._append_session_message(outbound)
                await self._broadcast_message(outbound, origin_channel=inbound.channel)
                yield self._format_event(
                    event_type="assistant_done",
                    response=RichResponse(),
                    session_id=inbound.session_id,
                )
                return

            extra_chunk = self._apply_compression_marker(
                text=full_text,
                compressed_meta=last_compressed_meta,
            )
            if extra_chunk is not None:
                full_text += extra_chunk
                yield self._format_event(
                    event_type="assistant_chunk",
                    response=RichResponse(text=extra_chunk),
                    session_id=inbound.session_id,
                )

            outbound = Message(
                text=full_text,
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
            yield self._format_event(
                event_type="assistant_done",
                response=RichResponse(),
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
    ) -> list[dict[str, str]]:
        text = (inbound.text or "").strip()
        if not text:
            raise ValueError("text is required for M2 chat pipeline")

        llm_messages: list[dict[str, str]] = []
        persona_prompt = await self._resolve_persona_prompt(text)
        if persona_prompt:
            llm_messages.append({"role": "system", "content": persona_prompt})
        if use_tools:
            llm_messages.append({"role": "system", "content": TOOL_USE_SYSTEM_PROMPT})
        llm_messages.append({"role": "system", "content": self._system_time_context()})
        prefs_context = self._preferences_context()
        if prefs_context:
            llm_messages.append({"role": "system", "content": prefs_context})
        semantic_context = await self._semantic_memory_context(text)
        if semantic_context:
            llm_messages.append({"role": "system", "content": semantic_context})

        if not self._history_suppressed():
            history = self.session_memory.get_recent_messages(
                inbound.session_id,
                limit=self.history_window,
            )
            for item in history:
                llm_item = self._to_llm_message(item)
                if llm_item is not None:
                    llm_messages.append(llm_item)

        llm_messages.append({"role": "user", "content": text})
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

    async def _semantic_memory_context(self, query: str) -> str:
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
        except Exception:
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
            except Exception:
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
        except Exception:
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
        except Exception:
            return ""

        if not rows:
            return ""

        lines = ["[User Preferences]"]
        for key, value in rows[:20]:
            if not key:
                continue
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    def _to_llm_message(self, message: Message) -> dict[str, str] | None:
        text = (message.text or "").strip()
        if not text:
            return None

        if message.sender == "user":
            role = "user"
        elif message.sender == "assistant":
            role = "assistant"
        else:
            return None
        return {"role": role, "content": text}

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
        exclude_channels: set[str] | None = None
        exclude_client_ids: set[str] | None = None
        channel = str(origin_channel or "").strip().lower()
        if channel == "webui":
            exclude_channels = {"qq"}
            if origin_client_id:
                exclude_client_ids = {origin_client_id}
        try:
            result = callback(
                message,
                exclude_channels=exclude_channels,
                exclude_client_ids=exclude_client_ids,
            )
        except TypeError:
            try:
                result = callback(message, exclude_channels=exclude_channels)
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

    def _format_event(
        self,
        *,
        event_type: str,
        response: RichResponse,
        session_id: str,
    ) -> dict[str, Any]:
        return self.channel_adapter.format(
            response,
            event_type=event_type,
            session_id=session_id,
        )

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
            except Exception:
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
                except Exception:
                    logger.exception(
                        "narration.callback.failed",
                        session_id=inbound.session_id,
                        tool_name=tool_name,
                    )
                    return
            except Exception:
                logger.exception(
                    "narration.callback.failed",
                    session_id=inbound.session_id,
                    tool_name=tool_name,
                )
                return
            if inspect.isawaitable(result):
                try:
                    await result
                except Exception:
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
        except TimeoutError:
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
        except RuntimeError:
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
            )

        return None
