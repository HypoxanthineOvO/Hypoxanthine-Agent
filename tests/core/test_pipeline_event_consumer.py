from __future__ import annotations

import asyncio

from hypo_agent.core.event_queue import EventQueue
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.models import Message


class StubRouter:
    async def call(self, model_name, messages, *, session_id=None, tools=None):
        del model_name, messages, session_id, tools
        return "ok"

    async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
        del model_name, messages, tools, session_id
        return {"text": "ok", "tool_calls": []}

    async def stream(self, model_name, messages, *, session_id=None, tools=None):
        del model_name, messages, session_id, tools
        yield "ok"


class StubSessionMemory:
    def __init__(self) -> None:
        self.appended: list[Message] = []

    def append(self, message: Message) -> None:
        self.appended.append(message)

    def get_recent_messages(self, session_id: str, limit: int | None = None) -> list[Message]:
        del session_id, limit
        return []


class FlakySessionMemory(StubSessionMemory):
    def __init__(self) -> None:
        super().__init__()
        self._append_calls = 0

    def append(self, message: Message) -> None:
        self._append_calls += 1
        if self._append_calls == 1:
            raise RuntimeError("append failed for first event")
        super().append(message)


def test_pipeline_event_consumer_persists_and_broadcasts_reminder() -> None:
    async def _run() -> None:
        queue = EventQueue()
        memory = StubSessionMemory()
        pushed: list[Message] = []

        async def on_proactive_message(message: Message) -> None:
            pushed.append(message)

        pipeline = ChatPipeline(
            router=StubRouter(),
            chat_model="Gemini3Pro",
            session_memory=memory,
            event_queue=queue,
            on_proactive_message=on_proactive_message,
        )

        await pipeline.start_event_consumer()
        await queue.put(
            {
                "event_type": "reminder_trigger",
                "session_id": "main",
                "title": "喝水",
                "description": "该喝水了",
            }
        )
        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert len(memory.appended) == 1
        assert memory.appended[0].session_id == "main"
        assert memory.appended[0].message_tag == "reminder"
        assert "喝水" in (memory.appended[0].text or "")
        assert len(pushed) == 1
        assert pushed[0].message_tag == "reminder"

    asyncio.run(_run())


def test_pipeline_event_consumer_writes_heartbeat_without_callback() -> None:
    async def _run() -> None:
        queue = EventQueue()
        memory = StubSessionMemory()
        pipeline = ChatPipeline(
            router=StubRouter(),
            chat_model="Gemini3Pro",
            session_memory=memory,
            event_queue=queue,
        )

        await pipeline.start_event_consumer()
        await queue.put(
            {
                "event_type": "heartbeat_trigger",
                "session_id": "main",
                "title": "服务巡检",
                "description": "发现异常",
            }
        )
        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert len(memory.appended) == 1
        assert memory.appended[0].message_tag == "heartbeat"
        assert "服务巡检" in (memory.appended[0].text or "")

    asyncio.run(_run())


def test_pipeline_event_consumer_continues_after_append_exception() -> None:
    async def _run() -> None:
        queue = EventQueue()
        memory = FlakySessionMemory()
        pipeline = ChatPipeline(
            router=StubRouter(),
            chat_model="Gemini3Pro",
            session_memory=memory,
            event_queue=queue,
        )

        await pipeline.start_event_consumer()
        await queue.put(
            {
                "event_type": "reminder_trigger",
                "session_id": "main",
                "title": "first",
            }
        )
        await asyncio.sleep(0.05)
        await queue.put(
            {
                "event_type": "reminder_trigger",
                "session_id": "main",
                "title": "second",
            }
        )
        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert len(memory.appended) == 1
        assert "second" in (memory.appended[0].text or "")

    asyncio.run(_run())
