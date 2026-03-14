from __future__ import annotations

from pathlib import Path
import asyncio

import pytest

from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.memory.structured_store import StructuredStore
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
            assert messages[0]["role"] == "system"
            assert "当前时间:" in messages[0]["content"]
            assert messages[1:] == [
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
            assert messages[0]["role"] == "system"
            assert "当前时间:" in messages[0]["content"]
            assert messages[1:] == [{"role": "user", "content": "hello"}]
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
    assert [event["type"] for event in events] == [
        "assistant_chunk",
        "assistant_chunk",
        "assistant_done",
    ]
    assert [event.get("text") for event in events[:2]] == ["He", "llo"]
    assert all(event["sender"] == "assistant" for event in events)
    assert all(event["session_id"] == "s1" for event in events)
    assert all(str(event["timestamp"]).endswith("Z") for event in events)
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


def test_pipeline_run_once_short_circuits_slash_command() -> None:
    memory = StubSessionMemory()

    class StubRouter:
        def __init__(self) -> None:
            self.calls = 0

        async def call(self, model_name, messages):
            del model_name, messages
            self.calls += 1
            return "LLM should not be called"

    class StubSlashCommands:
        async def try_handle(self, inbound: Message) -> str | None:
            if (inbound.text or "").startswith("/"):
                return "slash ok"
            return None

    router = StubRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        slash_commands=StubSlashCommands(),
    )

    reply = asyncio.run(
        pipeline.run_once(Message(text="/help", sender="user", session_id="s1"))
    )

    assert reply.sender == "assistant"
    assert reply.text == "slash ok"
    assert router.calls == 0
    assert memory.appended == []


class StubBreaker:
    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled

    def set_global_kill_switch(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def get_global_kill_switch(self) -> bool:
        return self._enabled


def test_pipeline_kill_blocks_llm() -> None:
    memory = StubSessionMemory()

    class StubRouter:
        def __init__(self) -> None:
            self.calls = 0

        async def call(self, model_name, messages):
            self.calls += 1
            return "LLM"

    breaker = StubBreaker(enabled=True)
    router = StubRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        circuit_breaker=breaker,
    )

    reply = asyncio.run(pipeline.run_once(Message(text="hello", sender="user", session_id="s1")))

    assert "Kill Switch" in (reply.text or "")
    assert router.calls == 0


def test_pipeline_stream_stops_when_kill_triggered() -> None:
    memory = StubSessionMemory()

    class StubRouter:
        async def stream(self, model_name, messages, *, session_id=None):
            yield "He"
            breaker.set_global_kill_switch(True)
            yield "llo"

    breaker = StubBreaker(enabled=False)
    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        circuit_breaker=breaker,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="hello", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    text = "".join(event.get("text", "") for event in events if event.get("type") == "assistant_chunk")

    assert "Kill Switch" in text
    assert "llo" not in text


def test_system_prompt_contains_time(monkeypatch: pytest.MonkeyPatch) -> None:
    import hypo_agent.core.pipeline as pipeline_module
    from datetime import datetime
    from zoneinfo import ZoneInfo

    fixed = datetime(2026, 3, 10, 0, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed
            return fixed.astimezone(tz)

    monkeypatch.setattr(pipeline_module, "datetime", FixedDatetime)

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

    messages = pipeline._build_llm_messages(Message(text="hi", sender="user", session_id="s1"))
    system_messages = [item for item in messages if item["role"] == "system"]
    assert len(system_messages) == 1
    content = system_messages[0]["content"]
    assert "当前时间:" in content
    assert "2026-03-10T00:15:00+08:00" in content
    assert "(" in content and ")" in content
    tz_name = content.split("(")[-1].split(")")[0].strip()
    assert tz_name


def test_pipeline_broadcasts_reply_for_qq_channel() -> None:
    memory = StubSessionMemory()
    from hypo_agent.core.channel_dispatcher import ChannelDispatcher

    dispatcher = ChannelDispatcher()
    webui_received: list[Message] = []
    qq_received: list[Message] = []

    async def webui_sink(message: Message) -> None:
        webui_received.append(message)

    async def qq_sink(message: Message) -> None:
        qq_received.append(message)

    dispatcher.register("webui", webui_sink)
    dispatcher.register("qq", qq_sink)

    class StubRouter:
        async def stream(self, model_name, messages, *, session_id=None):
            yield "Hi"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        on_proactive_message=dispatcher.broadcast,
    )

    async def _collect() -> None:
        inbound = Message(
            text="hello",
            sender="user",
            session_id="s1",
            channel="qq",
            sender_id="10001",
        )
        async for _ in pipeline.stream_reply(inbound):
            pass

    asyncio.run(_collect())

    assert len(webui_received) == 1
    assert len(qq_received) == 1
    assert webui_received[0].text == "Hi"
    assert webui_received[0].channel == "qq"
    assert qq_received[0].channel == "qq"


def test_pipeline_broadcasts_reply_excluding_qq_for_webui_origin() -> None:
    memory = StubSessionMemory()
    from hypo_agent.core.channel_dispatcher import ChannelDispatcher

    dispatcher = ChannelDispatcher()
    webui_received: list[Message] = []
    qq_received: list[Message] = []

    async def webui_sink(message: Message) -> None:
        webui_received.append(message)

    async def qq_sink(message: Message) -> None:
        qq_received.append(message)

    dispatcher.register("webui", webui_sink)
    dispatcher.register("qq", qq_sink)

    class StubRouter:
        async def stream(self, model_name, messages, *, session_id=None):
            yield "OK"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        on_proactive_message=dispatcher.broadcast,
    )

    async def _collect() -> None:
        inbound = Message(
            text="hello",
            sender="user",
            session_id="s1",
            channel="webui",
        )
        async for _ in pipeline.stream_reply(inbound):
            pass

    asyncio.run(_collect())

    assert len(webui_received) == 1
    assert webui_received[0].text == "OK"
    assert webui_received[0].channel == "webui"
    assert qq_received == []


def test_preference_injection(tmp_path: Path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _seed() -> StructuredStore:
        store = StructuredStore(db_path=db_path)
        await store.init()
        await store.set_preference("喜欢的饮品", "绿茶")
        return store

    store = asyncio.run(_seed())

    memory = StubSessionMemory()

    class StubRouter:
        async def call(self, model_name, messages):
            return "unused"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        structured_store=store,
    )

    messages = pipeline._build_llm_messages(
        Message(text="hi", sender="user", session_id="main"),
        use_tools=True,
    )
    system_messages = [item for item in messages if item["role"] == "system"]
    assert any("User Preferences" in item.get("content", "") for item in system_messages)


def test_pipeline_includes_persona_system_prompt_when_provided() -> None:
    memory = StubSessionMemory()

    class StubRouter:
        async def call(self, model_name, messages):
            del model_name, messages
            return "unused"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        persona_system_prompt="[Persona]\n## 环境信息\n代码仓库：/home/heyx/Hypo-Agent",
    )

    messages = pipeline._build_llm_messages(Message(text="hi", sender="user", session_id="s1"))
    system_messages = [item for item in messages if item["role"] == "system"]

    assert system_messages[0]["content"].startswith("[Persona]")
    assert "## 环境信息" in system_messages[0]["content"]
    assert "/home/heyx/Hypo-Agent" in system_messages[0]["content"]
