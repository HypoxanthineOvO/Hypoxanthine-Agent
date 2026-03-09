from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import inspect
import json
from typing import Any, Protocol

import structlog

from hypo_agent.core.channel_adapter import ChannelAdapter, WebUIAdapter
from hypo_agent.core.rich_response import RichResponse
from hypo_agent.memory.session import SessionMemory
from hypo_agent.models import Message, SkillOutput

logger = structlog.get_logger()

TOOL_USE_SYSTEM_PROMPT = (
    "You are an assistant with access to tools. "
    "When the user asks you to execute a command or run code, you MUST use "
    "the provided tools instead of describing the action in text. "
    "Always prefer using tools over explaining what you would do."
)


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
        max_react_rounds: int = 5,
        slash_commands: SlashCommands | None = None,
        output_compressor: ChatOutputCompressor | None = None,
        channel_adapter: ChannelAdapter | None = None,
        event_queue: Any | None = None,
        on_proactive_message: Any | None = None,
    ) -> None:
        self.router = router
        self.chat_model = chat_model
        self.session_memory = session_memory
        self.history_window = history_window
        self.skill_manager = skill_manager
        self.max_react_rounds = max_react_rounds
        self.slash_commands = slash_commands
        self.output_compressor = output_compressor
        self.channel_adapter = channel_adapter or WebUIAdapter()
        self.event_queue = event_queue
        self.on_proactive_message = on_proactive_message
        self._event_consumer_task: asyncio.Task[None] | None = None

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

    async def run_once(self, inbound: Message) -> Message:
        slash_result = await self._try_handle_slash(inbound)
        if slash_result is not None:
            return Message(
                text=slash_result,
                sender="assistant",
                session_id=inbound.session_id,
            )

        llm_messages = self._build_llm_messages(inbound)
        self.session_memory.append(inbound)
        text = await self.router.call(self.chat_model, llm_messages)
        outbound = Message(
            text=text,
            sender="assistant",
            session_id=inbound.session_id,
        )
        self.session_memory.append(outbound)
        return outbound

    async def stream_reply(self, inbound: Message) -> AsyncIterator[dict[str, Any]]:
        slash_result = await self._try_handle_slash(inbound)
        if slash_result is not None:
            yield self._format_event(
                event_type="assistant_chunk",
                response=RichResponse(text=slash_result),
                session_id=inbound.session_id,
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
        llm_messages = self._build_llm_messages(inbound, use_tools=use_tools)
        self.session_memory.append(inbound)

        full_text = ""

        if use_tools:
            assert self.skill_manager is not None
            tools = self.skill_manager.get_tools_schema()
            react_messages: list[dict[str, Any]] = list(llm_messages)
            reached_round_limit = True
            logger.info(
                "react.start",
                session_id=inbound.session_id,
                max_rounds=self.max_react_rounds,
            )

            for round_num in range(1, self.max_react_rounds + 1):
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
                            if not chunk:
                                continue
                            full_text += chunk
                            yield self._format_event(
                                event_type="assistant_chunk",
                                response=RichResponse(text=chunk),
                                session_id=inbound.session_id,
                            )
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

                    output = await self.skill_manager.invoke(
                        tool_name,
                        arguments,
                        session_id=inbound.session_id,
                    )
                    serialized_output = json.dumps(
                        output.model_dump(mode="json"),
                        ensure_ascii=False,
                    )
                    tool_content = serialized_output
                    tool_result_for_event: Any = output.result
                    tool_metadata_for_event = dict(output.metadata)
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
                if not chunk:
                    continue
                full_text += chunk
                yield self._format_event(
                    event_type="assistant_chunk",
                    response=RichResponse(text=chunk),
                    session_id=inbound.session_id,
                )

        outbound = Message(
            text=full_text,
            sender="assistant",
            session_id=inbound.session_id,
        )
        self.session_memory.append(outbound)
        yield self._format_event(
            event_type="assistant_done",
            response=RichResponse(),
            session_id=inbound.session_id,
        )

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

    def _build_llm_messages(
        self,
        inbound: Message,
        *,
        use_tools: bool = False,
    ) -> list[dict[str, str]]:
        text = (inbound.text or "").strip()
        if not text:
            raise ValueError("text is required for M2 chat pipeline")

        llm_messages: list[dict[str, str]] = []
        if use_tools:
            llm_messages.append({"role": "system", "content": TOOL_USE_SYSTEM_PROMPT})

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

    async def _consume_event_loop(self) -> None:
        assert self.event_queue is not None
        while True:
            event: dict[str, Any] = await self.event_queue.get()
            try:
                message = self._event_to_message(event)
                if message is None:
                    continue
                self.session_memory.append(message)
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

    def _event_to_message(self, event: dict[str, Any]) -> Message | None:
        event_type = str(event.get("event_type") or "").strip().lower()
        session_id = str(event.get("session_id") or "main")
        title = str(event.get("title") or "").strip()
        description = str(event.get("description") or "").strip()

        if event_type == "reminder_trigger":
            text = f"🔔 提醒：{title}" if title else "🔔 提醒"
            if description:
                text += f"\n{description}"
            return Message(
                text=text,
                sender="assistant",
                session_id=session_id,
                message_tag="reminder",
            )

        if event_type == "heartbeat_trigger":
            text = f"🔔 Heartbeat 异常：{title}" if title else "🔔 Heartbeat 异常"
            if description:
                text += f"\n{description}"
            return Message(
                text=text,
                sender="assistant",
                session_id=session_id,
                message_tag="heartbeat",
            )

        return None
