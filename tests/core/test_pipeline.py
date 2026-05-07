from __future__ import annotations

from pathlib import Path
import asyncio

import pytest

from hypo_agent.core.channel_progress import summarize_channel_progress_event
from hypo_agent.core.config_loader import RuntimeModelConfig
from hypo_agent.core.model_router import ModelRouter
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.core.skill_catalog import SkillManifest
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
            assert "## 当前运行环境" in messages[1]["content"]
            assert messages[2]["role"] == "system"
            assert "[Current Message Context]" in messages[2]["content"]
            assert "当前消息渠道: WebUI (webui)" in messages[2]["content"]
            assert messages[3]["role"] == "system"
            assert "answer the user's direct request and then stop" in messages[3]["content"]
            assert messages[4:] == [
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


def test_pipeline_history_budget_skips_system_messages_and_keeps_recent_dialogue() -> None:
    memory = StubSessionMemory(
        history=[
            Message(
                text="🔔 reminder should be skipped " + ("r" * 800),
                sender="assistant",
                session_id="s1",
                channel="system",
                message_tag="reminder",
            ),
            Message(text="第一轮用户 " + ("a" * 700), sender="user", session_id="s1"),
            Message(text="第一轮助手 " + ("b" * 700), sender="assistant", session_id="s1"),
            Message(
                text="💓 heartbeat should be skipped " + ("h" * 800),
                sender="assistant",
                session_id="s1",
                channel="system",
                message_tag="heartbeat",
            ),
            Message(text="第二轮用户 " + ("c" * 700), sender="user", session_id="s1"),
            Message(text="第二轮助手 " + ("d" * 700), sender="assistant", session_id="s1"),
            Message(text="第三轮用户 " + ("e" * 700), sender="user", session_id="s1"),
            Message(text="第三轮助手 " + ("f" * 700), sender="assistant", session_id="s1"),
        ]
    )

    class StubRouter:
        async def call(self, model_name, messages):
            del model_name, messages
            return "unused"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=100,
        history_token_budget=900,
    )

    messages = asyncio.run(
        pipeline._build_llm_messages(Message(text="新的问题", sender="user", session_id="s1"))
    )
    history_messages = [item for item in messages if item["role"] in {"user", "assistant"}]
    history_text = "\n".join(str(item["content"]) for item in history_messages)

    assert "should be skipped" not in history_text
    assert "第三轮用户" in history_text
    assert "第三轮助手" in history_text
    assert "第一轮用户" not in history_text


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


def test_pipeline_confirms_pending_notion_todo_binding_without_llm(tmp_path: Path) -> None:
    memory = StubSessionMemory()
    store = StructuredStore(db_path=tmp_path / "agent.db")
    asyncio.run(
        store.set_preference(
            "notion.todo_database_candidate_pending",
            (
                '{"database_id":"todo-db-discovered","title":"HYX的计划通",'
                '"url":"https://www.notion.so/todo-db-discovered"}'
            ),
        )
    )

    class StubRouter:
        async def call(self, model_name, messages, *, session_id=None, tools=None):
            raise AssertionError("LLM should not be called for notion todo binding confirmation")

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        structured_store=store,
        history_window=20,
    )

    reply = asyncio.run(
        pipeline.run_once(
            Message(text="确认绑定 HYX的计划通", sender="user", session_id="s1")
        )
    )

    assert "已绑定 Notion 待办数据库" in str(reply.text)
    assert asyncio.run(store.get_preference("notion.todo_database_id")) == "todo-db-discovered"
    assert asyncio.run(store.get_preference("notion.todo_database_candidate_pending")) is None


def test_pipeline_shortcuts_notion_todo_request_without_llm() -> None:
    memory = StubSessionMemory()

    class StubSkillManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, str | None, str | None]] = []

        def get_tools_schema(self) -> list[dict]:
            return [{"type": "function", "function": {"name": "get_notion_todo_snapshot"}}]

        async def invoke(
            self,
            tool_name: str,
            params: dict,
            *,
            session_id: str | None = None,
            skill_name: str | None = None,
        ) -> SkillOutput:
            self.calls.append((tool_name, params, session_id, skill_name))
            return SkillOutput(
                status="success",
                result={
                    "available": True,
                    "human_summary": "今日到期未完成：\n- 提交周报",
                },
            )

    class StubRouter:
        async def call(self, model_name, messages, *, session_id=None, tools=None):
            raise AssertionError("LLM should not be called for notion todo shortcut")

    skill_manager = StubSkillManager()
    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skill_manager,
        history_window=20,
    )

    reply = asyncio.run(
        pipeline.run_once(
            Message(text="查看一下今天的计划通待办事项", sender="user", session_id="s1")
        )
    )

    assert reply.text == "今日到期未完成：\n- 提交周报"
    assert skill_manager.calls == [("get_notion_todo_snapshot", {}, "s1", "direct")]


def test_pipeline_shortcuts_notion_todo_followup_after_binding_without_llm() -> None:
    memory = StubSessionMemory(
        history=[
            Message(text="查看一下今天的计划通待办事项", sender="user", session_id="s1"),
            Message(
                text="已绑定 Notion 待办数据库：HYX 的计划通（ID: db-1）。后续 heartbeat 将直接使用这个数据库。",
                sender="assistant",
                session_id="s1",
            ),
        ]
    )

    class StubSkillManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, str | None, str | None]] = []

        def get_tools_schema(self) -> list[dict]:
            return [{"type": "function", "function": {"name": "get_notion_todo_snapshot"}}]

        async def invoke(
            self,
            tool_name: str,
            params: dict,
            *,
            session_id: str | None = None,
            skill_name: str | None = None,
        ) -> SkillOutput:
            self.calls.append((tool_name, params, session_id, skill_name))
            return SkillOutput(
                status="success",
                result={
                    "available": True,
                    "human_summary": "今日到期未完成：\n- 提交周报",
                },
            )

    class StubRouter:
        async def call(self, model_name, messages, *, session_id=None, tools=None):
            raise AssertionError("LLM should not be called for notion todo follow-up shortcut")

    skill_manager = StubSkillManager()
    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skill_manager,
        history_window=20,
    )

    reply = asyncio.run(
        pipeline.run_once(
            Message(text="好，查看吧", sender="user", session_id="s1")
        )
    )

    assert reply.text == "今日到期未完成：\n- 提交周报"
    assert skill_manager.calls == [("get_notion_todo_snapshot", {}, "s1", "direct")]


def test_pipeline_shortcuts_wewe_login_request_without_llm(tmp_path: Path) -> None:
    memory = StubSessionMemory()
    pushed: list[Message] = []

    class StubWeWeMonitor:
        def is_login_request(self, text: str | None) -> bool:
            return str(text or "").strip() == "我扫码登录一下"

        async def start_login_flow(self, *, session_id: str, channel: str, sender_id: str | None = None) -> Message:
            return Message(
                text="请扫码登录 WeWe RSS。",
                sender="assistant",
                session_id=session_id,
                channel=channel,
                sender_id=sender_id,
                attachments=[
                    Attachment(
                        type="image",
                        url=str(tmp_path / "wewe.png"),
                        filename="wewe.png",
                        mime_type="image/png",
                    )
                ],
                message_tag="tool_status",
                metadata={"target_channels": [channel]},
            )

    class StubRouter:
        async def call(self, model_name, messages, *, session_id=None, tools=None):
            raise AssertionError("LLM should not be called for WeWe QR shortcut")

    async def on_proactive_message(message: Message, **_: object) -> None:
        pushed.append(message)

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        on_proactive_message=on_proactive_message,
        wewe_rss_monitor=StubWeWeMonitor(),
    )

    reply = asyncio.run(
        pipeline.run_once(
            Message(text="我扫码登录一下", sender="user", session_id="s1", channel="qq", sender_id="u1")
        )
    )

    assert reply.text == "请扫码登录 WeWe RSS。"
    assert reply.attachments[0].filename == "wewe.png"
    assert reply.metadata["target_channels"] == ["qq"]
    assert [m.sender for m in memory.appended] == ["user", "assistant"]
    assert pushed[-1].metadata["target_channels"] == ["qq"]


def test_pipeline_stream_reply_emits_chunk_and_done_events_and_persists() -> None:
    memory = StubSessionMemory()

    class StubRouter:
        async def stream(self, model_name, messages, *, session_id=None):
            assert model_name == "Gemini3Pro"
            assert messages[0]["role"] == "system"
            assert "当前时间:" in messages[0]["content"]
            assert messages[1]["role"] == "system"
            assert "## 当前运行环境" in messages[1]["content"]
            assert messages[2]["role"] == "system"
            assert "[Current Message Context]" in messages[2]["content"]
            assert "当前消息渠道: WebUI (webui)" in messages[2]["content"]
            assert messages[3]["role"] == "system"
            assert "answer the user's direct request and then stop" in messages[3]["content"]
            assert messages[4:] == [{"role": "user", "content": "hello"}]
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
    assert all(str(event["timestamp"]).endswith("+08:00") for event in events)
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

        async def invoke(self, tool_name, params, *, session_id=None, skill_name=None):
            del tool_name, params, session_id, skill_name
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


def test_pipeline_invokes_tools_with_direct_skill_name() -> None:
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
                            "id": "call_echo",
                            "function": {
                                "name": "echo",
                                "arguments": "{\"text\":\"hello\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

    class RecordingSkillManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, str | None, str | None]] = []

        def get_tools_schema(self):
            return [{"type": "function", "function": {"name": "echo"}}]

        async def invoke(self, tool_name, params, *, session_id=None, skill_name=None):
            self.calls.append((tool_name, params, session_id, skill_name))
            return SkillOutput(status="success", result={"echo": params["text"]})

    skills = RecordingSkillManager()
    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        skill_manager=skills,
    )

    async def _collect() -> None:
        inbound = Message(text="say hello", sender="user", session_id="s1")
        async for _ in pipeline.stream_reply(inbound):
            pass

    asyncio.run(_collect())

    assert skills.calls == [("echo", {"text": "hello"}, "s1", "direct")]


def test_pipeline_preserves_reasoning_content_for_follow_up_tool_rounds() -> None:
    memory = StubSessionMemory()
    captured_messages: list[list[dict[str, object]]] = []

    class StubRouter:
        def __init__(self) -> None:
            self.called = 0

        async def call(self, model_name, messages, **kwargs):
            del model_name, messages, kwargs
            return "unused"

        async def call_with_tools(
            self,
            model_name,
            messages,
            *,
            tools=None,
            session_id=None,
            **kwargs,
        ):
            del model_name, tools, session_id, kwargs
            self.called += 1
            captured_messages.append(messages)
            if self.called == 1:
                tool_calls = [
                    {
                        "id": "call_echo",
                        "function": {
                            "name": "echo",
                            "arguments": "{\"text\":\"hello\"}",
                        },
                    }
                ]
                return {
                    "text": "",
                    "tool_calls": tool_calls,
                    "assistant_message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "Need to call echo before answering.",
                        "tool_calls": tool_calls,
                    },
                }
            assistant_messages = [
                message for message in messages if message.get("role") == "assistant"
            ]
            assert assistant_messages[-1]["reasoning_content"] == "Need to call echo before answering."
            return {"text": "done", "tool_calls": []}

    class RecordingSkillManager:
        def get_tools_schema(self):
            return [{"type": "function", "function": {"name": "echo"}}]

        async def invoke(self, tool_name, params, *, session_id=None, skill_name=None):
            del session_id, skill_name
            assert tool_name == "echo"
            return SkillOutput(status="success", result={"echo": params["text"]})

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="DeepSeekV4",
        session_memory=memory,
        history_window=20,
        skill_manager=RecordingSkillManager(),
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="say hello", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())

    assert any(event["type"] == "assistant_chunk" and event.get("text") == "done" for event in events)
    assert any(event["type"] == "assistant_done" for event in events)
    assert len(captured_messages) == 2


def test_pipeline_injects_skill_instructions_and_exec_profile() -> None:
    memory = StubSessionMemory()

    class StubCatalog:
        def match_candidates(self, user_message: str):
            assert "git" in user_message
            return [
                SkillManifest(
                    name="git-workflow",
                    description="Git workflow",
                    category="pure",
                    path=Path("/tmp/git-workflow"),
                    allowed_tools=["exec_command"],
                    backend="exec",
                    exec_profile="git",
                    triggers=["git"],
                    risk="low",
                    dependencies=["git"],
                    compatibility="linux",
                )
            ]

        def load_body(self, skill_name: str) -> str:
            assert skill_name == "git-workflow"
            return "Run git status first."

    class StubRouter:
        def __init__(self) -> None:
            self.messages: list[dict] | None = None

        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, tools, session_id
            self.messages = messages
            if not hasattr(self, "called"):
                self.called = 1
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_exec",
                            "function": {
                                "name": "exec_command",
                                "arguments": "{\"command\":\"git status --short\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

    class RecordingSkillManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, str | None, str | None]] = []

        def get_tools_schema(self):
            return [{"type": "function", "function": {"name": "exec_command"}}]

        async def invoke(self, tool_name, params, *, session_id=None, skill_name=None):
            self.calls.append((tool_name, params, session_id, skill_name))
            return SkillOutput(status="success", result={"stdout": "ok"})

    router = StubRouter()
    skills = RecordingSkillManager()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        skill_manager=skills,
        skill_catalog=StubCatalog(),
    )

    async def _collect() -> None:
        inbound = Message(text="please inspect git changes", sender="user", session_id="s1")
        async for _ in pipeline.stream_reply(inbound):
            pass

    asyncio.run(_collect())

    assert any(
        message["role"] == "system" and "Skill instructions" in str(message["content"])
        for message in (router.messages or [])
    )
    assert skills.calls == [
        (
            "exec_command",
            {"command": "git status --short", "exec_profile": "git"},
            "s1",
            "git-workflow",
        )
    ]


def test_pipeline_routes_matched_skill_turns_to_reasoning_model() -> None:
    memory = StubSessionMemory()
    captured: dict[str, object] = {}

    class StubCatalog:
        def match_candidates(self, user_message: str):
            assert "git" in user_message
            return [
                SkillManifest(
                    name="git-workflow",
                    description="Git workflow",
                    category="pure",
                    path=Path("/tmp/git-workflow"),
                    allowed_tools=["exec_command"],
                    backend="exec",
                    exec_profile="git",
                    triggers=["git"],
                    risk="low",
                    dependencies=["git"],
                    compatibility="linux",
                )
            ]

        def load_body(self, skill_name: str) -> str:
            assert skill_name == "git-workflow"
            return "Run git status first."

    class StubRouter:
        def get_model_for_task(self, task_type: str) -> str:
            if task_type == "reasoning":
                return "Reasoner"
            return "Gemini3Pro"

        async def call_with_tools(
            self,
            model_name,
            messages,
            *,
            tools=None,
            session_id=None,
            task_type=None,
        ):
            del messages, tools, session_id
            captured["model_name"] = model_name
            captured["task_type"] = task_type
            return {"text": "done", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None, task_type=None):
            del model_name, messages, session_id, tools, task_type
            if False:  # pragma: no cover
                yield ""

    class StubSkillManager:
        def get_tools_schema(self):
            return [{"type": "function", "function": {"name": "exec_command"}}]

        async def invoke(self, tool_name, params, *, session_id=None, skill_name=None):
            del tool_name, params, session_id, skill_name
            return SkillOutput(status="success", result="unused")

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        skill_manager=StubSkillManager(),
        skill_catalog=StubCatalog(),
    )

    async def _collect() -> None:
        inbound = Message(text="please inspect git changes", sender="user", session_id="s1")
        async for _ in pipeline.stream_reply(inbound):
            pass

    asyncio.run(_collect())

    assert captured == {"model_name": "Reasoner", "task_type": "reasoning"}


def test_pipeline_filters_tools_to_core_and_matched_skill_candidates() -> None:
    memory = StubSessionMemory()
    captured: dict[str, list[str]] = {}

    class StubCatalog:
        def match_candidates(self, user_message: str):
            assert "git" in user_message
            return [
                SkillManifest(
                    name="git-workflow",
                    description="Git workflow",
                    category="pure",
                    path=Path("/tmp/git-workflow"),
                    allowed_tools=["exec_command"],
                    backend="exec",
                    exec_profile="git",
                    triggers=["git"],
                    risk="low",
                    dependencies=["git"],
                    compatibility="linux",
                )
            ]

        def load_body(self, skill_name: str) -> str:
            assert skill_name == "git-workflow"
            return "Run git status first."

    class StubRouter:
        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, messages, session_id
            captured["tool_names"] = [tool["function"]["name"] for tool in tools or []]
            return {"text": "done", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            if False:  # pragma: no cover
                yield ""

    class StubSkillManager:
        def get_tools_schema(self):
            return [
                {"type": "function", "function": {"name": "exec_command"}},
                {"type": "function", "function": {"name": "read_file"}},
                {"type": "function", "function": {"name": "create_reminder"}},
                {"type": "function", "function": {"name": "notion_query_db"}},
            ]

        async def invoke(self, tool_name, params, *, session_id=None, skill_name=None):
            del tool_name, params, session_id, skill_name
            return SkillOutput(status="success", result="unused")

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=100,
        skill_manager=StubSkillManager(),
        skill_catalog=StubCatalog(),
    )

    async def _collect() -> None:
        inbound = Message(text="please inspect git changes", sender="user", session_id="s1")
        async for _ in pipeline.stream_reply(inbound):
            pass

    asyncio.run(_collect())

    assert captured["tool_names"] == ["exec_command", "read_file", "create_reminder"]


def test_pipeline_injects_cli_json_metadata_into_exec_command() -> None:
    memory = StubSessionMemory()

    class StubCatalog:
        def match_candidates(self, user_message: str):
            assert "json cli" in user_message
            return [
                SkillManifest(
                    name="external-cli",
                    description="JSON CLI workflow",
                    category="pure",
                    path=Path("/tmp/external-cli"),
                    allowed_tools=["exec_command"],
                    backend="exec",
                    exec_profile="cli-json",
                    triggers=["json cli"],
                    risk="low",
                    dependencies=["json-cli"],
                    compatibility="linux",
                    cli_package="@acme/json-cli",
                    cli_commands=["json-cli"],
                    io_format="json-stdio",
                )
            ]

        def load_body(self, skill_name: str) -> str:
            assert skill_name == "external-cli"
            return "Use the JSON CLI."

    class StubRouter:
        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, messages, tools, session_id
            if not hasattr(self, "called"):
                self.called = 1
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_exec",
                            "function": {
                                "name": "exec_command",
                                "arguments": "{\"command\":\"json-cli ping\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

    class RecordingSkillManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, str | None, str | None]] = []

        def get_tools_schema(self):
            return [{"type": "function", "function": {"name": "exec_command"}}]

        async def invoke(self, tool_name, params, *, session_id=None, skill_name=None):
            self.calls.append((tool_name, params, session_id, skill_name))
            return SkillOutput(status="success", result={"stdout": '{"ok":true}'})

    skills = RecordingSkillManager()
    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        skill_manager=skills,
        skill_catalog=StubCatalog(),
    )

    async def _collect() -> None:
        inbound = Message(text="please run the json cli", sender="user", session_id="s1")
        async for _ in pipeline.stream_reply(inbound):
            pass

    asyncio.run(_collect())

    assert skills.calls == [
        (
            "exec_command",
            {
                "command": "json-cli ping",
                "exec_profile": "cli-json",
                "allowed_commands": ["json-cli"],
                "io_format": "json-stdio",
            },
            "s1",
            "external-cli",
        )
    ]


def test_pipeline_invokes_tools_with_direct_skill_name() -> None:
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
                            "id": "call_echo",
                            "function": {
                                "name": "echo",
                                "arguments": "{\"text\":\"hello\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

    class RecordingSkillManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, str | None, str | None]] = []

        def get_tools_schema(self):
            return [{"type": "function", "function": {"name": "echo"}}]

        async def invoke(self, tool_name, params, *, session_id=None, skill_name=None):
            self.calls.append((tool_name, params, session_id, skill_name))
            return SkillOutput(status="success", result={"echo": params["text"]})

    skills = RecordingSkillManager()
    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        skill_manager=skills,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="say hello", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    asyncio.run(_collect())

    assert skills.calls == [("echo", {"text": "hello"}, "s1", "direct")]


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


def test_pipeline_run_once_appends_codex_status_bar_for_attached_task() -> None:
    memory = StubSessionMemory()

    class StubRouter:
        async def call(self, model_name, messages):
            del model_name, messages
            return "regular answer"

    class StubCoderTaskService:
        async def get_attached_task(self, session_id: str) -> dict[str, object] | None:
            assert session_id == "s1"
            return {"task_id": "task-123", "status": "running"}

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        coder_task_service=StubCoderTaskService(),
    )

    reply = asyncio.run(
        pipeline.run_once(Message(text="hello", sender="user", session_id="s1"))
    )

    assert reply.text is not None
    assert "regular answer" in reply.text
    assert "🤖 Codex · task-123 | ⏳ RUNNING" in reply.text
    assert "/codex send" in reply.text


def test_pipeline_run_once_skips_codex_status_bar_for_slash_command() -> None:
    memory = StubSessionMemory()

    class StubRouter:
        async def call(self, model_name, messages):
            del model_name, messages
            return "unused"

    class StubSlashCommands:
        async def try_handle(self, inbound: Message) -> str | None:
            if (inbound.text or "").startswith("/"):
                return "slash ok"
            return None

    class StubCoderTaskService:
        async def get_attached_task(self, session_id: str) -> dict[str, object] | None:
            del session_id
            return {"task_id": "task-123", "status": "running"}

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        slash_commands=StubSlashCommands(),
        coder_task_service=StubCoderTaskService(),
    )

    reply = asyncio.run(
        pipeline.run_once(Message(text="/help", sender="user", session_id="s1"))
    )

    assert reply.text == "slash ok"


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
    monkeypatch.setattr(pipeline_module, "now_local", lambda: fixed)

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
    assert len(system_messages) == 4
    content = system_messages[0]["content"]
    assert "当前时间:" in content
    assert "2026年03月10日 00:15 (Tuesday)" in content
    assert "Asia/Shanghai" in content
    assert "时区:" in content
    runtime_content = system_messages[1]["content"]
    assert "## 当前运行环境" in runtime_content
    assert "当前模型: Gemini3Pro" in runtime_content
    assert "路由类型: chat" in runtime_content
    assert "[Current Message Context]" in system_messages[2]["content"]
    assert "当前消息渠道: WebUI (webui)" in system_messages[2]["content"]
    assert "answer the user's direct request and then stop" in system_messages[3]["content"]


def test_system_prompt_contains_model_info() -> None:
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
    )

    messages = asyncio.run(
        pipeline._build_llm_messages(Message(text="hi", sender="user", session_id="s1"))
    )
    system_messages = [item["content"] for item in messages if item["role"] == "system"]
    runtime_content = next(item for item in system_messages if "## 当前运行环境" in item)

    assert "当前模型: Gemini3Pro (Gemini3Pro)" in runtime_content
    assert "路由类型: chat" in runtime_content
    assert "服务器时间:" in runtime_content


def test_system_prompt_updates_on_fallback() -> None:
    memory = StubSessionMemory()
    captured_messages: list[list[dict[str, object]]] = []

    runtime_config = RuntimeModelConfig.model_validate(
        {
            "default_model": "Gemini3Pro",
            "task_routing": {"chat": "Gemini3Pro"},
            "models": {
                "Gemini3Pro": {
                    "provider": "Hiapi",
                    "litellm_model": "openai/gemini-2.5-pro",
                    "fallback": "DeepseekV3_2",
                    "api_base": "https://hiapi.online/v1",
                    "api_key": "sk-hiapi",
                },
                "DeepseekV3_2": {
                    "provider": "Volcengine",
                    "litellm_model": "openai/ep-20251215171209-4z5qk",
                    "fallback": None,
                    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
                    "api_key": "volc-key",
                },
            },
        }
    )

    async def fake_acompletion(**kwargs):
        captured_messages.append(kwargs["messages"])
        if kwargs["model"] == "openai/gemini-2.5-pro":
            raise TimeoutError("primary timeout")
        return {
            "choices": [{"message": {"content": "fallback ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
    )

    reply = asyncio.run(pipeline.run_once(Message(text="hi", sender="user", session_id="s1")))

    assert reply.text == "fallback ok"
    first_runtime = next(
        item["content"]
        for item in captured_messages[0]
        if item["role"] == "system" and "## 当前运行环境" in str(item["content"])
    )
    second_runtime = next(
        item["content"]
        for item in captured_messages[1]
        if item["role"] == "system" and "## 当前运行环境" in str(item["content"])
    )
    assert "当前模型: Gemini3Pro (openai/gemini-2.5-pro)" in str(first_runtime)
    assert "备用模型" not in str(first_runtime)
    assert "当前模型: DeepseekV3_2 (openai/ep-20251215171209-4z5qk)" in str(second_runtime)
    assert "主模型 Gemini3Pro 暂时不可用" in str(second_runtime)


def test_successful_fallback_is_not_visible_on_external_channel() -> None:
    text, prelude_sent = summarize_channel_progress_event(
        {
            "type": "model_fallback",
            "failed_model": "GPT-5.4",
            "reason": "API timeout",
            "fallback_model": "EdenQwen",
        }
    )

    assert text is None
    assert prelude_sent is False


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


def test_pipeline_persists_slash_command_conversation() -> None:
    memory = StubSessionMemory()

    class StubRouter:
        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, messages, tools, session_id
            raise AssertionError("LLM should not be called for slash commands")

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called for slash commands")
            yield ""  # pragma: no cover

    class StubSlashCommands:
        async def try_handle(self, inbound: Message) -> str | None:
            if (inbound.text or "").startswith("/codex"):
                return "Codex 任务已提交\ntask_id=task-123\nstatus=running\n目录：/tmp/repo"
            return None

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        slash_commands=StubSlashCommands(),
    )

    async def _collect() -> None:
        inbound = Message(
            text="/codex 检查仓库 --dir /tmp/repo",
            sender="user",
            session_id="main",
            channel="qq",
            sender_id="10001",
        )
        async for _ in pipeline.stream_reply(inbound):
            pass

    asyncio.run(_collect())

    assert len(memory.appended) == 2
    assert memory.appended[0].text == "/codex 检查仓库 --dir /tmp/repo"
    assert memory.appended[0].sender == "user"
    assert memory.appended[1].sender == "assistant"
    assert "Codex 任务已提交" in str(memory.appended[1].text)


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
    assert any("High Priority User Preferences" in item.get("content", "") for item in system_messages)


def test_pipeline_prefers_typed_prompt_memory_and_filters_runtime_state(tmp_path: Path) -> None:
    async def _seed() -> StructuredStore:
        store = StructuredStore(db_path=tmp_path / "hypo.db")
        await store.init()
        await store.set_preference("auth.pending.zhihu", "legacy auth state")
        await store.save_memory_item(
            memory_class="interaction_policy",
            key="reply_boundary",
            value="答完直接结束，不要追加反问",
            source="test",
            language="zh",
        )
        await store.save_memory_item(
            memory_class="operational_state",
            key="email_scan.cursor",
            value="cursor-1",
            source="test",
            language="zh",
        )
        return store

    class StubRouter:
        async def call(self, model_name, messages):
            del model_name, messages
            return "unused"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=StubSessionMemory(),
        history_window=20,
        structured_store=asyncio.run(_seed()),
    )

    context = pipeline._preferences_context()

    assert "reply_boundary: 答完直接结束，不要追加反问" in context
    assert "email_scan.cursor" not in context
    assert "auth.pending.zhihu" not in context


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


def test_pipeline_tool_prompt_tells_model_not_to_guess_file_permission_denials() -> None:
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
    )

    messages = asyncio.run(
        pipeline._build_llm_messages(
            Message(text="帮我读一下 /tmp/a.txt", sender="user", session_id="s1"),
            use_tools=True,
        )
    )
    system_messages = [item["content"] for item in messages if item["role"] == "system"]
    tool_prompt = next(item for item in system_messages if "assistant with access to tools" in item)

    assert "do not assume permission is missing before trying" in tool_prompt
    assert "read_file or list_directory first" in tool_prompt
    assert "Only tell the user that access is denied after a tool actually returns a permission error" in tool_prompt


def test_pipeline_places_high_priority_preferences_after_semantic_memory(tmp_path: Path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _seed() -> StructuredStore:
        store = StructuredStore(db_path=db_path)
        await store.init()
        await store.set_preference("reply_boundary", "答完直接结束，不要追加反问")
        return store

    class StubSemanticMemory:
        async def search(self, query: str, top_k: int = 5) -> list[ChunkResult]:
            del query, top_k
            return [
                ChunkResult(
                    file_path="memory/knowledge/persona/user_preferences.md",
                    chunk_text="某条较弱的历史记忆。",
                    score=0.9,
                    chunk_index=0,
                )
            ]

    memory = StubSessionMemory()
    store = asyncio.run(_seed())
    class StubRouter:
        async def call(self, model_name, messages):
            del model_name, messages
            return "unused"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        structured_store=store,
        semantic_memory=StubSemanticMemory(),
    )

    messages = asyncio.run(
        pipeline._build_llm_messages(Message(text="hi", sender="user", session_id="s1"))
    )
    system_contents = [item["content"] for item in messages if item["role"] == "system"]
    semantic_idx = next(i for i, item in enumerate(system_contents) if "[相关记忆]" in item)
    prefs_idx = next(i for i, item in enumerate(system_contents) if "[High Priority User Preferences]" in item)
    boundary_idx = next(
        i for i, item in enumerate(system_contents) if "answer the user's direct request and then stop" in item
    )

    assert semantic_idx < prefs_idx < boundary_idx


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


def test_pipeline_heartbeat_bypasses_persona_dynamic_semantic_memory() -> None:
    memory = StubSessionMemory()

    class StubSemanticMemory:
        async def search(self, query: str, top_k: int = 5) -> list[ChunkResult]:
            raise AssertionError(
                f"persona semantic memory should be skipped for heartbeat: {query=} {top_k=}"
            )

    class StubPersonaManager:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_system_prompt_section(self, query: str | None = None) -> str:
            self.calls.append(str(query or ""))
            return "persona-with-dynamic-memory"

    class StubRouter:
        async def call(self, model_name, messages):
            del model_name
            system_messages = [item for item in messages if item["role"] == "system"]
            assert all("persona-with-dynamic-memory" not in item["content"] for item in system_messages)
            return "heartbeat ok"

    persona_manager = StubPersonaManager()
    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        persona_system_prompt="static persona only",
        persona_manager=persona_manager,
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
    assert persona_manager.calls == []


def test_pipeline_heartbeat_prefers_lightweight_model_route() -> None:
    memory = StubSessionMemory()

    class StubRouter:
        def __init__(self) -> None:
            self.models: list[str] = []

        def get_model_for_task(self, task_type: str) -> str:
            if task_type == "lightweight":
                return "DeepseekV3_2_Core"
            return "GPT"

        async def call(self, model_name, messages):
            del messages
            self.models.append(model_name)
            return "heartbeat ok"

    router = StubRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="GPT",
        session_memory=memory,
        history_window=20,
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
    assert router.models == ["DeepseekV3_2_Core"]


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
