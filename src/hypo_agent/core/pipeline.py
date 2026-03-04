from __future__ import annotations

from collections.abc import AsyncIterator
import json
from typing import Any, Protocol

import structlog

from hypo_agent.memory.session import SessionMemory
from hypo_agent.models import Message, SkillOutput

logger = structlog.get_logger()


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
    ) -> Any: ...


class ChatPipeline:
    def __init__(
        self,
        router: ChatModelRouter,
        chat_model: str,
        session_memory: SessionMemory,
        history_window: int = 20,
        skill_manager: ChatSkillManager | None = None,
        max_react_rounds: int = 5,
    ) -> None:
        self.router = router
        self.chat_model = chat_model
        self.session_memory = session_memory
        self.history_window = history_window
        self.skill_manager = skill_manager
        self.max_react_rounds = max_react_rounds

    async def run_once(self, inbound: Message) -> Message:
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
        llm_messages = self._build_llm_messages(inbound)
        self.session_memory.append(inbound)

        full_text = ""
        use_tools = (
            self.skill_manager is not None
            and hasattr(self.router, "call_with_tools")
            and self.max_react_rounds > 0
        )

        if use_tools:
            assert self.skill_manager is not None
            tools = self.skill_manager.get_tools_schema()
            react_messages: list[dict[str, Any]] = list(llm_messages)
            reached_round_limit = True

            for round_num in range(1, self.max_react_rounds + 1):
                decision = await self.router.call_with_tools(
                    self.chat_model,
                    react_messages,
                    tools=tools,
                    session_id=inbound.session_id,
                )
                tool_calls = decision.get("tool_calls") or []
                del round_num
                if not tool_calls:
                    reached_round_limit = False
                    async for chunk in self.router.stream(
                        self.chat_model,
                        react_messages,
                        session_id=inbound.session_id,
                        tools=tools,
                    ):
                        if not chunk:
                            continue
                        full_text += chunk
                        yield {
                            "type": "assistant_chunk",
                            "text": chunk,
                            "sender": "assistant",
                            "session_id": inbound.session_id,
                        }
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
                    yield {
                        "type": "tool_call_start",
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "arguments": arguments,
                        "session_id": inbound.session_id,
                    }
                    output = await self.skill_manager.invoke(
                        tool_name,
                        arguments,
                        session_id=inbound.session_id,
                    )
                    yield {
                        "type": "tool_call_result",
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "status": output.status,
                        "result": output.result,
                        "error_info": output.error_info,
                        "metadata": output.metadata,
                        "session_id": inbound.session_id,
                    }
                    react_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": json.dumps(output.model_dump(mode="json"), ensure_ascii=False),
                        }
                    )

            if reached_round_limit:
                full_text = "Stopped due to max ReAct rounds limit."
                yield {
                    "type": "assistant_chunk",
                    "text": full_text,
                    "sender": "assistant",
                    "session_id": inbound.session_id,
                }
        else:
            async for chunk in self.router.stream(
                self.chat_model,
                llm_messages,
                session_id=inbound.session_id,
            ):
                if not chunk:
                    continue
                full_text += chunk
                yield {
                    "type": "assistant_chunk",
                    "text": chunk,
                    "sender": "assistant",
                    "session_id": inbound.session_id,
                }

        outbound = Message(
            text=full_text,
            sender="assistant",
            session_id=inbound.session_id,
        )
        self.session_memory.append(outbound)
        yield {
            "type": "assistant_done",
            "sender": "assistant",
            "session_id": inbound.session_id,
        }

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

    def _build_llm_messages(self, inbound: Message) -> list[dict[str, str]]:
        text = (inbound.text or "").strip()
        if not text:
            raise ValueError("text is required for M2 chat pipeline")

        llm_messages: list[dict[str, str]] = []
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
