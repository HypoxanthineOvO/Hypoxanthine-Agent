from __future__ import annotations

import asyncio
from typing import Any

from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.models import Message, SkillOutput
from hypo_agent.skills.auth_skill import AuthSkill
from hypo_agent.skills.base import BaseSkill


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


class FakeSkill(BaseSkill):
    def __init__(
        self,
        *,
        name: str,
        description: str,
        tools: list[dict[str, Any]],
        keyword_hints: list[str] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.required_permissions: list[str] = []
        self._tools = tools
        self.keyword_hints = [hint.casefold() for hint in (keyword_hints or [])]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        self.calls.append((tool_name, dict(params)))
        if tool_name == "sub_list":
            return SkillOutput(
                status="success",
                result={"items": [{"id": "sub-1", "name": "Test UP"}]},
            )
        return SkillOutput(status="success", result={"ok": True})


def _tool_schema(name: str, description: str = "") -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or f"Tool {name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


class ProgressiveSkillManager(SkillManager):
    def match_skills_for_text(self, text: str) -> list[str]:
        normalized = str(text or "").casefold()
        matched: list[str] = []
        for skill in self._skills.values():
            hints = getattr(skill, "keyword_hints", [])
            if any(hint in normalized for hint in hints):
                matched.append(skill.name)
        return matched


def _build_skill_manager() -> tuple[ProgressiveSkillManager, FakeSkill]:
    manager = ProgressiveSkillManager()
    subscription_skill = FakeSkill(
        name="subscription",
        description="管理 B 站、微博、知乎等平台的订阅查看、搜索与推送。",
        tools=[_tool_schema("sub_list", "List subscriptions")],
        keyword_hints=["b站订阅", "订阅", "bilibili"],
    )
    filesystem_skill = FakeSkill(
        name="filesystem",
        description="读取、写入并列出本地文件和目录。",
        tools=[
            _tool_schema("read_file"),
            _tool_schema("write_file"),
            _tool_schema("list_directory"),
        ],
        keyword_hints=["文件", "目录"],
    )
    reminder_skill = FakeSkill(
        name="reminder",
        description="创建、更新、删除和列出提醒事项。",
        tools=[
            _tool_schema("create_reminder"),
            _tool_schema("list_reminders"),
        ],
        keyword_hints=["提醒"],
    )
    memory_skill = FakeSkill(
        name="memory",
        description="保存和读取结构化用户偏好。",
        tools=[
            _tool_schema("save_preference"),
        ],
        keyword_hints=["偏好"],
    )

    manager.register(filesystem_skill, source="test")
    manager.register(subscription_skill, source="test")
    manager.register(reminder_skill, source="test")
    manager.register(memory_skill, source="test")
    manager.register_builtin_tool(_tool_schema("web_search"), lambda **_: SkillOutput(status="success"))
    manager.register_builtin_tool(_tool_schema("web_read"), lambda **_: SkillOutput(status="success"))
    manager.register_builtin_tool(_tool_schema("exec_command"), lambda **_: SkillOutput(status="success"))
    manager.register_builtin_tool(_tool_schema("run_code"), lambda **_: SkillOutput(status="success"))
    manager.register_builtin_tool(_tool_schema("save_sop"), lambda **_: SkillOutput(status="success"))
    manager.register_builtin_tool(_tool_schema("update_persona_memory"), lambda **_: SkillOutput(status="success"))
    return manager, subscription_skill


async def _collect_events(pipeline: ChatPipeline, inbound: Message) -> list[dict[str, Any]]:
    return [event async for event in pipeline.stream_reply(inbound)]


def test_skill_catalog_in_system_prompt() -> None:
    memory = StubSessionMemory()
    skill_manager, _ = _build_skill_manager()

    class StubRouter:
        async def call(self, model_name, messages, **kwargs):
            del model_name, kwargs
            system_messages = [msg["content"] for msg in messages if msg["role"] == "system"]
            catalog_message = next(
                content for content in system_messages if "Available Skills" in content
            )
            assert "subscription: 管理 B 站、微博、知乎等平台的订阅查看、搜索与推送。" in catalog_message
            assert "工具名: sub_list" in catalog_message
            assert "filesystem: 读取、写入并列出本地文件和目录。" in catalog_message
            assert "工具名: read_file, write_file, list_directory" in catalog_message
            assert "请直接尝试调用" in catalog_message
            return "我可以处理订阅、文件、提醒和偏好相关任务。"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skill_manager,
        persona_system_prompt="你是 Hypo-Agent。",
    )

    reply = asyncio.run(
        pipeline.run_once(Message(text="你有什么能力", sender="user", session_id="s1"))
    )

    assert "订阅" in str(reply.text)


def test_core_tools_always_exposed() -> None:
    memory = StubSessionMemory()
    skill_manager, _ = _build_skill_manager()
    captured: dict[str, Any] = {}

    class StubRouter:
        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None, **kwargs):
            del model_name, messages, session_id, kwargs
            captured["tool_names"] = [tool["function"]["name"] for tool in tools or []]
            return {"text": "ok", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None, **kwargs):
            del model_name, messages, session_id, tools, kwargs
            yield ""

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skill_manager,
    )

    asyncio.run(_collect_events(pipeline, Message(text="帮我查一下", sender="user", session_id="s1")))

    assert set(captured["tool_names"]) >= {
        "web_search",
        "web_read",
        "exec_command",
        "run_code",
        "read_file",
        "write_file",
        "list_directory",
        "create_reminder",
        "save_sop",
        "save_preference",
    }
    assert "sub_list" not in set(captured["tool_names"])


def test_dynamic_load_on_unknown_tool() -> None:
    memory = StubSessionMemory()
    skill_manager, subscription_skill = _build_skill_manager()

    class StubRouter:
        def __init__(self) -> None:
            self.tool_snapshots: list[list[str]] = []
            self.calls = 0

        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None, **kwargs):
            del model_name, messages, session_id, kwargs
            self.calls += 1
            tool_names = [tool["function"]["name"] for tool in tools or []]
            self.tool_snapshots.append(tool_names)
            if self.calls == 1:
                assert "sub_list" not in tool_names
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "sub_list", "arguments": "{}"},
                        }
                    ],
                }
            if self.calls == 2:
                assert "sub_list" in tool_names
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "sub_list", "arguments": "{}"},
                        }
                    ],
                }
            return {"text": "已列出订阅", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None, **kwargs):
            del model_name, messages, session_id, tools, kwargs
            yield ""

    router = StubRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skill_manager,
    )

    events = asyncio.run(
        _collect_events(pipeline, Message(text="帮我看看关注的UP主", sender="user", session_id="s1"))
    )

    assert router.calls == 3
    assert any(event["type"] == "tool_call_start" and event["iteration"] == 1 for event in events)
    assert subscription_skill.calls == [("sub_list", {"__session_id": "s1"})]


def test_dynamic_load_on_skill_name_uses_fuzzy_owner_match() -> None:
    memory = StubSessionMemory()
    skill_manager, subscription_skill = _build_skill_manager()

    class StubRouter:
        def __init__(self) -> None:
            self.calls = 0
            self.tool_snapshots: list[list[str]] = []
            self.tool_messages: list[str] = []

        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None, **kwargs):
            del model_name, session_id, kwargs
            self.calls += 1
            tool_names = [tool["function"]["name"] for tool in tools or []]
            self.tool_snapshots.append(tool_names)
            if self.calls == 1:
                assert "sub_list" not in tool_names
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_subscription",
                            "type": "function",
                            "function": {"name": "subscription", "arguments": "{}"},
                        }
                    ],
                }
            if self.calls == 2:
                assert "sub_list" in tool_names
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_sub_list",
                            "type": "function",
                            "function": {"name": "sub_list", "arguments": "{}"},
                        }
                    ],
                }
            self.tool_messages = [
                str(message.get("content") or "")
                for message in messages
                if message.get("role") == "tool"
            ]
            return {"text": "已列出订阅", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None, **kwargs):
            del model_name, messages, session_id, tools, kwargs
            yield ""

    router = StubRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skill_manager,
    )

    asyncio.run(
        _collect_events(pipeline, Message(text="帮我看看 subscription", sender="user", session_id="s1"))
    )

    assert router.calls == 3
    assert subscription_skill.calls == [("sub_list", {"__session_id": "s1"})]
    assert all("tool_not_found" not in message for message in router.tool_messages)


def test_dynamic_load_unknown_skill() -> None:
    memory = StubSessionMemory()
    skill_manager, subscription_skill = _build_skill_manager()

    class StubRouter:
        def __init__(self) -> None:
            self.calls = 0
            self.tool_messages: list[str] = []

        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None, **kwargs):
            del model_name, session_id, tools, kwargs
            self.calls += 1
            if self.calls == 1:
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_missing",
                            "type": "function",
                            "function": {"name": "missing_tool", "arguments": "{}"},
                        }
                    ],
                }
            self.tool_messages = [
                str(message.get("content") or "")
                for message in messages
                if message.get("role") == "tool"
            ]
            return {"text": "工具不存在", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None, **kwargs):
            del model_name, messages, session_id, tools, kwargs
            yield ""

    router = StubRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skill_manager,
    )

    asyncio.run(_collect_events(pipeline, Message(text="调用一个不存在的工具", sender="user", session_id="s1")))

    assert router.calls == 2
    assert subscription_skill.calls == []
    assert any("tool_not_found" in message for message in router.tool_messages)


def test_keyword_preload_still_works() -> None:
    memory = StubSessionMemory()
    skill_manager, _ = _build_skill_manager()
    captured: dict[str, Any] = {}

    class StubRouter:
        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None, **kwargs):
            del model_name, messages, session_id, kwargs
            captured["tool_names"] = [tool["function"]["name"] for tool in tools or []]
            return {"text": "已看到订阅工具", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None, **kwargs):
            del model_name, messages, session_id, tools, kwargs
            yield ""

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skill_manager,
    )

    asyncio.run(_collect_events(pipeline, Message(text="帮我看B站订阅", sender="user", session_id="s1")))

    assert "sub_list" in captured["tool_names"]


def test_no_keyword_still_loads() -> None:
    memory = StubSessionMemory()
    skill_manager, _ = _build_skill_manager()

    class StubRouter:
        def __init__(self) -> None:
            self.snapshots: list[list[str]] = []
            self.calls = 0

        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None, **kwargs):
            del model_name, messages, session_id, kwargs
            self.calls += 1
            tool_names = [tool["function"]["name"] for tool in tools or []]
            self.snapshots.append(tool_names)
            if self.calls == 1:
                assert "sub_list" not in tool_names
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_sub",
                            "type": "function",
                            "function": {"name": "sub_list", "arguments": "{}"},
                        }
                    ],
                }
            if self.calls == 2:
                assert "sub_list" in tool_names
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_sub",
                            "type": "function",
                            "function": {"name": "sub_list", "arguments": "{}"},
                        }
                    ],
                }
            return {"text": "已查看关注的 UP 主", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None, **kwargs):
            del model_name, messages, session_id, tools, kwargs
            yield ""

    router = StubRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skill_manager,
    )

    asyncio.run(_collect_events(pipeline, Message(text="帮我看看关注的UP主", sender="user", session_id="s1")))

    assert router.calls == 3
    assert "sub_list" not in router.snapshots[0]
    assert "sub_list" in router.snapshots[1]


def test_loaded_skills_persist_in_session() -> None:
    memory = StubSessionMemory()
    skill_manager, _ = _build_skill_manager()

    class StubRouter:
        def __init__(self) -> None:
            self.calls = 0
            self.snapshots: list[list[str]] = []

        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None, **kwargs):
            del model_name, messages, session_id, kwargs
            self.calls += 1
            tool_names = [tool["function"]["name"] for tool in tools or []]
            self.snapshots.append(tool_names)
            if self.calls == 1:
                assert "sub_list" not in tool_names
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "sub_list", "arguments": "{}"},
                        }
                    ],
                }
            if self.calls == 2:
                assert "sub_list" in tool_names
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "sub_list", "arguments": "{}"},
                        }
                    ],
                }
            assert "sub_list" in tool_names
            return {"text": "第二轮直接可用", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None, **kwargs):
            del model_name, messages, session_id, tools, kwargs
            yield ""

    router = StubRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skill_manager,
    )

    asyncio.run(_collect_events(pipeline, Message(text="看看我最近关注的人", sender="user", session_id="s1")))
    asyncio.run(_collect_events(pipeline, Message(text="再看一次", sender="user", session_id="s1")))

    assert "sub_list" not in router.snapshots[0]
    assert "sub_list" in router.snapshots[1]
    assert "sub_list" in router.snapshots[2]


def test_skill_manager_matches_auth_login_keywords(tmp_path) -> None:
    manager = SkillManager()
    manager.register(
        AuthSkill(
            secrets_path=tmp_path / "secrets.yaml",
            qr_dir=tmp_path / "auth-qr",
        ),
        source="test",
    )

    matched = manager.match_skills_for_text("微博 Cookie 过期了，帮我重新登录微博，WeWe RSS 也登录失效了")

    assert "auth" in matched


def test_find_skill_by_tool_name_supports_fuzzy_skill_name_and_keywords() -> None:
    manager, subscription_skill = _build_skill_manager()

    assert manager.find_skill_by_tool_name("sub_list") is subscription_skill
    assert manager.find_skill_by_tool_name("subscription") is subscription_skill
    assert manager.find_skill_by_tool_name("sub") is subscription_skill
    assert manager.find_skill_by_tool_name("bilibili") is subscription_skill
