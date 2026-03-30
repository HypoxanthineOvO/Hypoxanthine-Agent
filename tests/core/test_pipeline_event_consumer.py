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
        assert pushed[0].channel == "system"

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
                "summary": "⚠️ 有 1 条提醒疑似漏触发",
            }
        )
        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert len(memory.appended) == 1
        assert memory.appended[0].message_tag == "heartbeat"
        assert "漏触发" in (memory.appended[0].text or "")

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


def test_pipeline_event_consumer_processes_multiple_events() -> None:
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
        for idx in range(3):
            await queue.put(
                {
                    "event_type": "reminder_trigger",
                    "session_id": "main",
                    "title": f"event-{idx}",
                }
            )
            await asyncio.sleep(0.05)

        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert len(memory.appended) == 3
        assert len(pushed) == 3
        assert pushed[0].channel == "system"
        assert "event-0" in (memory.appended[0].text or "")
        assert "event-1" in (memory.appended[1].text or "")
        assert "event-2" in (memory.appended[2].text or "")

    asyncio.run(_run())


def test_pipeline_event_consumer_preserves_target_channels_metadata() -> None:
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
                "event_type": "heartbeat_trigger",
                "session_id": "main",
                "summary": "只发微信",
                "channel": "weixin",
            }
        )
        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert len(memory.appended) == 1
        assert memory.appended[0].metadata["target_channels"] == ["weixin"]
        assert memory.appended[0].metadata["delivery_channel"] == "weixin"
        assert memory.appended[0].metadata["event_source"] == "heartbeat_trigger"
        assert len(pushed) == 1
        assert pushed[0].metadata["target_channels"] == ["weixin"]

    asyncio.run(_run())


def test_pipeline_event_consumer_accepts_feishu_target_channel() -> None:
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
                "session_id": "feishu_oc_chat_123",
                "title": "飞书提醒",
                "channel": "feishu",
            }
        )
        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert len(memory.appended) == 1
        assert memory.appended[0].metadata["target_channels"] == ["feishu"]
        assert memory.appended[0].metadata["delivery_channel"] == "feishu"
        assert len(pushed) == 1
        assert pushed[0].metadata["target_channels"] == ["feishu"]

    asyncio.run(_run())


def test_pipeline_event_consumer_writes_email_scan_message() -> None:
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
                "event_type": "email_scan_trigger",
                "session_id": "main",
                "summary": "🔴 1 封重要邮件；⚪ 2 封普通邮件；📂 5 封归档",
            }
        )
        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert len(memory.appended) == 1
        assert memory.appended[0].message_tag == "email_scan"
        assert "🔴" in (memory.appended[0].text or "")
        assert len(pushed) == 1
        assert pushed[0].message_tag == "email_scan"
        assert pushed[0].channel == "system"

    asyncio.run(_run())


def test_pipeline_event_consumer_writes_trendradar_message() -> None:
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
                "event_type": "trendradar_trigger",
                "session_id": "main",
                "title": "TrendRadar 摘要",
                "summary": "技术：阿里云涨价；财经：云厂商定价",
            }
        )
        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert len(memory.appended) == 1
        assert memory.appended[0].message_tag == "tool_status"
        assert "TrendRadar" in (memory.appended[0].text or "")
        assert len(pushed) == 1
        assert pushed[0].message_tag == "tool_status"

    asyncio.run(_run())


def test_pipeline_event_consumer_processes_user_message_tasks() -> None:
    async def _run() -> None:
        queue = EventQueue()
        memory = StubSessionMemory()
        streamed: list[dict[str, object]] = []

        async def emit(event: dict[str, object]) -> None:
            streamed.append(event)

        pipeline = ChatPipeline(
            router=StubRouter(),
            chat_model="Gemini3Pro",
            session_memory=memory,
            event_queue=queue,
        )

        await pipeline.start_event_consumer()
        await pipeline.enqueue_user_message(
            Message(text="hello", sender="user", session_id="main"),
            emit=emit,
        )
        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert any(str(item.get("type")) == "assistant_chunk" for item in streamed)
        assert any(str(item.get("type")) == "assistant_done" for item in streamed)

    asyncio.run(_run())


def test_pipeline_event_consumer_suppresses_internal_heartbeat_side_effects() -> None:
    async def _run() -> None:
        queue = EventQueue()
        memory = StubSessionMemory()
        pushed: list[Message] = []
        streamed: list[dict[str, object]] = []

        async def emit(event: dict[str, object]) -> None:
            streamed.append(event)

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
        await pipeline.enqueue_user_message(
            Message(
                text="internal heartbeat",
                sender="user",
                session_id="main",
                metadata={"source": "heartbeat"},
            ),
            emit=emit,
        )
        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert any(str(item.get("type")) == "assistant_chunk" for item in streamed)
        assert any(str(item.get("type")) == "assistant_done" for item in streamed)
        assert memory.appended == []
        assert pushed == []

    asyncio.run(_run())
