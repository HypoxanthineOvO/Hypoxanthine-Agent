from __future__ import annotations

import asyncio

import pytest

from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.models import Message


def test_pipeline_calls_router_with_single_user_message() -> None:
    class StubRouter:
        async def call(self, model_name, messages):
            assert model_name == "Gemini3Pro"
            assert messages == [{"role": "user", "content": "你好"}]
            return "你好，我在。"

    pipeline = ChatPipeline(router=StubRouter(), chat_model="Gemini3Pro")
    reply = asyncio.run(
        pipeline.run_once(Message(text="你好", sender="user", session_id="s1"))
    )

    assert reply.sender == "assistant"
    assert reply.text == "你好，我在。"
    assert reply.session_id == "s1"


def test_pipeline_stream_reply_emits_chunk_and_done_events() -> None:
    class StubRouter:
        async def stream(self, model_name, messages):
            assert model_name == "Gemini3Pro"
            assert messages == [{"role": "user", "content": "hello"}]
            yield "He"
            yield "llo"

    pipeline = ChatPipeline(router=StubRouter(), chat_model="Gemini3Pro")

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


def test_pipeline_rejects_empty_text() -> None:
    class StubRouter:
        async def call(self, model_name, messages):
            return "unused"

    pipeline = ChatPipeline(router=StubRouter(), chat_model="Gemini3Pro")

    with pytest.raises(ValueError, match="text"):
        asyncio.run(
            pipeline.run_once(
                Message(text="   ", sender="user", session_id="s1"),
            )
        )
