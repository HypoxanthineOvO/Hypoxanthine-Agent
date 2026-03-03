from __future__ import annotations

import asyncio

import pytest

from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.models import Message


class StubSessionMemory:
    def __init__(self, history: list[Message] | None = None) -> None:
        self.history = history or []
        self.appended: list[Message] = []

    def get_recent_messages(self, session_id: str, limit: int | None = None) -> list[Message]:
        if limit is None:
            return list(self.history)
        return list(self.history)[-limit:]

    def append(self, message: Message) -> None:
        self.appended.append(message)


def test_pipeline_injects_recent_history_before_inbound() -> None:
    memory = StubSessionMemory(
        history=[
            Message(text="旧问题", sender="user", session_id="s1"),
            Message(text="旧回答", sender="assistant", session_id="s1"),
            Message(text=None, sender="assistant", session_id="s1"),
            Message(text="ignored", sender="system", session_id="s1"),
        ]
    )

    class StubRouter:
        async def call(self, model_name, messages):
            assert model_name == "Gemini3Pro"
            assert messages == [
                {"role": "user", "content": "旧问题"},
                {"role": "assistant", "content": "旧回答"},
                {"role": "user", "content": "新问题"},
            ]
            return "新回答"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
    )
    reply = asyncio.run(
        pipeline.run_once(Message(text="新问题", sender="user", session_id="s1"))
    )

    assert reply.sender == "assistant"
    assert reply.text == "新回答"
    assert reply.session_id == "s1"
    assert [m.sender for m in memory.appended] == ["user", "assistant"]
    assert memory.appended[0].text == "新问题"
    assert memory.appended[1].text == "新回答"


def test_pipeline_stream_reply_emits_chunk_and_done_events_and_persists() -> None:
    memory = StubSessionMemory()

    class StubRouter:
        async def stream(self, model_name, messages, *, session_id=None):
            assert model_name == "Gemini3Pro"
            assert messages == [{"role": "user", "content": "hello"}]
            assert session_id == "s1"
            yield "He"
            yield "llo"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="hello", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    assert events == [
        {
            "type": "assistant_chunk",
            "text": "He",
            "sender": "assistant",
            "session_id": "s1",
        },
        {
            "type": "assistant_chunk",
            "text": "llo",
            "sender": "assistant",
            "session_id": "s1",
        },
        {
            "type": "assistant_done",
            "sender": "assistant",
            "session_id": "s1",
        },
    ]
    assert [m.sender for m in memory.appended] == ["user", "assistant"]
    assert memory.appended[1].text == "Hello"


def test_pipeline_rejects_empty_text() -> None:
    memory = StubSessionMemory()

    class StubRouter:
        async def call(self, model_name, messages):
            return "unused"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
    )

    with pytest.raises(ValueError, match="text"):
        asyncio.run(
            pipeline.run_once(
                Message(text="   ", sender="user", session_id="s1"),
            )
        )
    assert memory.appended == []
