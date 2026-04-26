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


class ControlledPipeline(ChatPipeline):
    def __init__(self, *args, blockers: dict[str, asyncio.Event] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.blockers = blockers or {}
        self.started: list[str] = []
        self.started_texts: list[str] = []
        self.finished: list[str] = []

    async def stream_reply(self, inbound: Message, **kwargs):
        del kwargs
        self.started.append(inbound.session_id)
        self.started_texts.append(str(inbound.text or ""))
        blocker = self.blockers.get(inbound.session_id)
        if blocker is not None:
            await blocker.wait()
        self.finished.append(inbound.session_id)
        yield {
            "type": "assistant_chunk",
            "content": f"ok:{inbound.session_id}:{inbound.text}",
            "session_id": inbound.session_id,
        }
        yield {"type": "assistant_done", "session_id": inbound.session_id}


class HangingPipeline(ChatPipeline):
    async def stream_reply(self, inbound: Message, **kwargs):
        del inbound, kwargs
        await asyncio.sleep(10)
        if False:  # pragma: no cover
            yield {}


class ErrorPipeline(ChatPipeline):
    async def stream_reply(self, inbound: Message, **kwargs):
        del inbound, kwargs
        raise RuntimeError("upstream failed")
        if False:  # pragma: no cover
            yield {}


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


def test_pipeline_event_consumer_writes_hypo_info_message() -> None:
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
                "event_type": "hypo_info_trigger",
                "session_id": "main",
                "title": "Hypo-Info 摘要",
                "summary": "AI：模型更新",
            }
        )
        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert len(memory.appended) == 1
        assert memory.appended[0].message_tag == "hypo_info"
        assert "Hypo-Info" in (memory.appended[0].text or "")
        assert len(pushed) == 1
        assert pushed[0].message_tag == "hypo_info"

    asyncio.run(_run())


def test_pipeline_event_consumer_writes_wewe_rss_message() -> None:
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
                "event_type": "wewe_rss_trigger",
                "session_id": "main",
                "summary": "WeWe RSS 微信读书账号已失效：reader-a",
                "channel": "qq",
            }
        )
        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert len(memory.appended) == 1
        assert memory.appended[0].message_tag == "tool_status"
        assert "WeWe RSS" in (memory.appended[0].text or "")
        assert memory.appended[0].metadata["target_channels"] == ["qq"]
        assert len(pushed) == 1
        assert pushed[0].metadata["target_channels"] == ["qq"]

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


def test_nonblocking_runtime_slow_session_does_not_block_other_session() -> None:
    async def _run() -> None:
        queue = EventQueue()
        blocker = asyncio.Event()
        emitted_a: list[dict[str, object]] = []
        emitted_b: list[dict[str, object]] = []

        pipeline = ControlledPipeline(
            router=StubRouter(),
            chat_model="Gemini3Pro",
            session_memory=StubSessionMemory(),
            event_queue=queue,
            blockers={"A": blocker},
            nonblocking_runtime_enabled=True,
            max_concurrent_work_items=2,
        )

        async def emit_a(event: dict[str, object]) -> None:
            emitted_a.append(dict(event))

        async def emit_b(event: dict[str, object]) -> None:
            emitted_b.append(dict(event))

        await pipeline.start_event_consumer()
        work_a = await pipeline.enqueue_user_message(
            Message(text="slow", sender="user", session_id="A"),
            emit=emit_a,
        )
        while "A" not in pipeline.started:
            await asyncio.sleep(0.01)
        work_b = await pipeline.enqueue_user_message(
            Message(text="fast", sender="user", session_id="B"),
            emit=emit_b,
        )
        for _ in range(50):
            if any(item.get("type") == "assistant_done" for item in emitted_b):
                break
            await asyncio.sleep(0.01)
        assert any(item.get("type") == "assistant_done" for item in emitted_b)
        assert not any(item.get("type") == "assistant_done" for item in emitted_a)
        assert work_a != work_b
        assert pipeline.get_work_status(work_b)["status"] == "done"

        blocker.set()
        for _ in range(50):
            if pipeline.get_work_status(work_a)["status"] == "done":
                break
            await asyncio.sleep(0.01)
        await pipeline.stop_event_consumer()
        assert pipeline.get_work_status(work_a)["status"] == "done"

    asyncio.run(_run())


def test_nonblocking_runtime_preserves_same_session_order_and_queued_status() -> None:
    async def _run() -> None:
        queue = EventQueue()
        blocker = asyncio.Event()
        first_events: list[dict[str, object]] = []
        second_events: list[dict[str, object]] = []
        pipeline = ControlledPipeline(
            router=StubRouter(),
            chat_model="Gemini3Pro",
            session_memory=StubSessionMemory(),
            event_queue=queue,
            blockers={"same": blocker},
            nonblocking_runtime_enabled=True,
            max_concurrent_work_items=2,
        )

        async def emit_first(event: dict[str, object]) -> None:
            first_events.append(dict(event))

        async def emit_second(event: dict[str, object]) -> None:
            second_events.append(dict(event))

        await pipeline.start_event_consumer()
        first_id = await pipeline.enqueue_user_message(
            Message(text="first", sender="user", session_id="same"),
            emit=emit_first,
        )
        while pipeline.get_work_status(first_id)["status"] != "running":
            await asyncio.sleep(0.01)
        second_id = await pipeline.enqueue_user_message(
            Message(text="second", sender="user", session_id="same"),
            emit=emit_second,
        )
        for _ in range(50):
            if pipeline.get_work_status(second_id)["status"] == "queued":
                break
            await asyncio.sleep(0.01)
        assert pipeline.get_work_status(second_id)["status"] == "queued"
        assert pipeline.started == ["same"]

        blocker.set()
        for _ in range(50):
            if pipeline.get_work_status(second_id)["status"] == "done":
                break
            await asyncio.sleep(0.01)
        await pipeline.stop_event_consumer()
        assert pipeline.started == ["same", "same"]
        assert first_events[0]["status"] == "queued"
        assert any(item.get("status") == "queued" for item in second_events)

    asyncio.run(_run())


def test_nonblocking_runtime_cancel_emits_terminal_status_and_releases_capacity() -> None:
    async def _run() -> None:
        queue = EventQueue()
        emitted: list[dict[str, object]] = []
        pipeline = HangingPipeline(
            router=StubRouter(),
            chat_model="Gemini3Pro",
            session_memory=StubSessionMemory(),
            event_queue=queue,
            nonblocking_runtime_enabled=True,
            max_concurrent_work_items=1,
        )

        async def emit(event: dict[str, object]) -> None:
            emitted.append(dict(event))

        await pipeline.start_event_consumer()
        work_id = await pipeline.enqueue_user_message(
            Message(text="cancel me", sender="user", session_id="cancel"),
            emit=emit,
        )
        while pipeline.get_work_status(work_id)["status"] != "running":
            await asyncio.sleep(0.01)
        assert await pipeline.cancel_work(work_id) is True
        for _ in range(50):
            if pipeline.get_work_status(work_id)["status"] == "cancelled":
                break
            await asyncio.sleep(0.01)
        await pipeline.stop_event_consumer()

        assert pipeline.get_work_status(work_id)["status"] == "cancelled"
        assert any(
            item.get("type") == "work_status"
            and item.get("status") == "cancelled"
            and item.get("terminal") is True
            for item in emitted
        )

    asyncio.run(_run())


def test_nonblocking_runtime_prioritizes_user_message_before_scheduled_message() -> None:
    async def _run() -> None:
        queue = EventQueue()
        emitted: list[dict[str, object]] = []
        pipeline = ControlledPipeline(
            router=StubRouter(),
            chat_model="Gemini3Pro",
            session_memory=StubSessionMemory(),
            event_queue=queue,
            nonblocking_runtime_enabled=True,
            max_concurrent_work_items=1,
        )

        async def emit(event: dict[str, object]) -> None:
            emitted.append(dict(event))

        await pipeline.enqueue_user_message(
            Message(
                text="scheduled heartbeat",
                sender="user",
                session_id="prio",
                metadata={"source": "heartbeat"},
            ),
            emit=emit,
        )
        await pipeline.enqueue_user_message(
            Message(text="human message", sender="user", session_id="prio"),
            emit=emit,
        )
        await pipeline.start_event_consumer()
        for _ in range(80):
            if pipeline.started_texts == ["human message", "scheduled heartbeat"]:
                break
            await asyncio.sleep(0.01)
        await pipeline.stop_event_consumer()

        assert pipeline.started_texts == ["human message", "scheduled heartbeat"]
        assert any(item.get("status") == "done" for item in emitted)

    asyncio.run(_run())


def test_nonblocking_runtime_timeout_emits_terminal_status() -> None:
    async def _run() -> None:
        queue = EventQueue()
        emitted: list[dict[str, object]] = []
        pipeline = HangingPipeline(
            router=StubRouter(),
            chat_model="Gemini3Pro",
            session_memory=StubSessionMemory(),
            event_queue=queue,
            nonblocking_runtime_enabled=True,
            user_message_timeout_seconds=0.05,
        )

        async def emit(event: dict[str, object]) -> None:
            emitted.append(dict(event))

        await pipeline.start_event_consumer()
        work_id = await pipeline.enqueue_user_message(
            Message(text="timeout", sender="user", session_id="timeout"),
            emit=emit,
        )
        for _ in range(80):
            if pipeline.get_work_status(work_id)["status"] == "timeout":
                break
            await asyncio.sleep(0.01)
        await pipeline.stop_event_consumer()

        assert pipeline.get_work_status(work_id)["status"] == "timeout"
        assert any(
            item.get("type") == "error"
            and item.get("code") == "USER_MESSAGE_TIMEOUT"
            and item.get("session_id") == "timeout"
            for item in emitted
        )
        assert any(
            item.get("type") == "work_status"
            and item.get("status") == "timeout"
            and item.get("terminal") is True
            for item in emitted
        )

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


def test_pipeline_event_consumer_emits_error_for_unhandled_user_message_exception() -> None:
    class ExplodingRouter(StubRouter):
        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise ValueError("bad upstream payload")
            yield ""  # pragma: no cover

    async def _run() -> None:
        queue = EventQueue()
        memory = StubSessionMemory()
        streamed: list[dict[str, object]] = []

        async def emit(event: dict[str, object]) -> None:
            streamed.append(event)

        pipeline = ChatPipeline(
            router=ExplodingRouter(),
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

        assert streamed[-1] == {
            "type": "error",
            "code": "LLM_RUNTIME_ERROR",
            "message": "LLM 调用失败，请检查配置或稍后重试",
            "retryable": True,
            "session_id": "main",
        }

    asyncio.run(_run())


def test_nonblocking_runtime_upstream_error_marks_work_error() -> None:
    async def _run() -> None:
        queue = EventQueue()
        emitted: list[dict[str, object]] = []
        pipeline = ErrorPipeline(
            router=StubRouter(),
            chat_model="Gemini3Pro",
            session_memory=StubSessionMemory(),
            event_queue=queue,
            nonblocking_runtime_enabled=True,
        )

        async def emit(event: dict[str, object]) -> None:
            emitted.append(dict(event))

        await pipeline.start_event_consumer()
        work_id = await pipeline.enqueue_user_message(
            Message(text="boom", sender="user", session_id="err"),
            emit=emit,
        )
        for _ in range(50):
            if pipeline.get_work_status(work_id)["status"] == "error":
                break
            await asyncio.sleep(0.01)
        await pipeline.stop_event_consumer()

        assert pipeline.get_work_status(work_id)["status"] == "error"
        assert any(item.get("type") == "error" for item in emitted)
        assert any(
            item.get("type") == "work_status"
            and item.get("status") == "error"
            and item.get("terminal") is True
            for item in emitted
        )

    asyncio.run(_run())



def test_pipeline_event_consumer_forwards_progress_events_to_emit_callback() -> None:
    async def _run() -> None:
        queue = EventQueue()
        memory = StubSessionMemory()
        emitted: list[dict[str, object]] = []

        class SkillManager:
            def get_tools_schema(self) -> list[dict]:
                return [{"type": "function", "function": {"name": "exec_command"}}]

            async def invoke(self, tool_name: str, params: dict, *, session_id=None, skill_name=None):
                del tool_name, params, session_id, skill_name
                from hypo_agent.models import SkillOutput
                return SkillOutput(status="success", result="ok")

        class Router:
            def __init__(self) -> None:
                self.calls = 0

            async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
                del model_name, messages, tools, session_id
                self.calls += 1
                if self.calls == 1:
                    return {
                        "text": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "exec_command",
                                    "arguments": '{"command":"echo hi"}',
                                },
                            }
                        ],
                    }
                return {"text": "done", "tool_calls": []}

            async def stream(self, model_name, messages, *, session_id=None, tools=None):
                del model_name, messages, session_id, tools
                if False:  # pragma: no cover
                    yield ""

        pipeline = ChatPipeline(
            router=Router(),
            chat_model="Gemini3Pro",
            session_memory=memory,
            event_queue=queue,
            skill_manager=SkillManager(),
        )

        async def emit(event: dict[str, object]) -> None:
            emitted.append(dict(event))

        await pipeline.start_event_consumer()
        await queue.put(
            {
                "event_type": "user_message",
                "message": Message(text="run", sender="user", session_id="main"),
                "emit": emit,
            }
        )
        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert any(item.get("type") == "pipeline_stage" for item in emitted)
        assert any(item.get("type") == "react_iteration" for item in emitted)
        assert any(item.get("type") == "assistant_done" for item in emitted)

    asyncio.run(_run())
