from __future__ import annotations

from pathlib import Path
import asyncio

import pytest

from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.memory.semantic_memory import ChunkResult
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import Attachment, Message
from hypo_agent.models import SkillOutput
from hypo_agent.core.unified_message import UnifiedMessage


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
            assert messages[1]["role"] == "system"
            assert "[Current Message Context]" in messages[1]["content"]
            assert "当前消息渠道: WebUI (webui)" in messages[1]["content"]
            assert messages[2:] == [
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


def test_pipeline_routes_image_attachments_to_vision_model(tmp_path: Path) -> None:
    memory = StubSessionMemory()
    image_path = tmp_path / "cat.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    class StubRouter:
        def get_model_for_task(self, task_type: str) -> str:
            assert task_type == "vision"
            return "Gpt52"

        async def call(self, model_name, messages, *, session_id=None, tools=None):
            del session_id, tools
            assert model_name == "Gpt52"
            user_message = messages[-1]
            assert user_message["role"] == "user"
            assert isinstance(user_message["content"], list)
            assert user_message["content"][0]["type"] == "text"
            assert user_message["content"][0]["text"] == "这是什么"
            assert user_message["content"][1]["type"] == "image_url"
            assert user_message["content"][1]["image_url"]["url"].startswith(
                "data:image/png;base64,"
            )
            return "是一张图片"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
    )

    reply = asyncio.run(
        pipeline.run_once(
            Message(
                text="这是什么",
                sender="user",
                session_id="s1",
                attachments=[
                    Attachment(
                        type="image",
                        url=str(image_path),
                        filename="cat.png",
                        mime_type="image/png",
                    )
                ],
            )
        )
    )

    assert reply.text == "是一张图片"


def test_pipeline_keeps_text_only_messages_on_default_chat_model() -> None:
    memory = StubSessionMemory()

    class StubRouter:
        async def call(self, model_name, messages, *, session_id=None, tools=None):
            del session_id, tools
            assert model_name == "Gemini3Pro"
            assert messages[-1] == {"role": "user", "content": "普通文本"}
            return "ok"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
    )

    reply = asyncio.run(
        pipeline.run_once(Message(text="普通文本", sender="user", session_id="s1"))
    )

    assert reply.text == "ok"


def test_pipeline_stream_reply_emits_chunk_and_done_events_and_persists() -> None:
    memory = StubSessionMemory()

    class StubRouter:
        async def stream(self, model_name, messages, *, session_id=None):
            assert model_name == "Gemini3Pro"
            assert messages[0]["role"] == "system"
            assert "当前时间:" in messages[0]["content"]
            assert messages[1]["role"] == "system"
            assert "[Current Message Context]" in messages[1]["content"]
            assert "当前消息渠道: WebUI (webui)" in messages[1]["content"]
            assert messages[2:] == [{"role": "user", "content": "hello"}]
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


def test_pipeline_attaches_skill_output_attachments_to_final_message() -> None:
    memory = StubSessionMemory()

    class StubRouter:
        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, messages, tools, session_id
            if not hasattr(self, "called"):
                self.called = 1
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_export",
                            "function": {
                                "name": "export_to_file",
                                "arguments": "{\"content\":\"hello\"}",
                            },
                        }
                    ],
                }
            return {"text": "已导出", "tool_calls": []}

    class StubSkillManager:
        def get_tools_schema(self):
            return [{"type": "function", "function": {"name": "export_to_file"}}]

        async def invoke(self, tool_name, params, *, session_id=None):
            del tool_name, params, session_id
            return SkillOutput(
                status="success",
                result="/tmp/export.pdf",
                attachments=[
                    Attachment(
                        type="file",
                        url="/tmp/export.pdf",
                        filename="export.pdf",
                        mime_type="application/pdf",
                    )
                ],
            )

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        skill_manager=StubSkillManager(),
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="导出", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())

    assert any(event["type"] == "tool_call_result" for event in events)
    assert memory.appended[-1].attachments[0].filename == "export.pdf"


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

    messages = asyncio.run(
        pipeline._build_llm_messages(Message(text="hi", sender="user", session_id="s1"))
    )
    system_messages = [item for item in messages if item["role"] == "system"]
    assert len(system_messages) == 2
    content = system_messages[0]["content"]
    assert "当前时间:" in content
    assert "2026-03-10T00:15:00+08:00" in content
    assert "(" in content and ")" in content
    tz_name = content.split("(")[-1].split(")")[0].strip()
    assert tz_name
    assert "[Current Message Context]" in system_messages[1]["content"]
    assert "当前消息渠道: WebUI (webui)" in system_messages[1]["content"]


def test_pipeline_broadcasts_reply_for_qq_channel() -> None:
    memory = StubSessionMemory()
    from hypo_agent.core.channel_dispatcher import ChannelDispatcher, ChannelRelayPolicy

    dispatcher = ChannelDispatcher()
    relay = ChannelRelayPolicy(dispatcher)
    webui_received: list[Message] = []
    qq_received: list[UnifiedMessage] = []

    async def webui_sink(message: Message) -> None:
        webui_received.append(message)

    async def qq_sink(message: UnifiedMessage) -> None:
        qq_received.append(message)

    dispatcher.register("webui", webui_sink, platform="webui", is_external=False)
    dispatcher.register("qq", qq_sink, platform="qq", is_external=True)

    class StubRouter:
        async def stream(self, model_name, messages, *, session_id=None):
            yield "Hi"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        on_proactive_message=relay.relay_message,
    )

    async def _collect() -> None:
        inbound = Message(
            text="hello",
            sender="user",
            session_id="main",
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
    assert qq_received[0].raw_text == "Hi"


def test_pipeline_broadcasts_reply_to_external_channels_for_webui_origin() -> None:
    memory = StubSessionMemory()
    from hypo_agent.core.channel_dispatcher import ChannelDispatcher, ChannelRelayPolicy

    dispatcher = ChannelDispatcher()
    relay = ChannelRelayPolicy(dispatcher)
    webui_received: list[Message] = []
    qq_received: list[UnifiedMessage] = []

    async def webui_sink(message: Message) -> None:
        webui_received.append(message)

    async def qq_sink(message: UnifiedMessage) -> None:
        qq_received.append(message)

    dispatcher.register("webui", webui_sink, platform="webui", is_external=False)
    dispatcher.register("qq", qq_sink, platform="qq", is_external=True)

    class StubRouter:
        async def stream(self, model_name, messages, *, session_id=None):
            yield "OK"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        on_proactive_message=relay.relay_message,
    )

    async def _collect() -> None:
        inbound = Message(
            text="hello",
            sender="user",
            session_id="main",
            channel="webui",
        )
        async for _ in pipeline.stream_reply(inbound):
            pass

    asyncio.run(_collect())

    assert len(webui_received) == 1
    assert webui_received[0].text == "OK"
    assert webui_received[0].channel == "webui"
    assert len(qq_received) == 1
    assert qq_received[0].raw_text == "OK"


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

    messages = asyncio.run(
        pipeline._build_llm_messages(
            Message(text="hi", sender="user", session_id="main"),
            use_tools=True,
        )
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

    messages = asyncio.run(
        pipeline._build_llm_messages(Message(text="hi", sender="user", session_id="s1"))
    )
    system_messages = [item for item in messages if item["role"] == "system"]

    assert system_messages[0]["content"].startswith("[Persona]")
    assert "## 环境信息" in system_messages[0]["content"]
    assert "/home/heyx/Hypo-Agent" in system_messages[0]["content"]


def test_pipeline_injects_semantic_memory_before_history() -> None:
    memory = StubSessionMemory(
        history=[
            Message(text="旧问题", sender="user", session_id="s1"),
            Message(text="旧回答", sender="assistant", session_id="s1"),
        ]
    )

    class StubSemanticMemory:
        async def search(self, query: str, top_k: int = 5) -> list[ChunkResult]:
            assert query == "新问题"
            assert top_k == 5
            return [
                ChunkResult(
                    file_path="memory/knowledge/persona/user_preferences.md",
                    chunk_text="用户喜欢简洁回复。",
                    score=0.9,
                    chunk_index=0,
                )
            ]

    class StubRouter:
        async def call(self, model_name, messages):
            assert model_name == "Gemini3Pro"
            system_messages = [item for item in messages if item["role"] == "system"]
            assert any("[Current Message Context]" in item["content"] for item in system_messages)
            assert any("[相关记忆]" in item["content"] for item in system_messages)
            assert messages[-3:] == [
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
        semantic_memory=StubSemanticMemory(),
    )

    reply = asyncio.run(pipeline.run_once(Message(text="新问题", sender="user", session_id="s1")))

    assert reply.text == "新回答"


def test_pipeline_injects_current_message_context_for_external_inbound() -> None:
    memory = StubSessionMemory()

    class StubRouter:
        async def call(self, model_name, messages):
            del model_name
            current_context = [
                item["content"]
                for item in messages
                if item["role"] == "system" and "[Current Message Context]" in item["content"]
            ]
            assert len(current_context) == 1
            assert "当前消息渠道: QQ (qq)" in current_context[0]
            assert "当前发送者ID: 10001" in current_context[0]
            assert "入站链路当前是可用的" in current_context[0]
            assert messages[-1] == {"role": "user", "content": "QQ 来的测试"}
            return "ok"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
    )

    reply = asyncio.run(
        pipeline.run_once(
            Message(
                text="QQ 来的测试",
                sender="user",
                session_id="s1",
                channel="qq",
                sender_id="10001",
            )
        )
    )

    assert reply.text == "ok"


def test_pipeline_prefixes_external_history_with_channel_context() -> None:
    memory = StubSessionMemory(
        history=[
            Message(
                text="我从微信发的",
                sender="user",
                session_id="s1",
                channel="weixin",
                sender_id="wx-user-1",
            )
        ]
    )

    class StubRouter:
        async def call(self, model_name, messages):
            del model_name
            assert messages[-2]["role"] == "user"
            assert "[Historical Message Context]" in messages[-2]["content"]
            assert "渠道: 微信 (weixin)" in messages[-2]["content"]
            assert "发送者ID: wx-user-1" in messages[-2]["content"]
            assert messages[-1] == {"role": "user", "content": "继续"}
            return "ok"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
    )

    reply = asyncio.run(
        pipeline.run_once(Message(text="继续", sender="user", session_id="s1"))
    )

    assert reply.text == "ok"


def test_pipeline_skips_semantic_memory_for_heartbeat_messages() -> None:
    memory = StubSessionMemory()

    class StubSemanticMemory:
        async def search(self, query: str, top_k: int = 5) -> list[ChunkResult]:
            raise AssertionError(f"semantic memory should be skipped for heartbeat: {query=} {top_k=}")

    class StubRouter:
        async def call(self, model_name, messages):
            del model_name
            system_messages = [item for item in messages if item["role"] == "system"]
            assert all("[相关记忆]" not in item["content"] for item in system_messages)
            return "heartbeat ok"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        semantic_memory=StubSemanticMemory(),
    )

    reply = asyncio.run(
        pipeline.run_once(
            Message(
                text="heartbeat prompt",
                sender="user",
                session_id="s1",
                channel="system",
                message_tag="heartbeat",
                metadata={"source": "heartbeat", "skip_memory_search": True},
            )
        )
    )

    assert reply.text == "heartbeat ok"


def test_pipeline_marks_sop_usage_after_semantic_hit() -> None:
    memory = StubSessionMemory()

    class StubSemanticMemory:
        async def search(self, query: str, top_k: int = 5) -> list[ChunkResult]:
            assert query == "执行部署"
            assert top_k == 5
            return [
                ChunkResult(
                    file_path="/tmp/memory/knowledge/sop/部署流程.md",
                    chunk_text="标题上下文：SOP: 部署流程 > 步骤\n\n1. 拉代码\n2. 重启服务",
                    score=0.9,
                    chunk_index=0,
                )
            ]

    class StubSopManager:
        def __init__(self) -> None:
            self.touched: list[list[str]] = []

        def is_sop_path(self, file_path: str) -> bool:
            return file_path.endswith("/sop/部署流程.md")

        async def touch_files(self, file_paths: list[str]) -> None:
            self.touched.append(list(file_paths))

    class StubRouter:
        async def call(self, model_name, messages):
            del model_name, messages
            return "按 SOP 执行完成"

    sop_manager = StubSopManager()
    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        semantic_memory=StubSemanticMemory(),
        sop_manager=sop_manager,
    )

    reply = asyncio.run(pipeline.run_once(Message(text="执行部署", sender="user", session_id="s1")))

    assert reply.text == "按 SOP 执行完成"
    assert sop_manager.touched == [["/tmp/memory/knowledge/sop/部署流程.md"]]


def test_pipeline_uses_persona_manager_before_semantic_memory() -> None:
    memory = StubSessionMemory()

    class StubSemanticMemory:
        async def search(self, query: str, top_k: int = 5) -> list[ChunkResult]:
            del query, top_k
            return []

    class StubPersonaManager:
        async def get_system_prompt_section(self, query: str | None = None) -> str:
            assert query == "hi"
            return "[Persona]\n你是 Hypo。"

    class StubRouter:
        async def call(self, model_name, messages):
            del model_name
            assert messages[0] == {"role": "system", "content": "[Persona]\n你是 Hypo。"}
            return "unused"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        persona_manager=StubPersonaManager(),
        semantic_memory=StubSemanticMemory(),
    )

    asyncio.run(pipeline.run_once(Message(text="hi", sender="user", session_id="s1")))
