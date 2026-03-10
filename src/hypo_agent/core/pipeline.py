from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from hypo_agent.core.config_loader import load_runtime_model_config
from hypo_agent.core.model_router import ModelRouter
from hypo_agent.models import Message


class ChatModelRouter(Protocol):
    async def call(self, model_name: str, messages: list[dict[str, Any]]) -> str: ...

    async def stream(
        self,
        model_name: str,
        messages: list[dict[str, Any]],
    ) -> AsyncIterator[str]: ...


class ChatPipeline:
    def __init__(self, router: ChatModelRouter, chat_model: str) -> None:
        self.router = router
        self.chat_model = chat_model

    async def run_once(self, inbound: Message) -> Message:
        llm_messages = self._build_llm_messages(inbound)
        text = await self.router.call(self.chat_model, llm_messages)
        return Message(
            text=text,
            sender="assistant",
            session_id=inbound.session_id,
        )

    async def stream_reply(self, inbound: Message) -> AsyncIterator[dict[str, Any]]:
        llm_messages = self._build_llm_messages(inbound)
        async for chunk in self.router.stream(self.chat_model, llm_messages):
            if not chunk:
                continue
            yield {
                "type": "assistant_chunk",
                "text": chunk,
                "sender": "assistant",
                "session_id": inbound.session_id,
            }
        yield {
            "type": "assistant_done",
            "sender": "assistant",
            "session_id": inbound.session_id,
        }

    def _build_llm_messages(self, inbound: Message) -> list[dict[str, str]]:
        text = (inbound.text or "").strip()
        if not text:
            raise ValueError("text is required for M2 chat pipeline")
        return [{"role": "user", "content": text}]


def build_default_pipeline() -> ChatPipeline:
    runtime_config = load_runtime_model_config()
    router = ModelRouter(runtime_config)
    chat_model = runtime_config.task_routing.get("chat", runtime_config.default_model)
    return ChatPipeline(router=router, chat_model=chat_model)
