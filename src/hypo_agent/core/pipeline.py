from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from hypo_agent.memory.session import SessionMemory
from hypo_agent.models import Message


class ChatModelRouter(Protocol):
    async def call(self, model_name: str, messages: list[dict[str, Any]]) -> str: ...

    async def stream(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
        *,
        session_id: str | None = None,
    ) -> AsyncIterator[str]: ...


class ChatPipeline:
    def __init__(
        self,
        router: ChatModelRouter,
        chat_model: str,
        session_memory: SessionMemory,
        history_window: int = 20,
    ) -> None:
        self.router = router
        self.chat_model = chat_model
        self.session_memory = session_memory
        self.history_window = history_window

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

        full_reply: list[str] = []
        async for chunk in self.router.stream(
            self.chat_model,
            llm_messages,
            session_id=inbound.session_id,
        ):
            if not chunk:
                continue
            full_reply.append(chunk)
            yield {
                "type": "assistant_chunk",
                "text": chunk,
                "sender": "assistant",
                "session_id": inbound.session_id,
            }

        outbound = Message(
            text="".join(full_reply),
            sender="assistant",
            session_id=inbound.session_id,
        )
        self.session_memory.append(outbound)
        yield {
            "type": "assistant_done",
            "sender": "assistant",
            "session_id": inbound.session_id,
        }

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
