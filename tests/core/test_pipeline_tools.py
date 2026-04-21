from __future__ import annotations

import asyncio
import json

import hypo_agent.core.pipeline as pipeline_module
import pytest
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.models import CircuitBreakerConfig, Message, SkillOutput
from hypo_agent.security.circuit_breaker import CircuitBreaker
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


class StubSkillManager:
    def __init__(self, output: SkillOutput | None = None) -> None:
        self.calls: list[tuple[str, dict, str | None]] = []
        self.output = output or SkillOutput(
            status="success",
            result={"stdout": "hi", "stderr": "", "exit_code": 0},
        )

    def get_tools_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "exec_command",
                    "parameters": {"type": "object"},
                },
            }
        ]

    async def invoke(
        self,
        tool_name: str,
        params: dict,
        *,
        session_id: str | None = None,
        skill_name: str | None = None,
    ) -> SkillOutput:
        self.calls.append((tool_name, params, session_id, skill_name))
        return self.output


class RecordingLogger:
    def __init__(self) -> None:
        self.debug_calls: list[tuple[str, dict]] = []
        self.info_calls: list[tuple[str, dict]] = []

    def debug(self, event: str, **kwargs) -> None:
        self.debug_calls.append((event, kwargs))

    def info(self, event: str, **kwargs) -> None:
        self.info_calls.append((event, kwargs))

    def warning(self, event: str, **kwargs) -> None:
        self.info_calls.append((event, kwargs))

    def exception(self, event: str, **kwargs) -> None:  # pragma: no cover - defensive
        self.info_calls.append((event, kwargs))


def test_pipeline_stream_reply_with_tools_single_round() -> None:
    memory = StubSessionMemory()
    skills = StubSkillManager()

    class StubRouter:
        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            assert model_name == "Gemini3Pro"
            assert tools is not None
            assert session_id == "s1"
            return {"text": "", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            assert model_name == "Gemini3Pro"
            assert session_id == "s1"
            assert tools is not None
            yield "He"
            yield "llo"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="hello", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    assert events[-1]["type"] == "assistant_done"
    assert events[0]["type"] == "assistant_chunk"
    assert [m.sender for m in memory.appended] == ["user", "assistant"]
    assert memory.appended[1].text == "Hello"


def test_pipeline_stream_reply_uses_call_with_tools_text_without_stream_call() -> None:
    memory = StubSessionMemory()
    skills = StubSkillManager()

    class StubRouter:
        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            assert model_name == "Gemini3Pro"
            assert tools is not None
            assert session_id == "s1"
            assert messages[0]["role"] == "system"
            assert "MUST use the provided tools" in messages[0]["content"]
            return {"text": "direct answer", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            raise AssertionError("stream() should not be called when decision text exists")
            yield ""  # pragma: no cover

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="hello", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    assert [event["type"] for event in events] == ["assistant_chunk", "assistant_done"]
    assert events[0]["text"] == "direct answer"


def test_pipeline_tool_prompt_includes_self_repair_guidance() -> None:
    memory = StubSessionMemory()
    skills = StubSkillManager()
    captured_messages: list[dict] = []

    class StubRouter:
        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, tools, session_id
            captured_messages.extend(messages)
            return {"text": "ok", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            raise AssertionError("stream() should not be called when decision text exists")
            yield ""  # pragma: no cover

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="检查一下最近为什么工具总失败", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    asyncio.run(_collect())
    tool_prompt = next(
        item["content"]
        for item in captured_messages
        if item["role"] == "system" and "MUST use the provided tools" in item["content"]
    )
    assert "get_error_summary" in tool_prompt
    assert "get_tool_history" in tool_prompt
    assert "coder_submit_task" in tool_prompt


def test_pipeline_stream_reply_runs_tool_and_emits_tool_events() -> None:
    memory = StubSessionMemory()
    skills = StubSkillManager()

    class StubRouter:
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
                                "arguments": "{\"command\": \"echo hi\"}",
                            },
                        }
                    ],
                }
            return {"text": "", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            yield "done"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="run", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    assert events[0]["type"] == "tool_call_start"
    assert events[1]["type"] == "tool_call_result"
    assert events[1]["status"] == "success"
    assert events[1]["metadata"]["ephemeral"] is True
    assert events[-1]["type"] == "assistant_done"
    assert skills.calls[0][0] == "exec_command"
    assert skills.calls[0][1]["command"] == "echo hi"
    assert skills.calls[0][3] == "direct"


def test_pipeline_stream_reply_logs_tool_names_before_react_rounds(monkeypatch) -> None:
    memory = StubSessionMemory()

    class BuiltinAwareSkillManager(StubSkillManager):
        def get_tools_schema(self) -> list[dict]:
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "update_persona_memory",
                        "parameters": {"type": "object"},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "save_sop",
                        "parameters": {"type": "object"},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "search_sop",
                        "parameters": {"type": "object"},
                    },
                },
            ]

    skills = BuiltinAwareSkillManager()
    logger = RecordingLogger()
    monkeypatch.setattr(pipeline_module, "logger", logger)

    class StubRouter:
        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, messages, session_id
            assert tools is not None
            return {"text": "好的，我会记住。", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            raise AssertionError("stream() should not be called when decision text exists")
            yield ""  # pragma: no cover

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="记住我喜欢简洁回复", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())

    assert events[-1]["type"] == "assistant_done"
    debug_event = next(kwargs for event, kwargs in logger.debug_calls if event == "react.tools")
    assert debug_event["tool_names"] == [
        "update_persona_memory",
        "save_sop",
        "search_sop",
    ]


def test_pipeline_heartbeat_filters_high_risk_tools_and_sets_timeout() -> None:
    memory = StubSessionMemory()

    class HeartbeatSkillManager(StubSkillManager):
        def get_tools_schema(self) -> list[dict]:
            return [
                {
                    "type": "function",
                    "function": {"name": "exec_command", "parameters": {"type": "object"}},
                },
                {
                    "type": "function",
                    "function": {"name": "tmux_send", "parameters": {"type": "object"}},
                },
                {
                    "type": "function",
                    "function": {"name": "write_file", "parameters": {"type": "object"}},
                },
                {
                    "type": "function",
                    "function": {"name": "list_reminders", "parameters": {"type": "object"}},
                },
            ]

    skills = HeartbeatSkillManager()
    captured: dict[str, object] = {}

    class StubRouter:
        async def call_with_tools(
            self,
            model_name,
            messages,
            *,
            tools=None,
            session_id=None,
            timeout_seconds=None,
        ):
            del model_name, messages, session_id
            captured["tool_names"] = [tool["function"]["name"] for tool in tools or []]
            captured["timeout_seconds"] = timeout_seconds
            return {"text": "", "tool_calls": []}

        async def stream(
            self,
            model_name,
            messages,
            *,
            session_id=None,
            tools=None,
            timeout_seconds=None,
        ):
            del model_name, messages, session_id, tools, timeout_seconds
            yield "heartbeat ok"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="GPT",
        session_memory=memory,
        skill_manager=skills,
        heartbeat_allowed_tools={"exec_command", "list_reminders"},
        heartbeat_model_timeout_seconds=60,
    )

    async def _collect() -> list[dict]:
        inbound = Message(
            text="heartbeat",
            sender="user",
            session_id="main",
            channel="system",
            message_tag="heartbeat",
            metadata={"source": "heartbeat"},
        )
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())

    assert events[-1]["type"] == "assistant_done"
    assert captured["tool_names"] == ["exec_command", "list_reminders"]
    assert captured["timeout_seconds"] == 60.0


def test_pipeline_non_heartbeat_sets_total_react_timeout_on_router_calls() -> None:
    memory = StubSessionMemory()
    skills = StubSkillManager()
    captured: dict[str, object] = {}

    class StubRouter:
        async def call_with_tools(
            self,
            model_name,
            messages,
            *,
            tools=None,
            session_id=None,
            timeout_seconds=None,
        ):
            del model_name, messages, tools, session_id
            captured["timeout_seconds"] = timeout_seconds
            return {"text": "done", "tool_calls": []}

        async def stream(
            self,
            model_name,
            messages,
            *,
            session_id=None,
            tools=None,
            timeout_seconds=None,
        ):
            del model_name, messages, session_id, tools, timeout_seconds
            if False:  # pragma: no cover
                yield ""

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        max_react_timeout_seconds=120,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="run", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())

    assert events[-1]["type"] == "assistant_done"
    assert captured["timeout_seconds"] == 120


def test_pipeline_marks_sop_usage_after_search_sop_tool_result() -> None:
    memory = StubSessionMemory()

    class SearchSopSkillManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, str | None]] = []

        def get_tools_schema(self) -> list[dict]:
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "search_sop",
                        "parameters": {"type": "object"},
                    },
                }
            ]

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
                    "items": [
                        {
                            "title": "重启 Hypo-Agent 服务流程",
                            "file_path": "/tmp/memory/knowledge/sop/重启 Hypo-Agent 服务流程.md",
                        }
                    ]
                },
            )

    class StubSopManager:
        def __init__(self) -> None:
            self.touched: list[list[str]] = []

        async def touch_files(self, file_paths: list[str]) -> None:
            self.touched.append(list(file_paths))

    class StubRouter:
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
                                "name": "search_sop",
                                "arguments": "{\"query\": \"怎么重启 Hypo-Agent\", \"top_k\": 3}",
                            },
                        }
                    ],
                }
            return {"text": "按 SOP 执行完成", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called when final text exists")
            yield ""  # pragma: no cover

    skills = SearchSopSkillManager()
    sop_manager = StubSopManager()
    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        sop_manager=sop_manager,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="怎么重启 Hypo-Agent", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())

    assert events[-1]["type"] == "assistant_done"
    assert skills.calls == [("search_sop", {"query": "怎么重启 Hypo-Agent", "top_k": 3}, "s1", "direct")]
    assert sop_manager.touched == [[
        "/tmp/memory/knowledge/sop/重启 Hypo-Agent 服务流程.md"
    ]]


def test_pipeline_stream_reply_sends_humanized_tool_status_messages() -> None:
    memory = StubSessionMemory()

    class ReminderSkillManager(StubSkillManager):
        def get_tools_schema(self) -> list[dict]:
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "create_reminder",
                        "parameters": {"type": "object"},
                    },
                }
            ]

    skills = ReminderSkillManager()
    pushed: list[Message] = []

    async def on_proactive_message(message: Message) -> None:
        pushed.append(message)

    class StubRouter:
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
                                "name": "create_reminder",
                                "arguments": "{\"title\":\"x\",\"schedule_type\":\"once\",\"schedule_value\":\"2026-03-08T15:00:00+08:00\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called")
            yield ""  # pragma: no cover

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        on_proactive_message=on_proactive_message,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="run", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    assert events[-1]["type"] == "assistant_done"
    tool_status = [item for item in pushed if item.message_tag == "tool_status"]
    assert len(tool_status) == 2
    assert tool_status[0].text == "🔔 正在创建提醒..."
    assert tool_status[1].text == "✅ 提醒创建成功"
    assert tool_status[0].metadata["ephemeral"] is True


def test_pipeline_stream_reply_skips_tool_status_messages_when_narration_enabled() -> None:
    memory = StubSessionMemory()
    skills = StubSkillManager()
    pushed: list[Message] = []

    async def on_proactive_message(message: Message) -> None:
        pushed.append(message)

    class StubNarrationObserver:
        enabled = True

    class StubRouter:
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
                                "name": "create_reminder",
                                "arguments": "{\"title\":\"x\",\"schedule_type\":\"once\",\"schedule_value\":\"2026-03-08T15:00:00+08:00\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called")
            yield ""  # pragma: no cover

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        on_proactive_message=on_proactive_message,
        narration_observer=StubNarrationObserver(),
        on_narration=lambda payload, **kwargs: None,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="run", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    assert events[-1]["type"] == "assistant_done"
    tool_status = [item for item in pushed if item.message_tag == "tool_status"]
    assert tool_status == []


def test_no_tool_status_when_event_emitter_present() -> None:
    memory = StubSessionMemory()
    pushed: list[Message] = []
    emitted: list[dict[str, object]] = []

    class ReminderSkillManager(StubSkillManager):
        def get_tools_schema(self) -> list[dict]:
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "create_reminder",
                        "parameters": {"type": "object"},
                    },
                }
            ]

    class StubRouter:
        def __init__(self) -> None:
            self.calls = 0

        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None, event_emitter=None):
            del model_name, messages, tools, session_id, event_emitter
            self.calls += 1
            if self.calls == 1:
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "create_reminder",
                                "arguments": "{\"title\":\"x\",\"schedule_type\":\"once\",\"schedule_value\":\"2026-03-08T15:00:00+08:00\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None, event_emitter=None):
            del model_name, messages, session_id, tools, event_emitter
            raise AssertionError("stream should not be called")
            yield ""  # pragma: no cover

    async def on_proactive_message(message: Message) -> None:
        pushed.append(message)

    async def event_emitter(payload: dict[str, object]) -> None:
        emitted.append(dict(payload))

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=ReminderSkillManager(),
        on_proactive_message=on_proactive_message,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="run", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound, event_emitter=event_emitter)]

    events = asyncio.run(_collect())

    assert events[-1]["type"] == "assistant_done"
    assert any(item.get("type") == "react_iteration" for item in emitted)
    assert [item for item in pushed if item.message_tag == "tool_status"] == []


def test_tool_status_sent_when_no_emitter() -> None:
    memory = StubSessionMemory()
    pushed: list[Message] = []

    class ReminderSkillManager(StubSkillManager):
        def get_tools_schema(self) -> list[dict]:
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "create_reminder",
                        "parameters": {"type": "object"},
                    },
                }
            ]

    class StubRouter:
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
                                "name": "create_reminder",
                                "arguments": "{\"title\":\"x\",\"schedule_type\":\"once\",\"schedule_value\":\"2026-03-08T15:00:00+08:00\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called")
            yield ""  # pragma: no cover

    async def on_proactive_message(message: Message) -> None:
        pushed.append(message)

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=ReminderSkillManager(),
        on_proactive_message=on_proactive_message,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="run", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())

    assert events[-1]["type"] == "assistant_done"
    tool_status = [item.text for item in pushed if item.message_tag == "tool_status"]
    assert tool_status == ["🔔 正在创建提醒...", "✅ 提醒创建成功"]


def test_pipeline_stream_reply_respects_max_react_rounds() -> None:
    memory = StubSessionMemory()
    skills = StubSkillManager()

    class StubRouter:
        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, messages, tools, session_id
            return {
                "text": "",
                "tool_calls": [
                    {
                        "id": "loop",
                        "type": "function",
                        "function": {
                            "name": "exec_command",
                            "arguments": "{\"command\": \"echo hi\"}",
                        },
                    }
                ],
            }

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            yield "should-not-run"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        max_react_rounds=2,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="loop", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    assert events[-2]["type"] == "assistant_chunk"
    assert "best-effort summary" in events[-2]["text"].lower()
    assert events[-1]["type"] == "assistant_done"


def test_pipeline_stream_reply_gracefully_degrades_before_round_limit() -> None:
    memory = StubSessionMemory()
    skills = StubSkillManager()

    class StubRouter:
        def __init__(self) -> None:
            self.tools_history: list[list[dict] | None] = []

        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, session_id
            self.tools_history.append(tools)
            if tools is None:
                return {"text": "基于现有结果：CPU 正常，邮件无异常。", "tool_calls": []}
            return {
                "text": "",
                "tool_calls": [
                    {
                        "id": "loop",
                        "type": "function",
                        "function": {
                            "name": "exec_command",
                            "arguments": "{\"command\": \"uptime\"}",
                        },
                    }
                ],
            }

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called when graceful degradation succeeds")
            yield ""  # pragma: no cover

    router = StubRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        max_react_rounds=2,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="loop", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    combined = "".join(event.get("text", "") for event in events if event.get("type") == "assistant_chunk")

    assert "基于现有结果" in combined
    assert "max react rounds" not in combined.lower()
    assert router.tools_history == [skills.get_tools_schema(), None]


def test_pipeline_default_max_react_rounds_is_higher_than_before() -> None:
    class DefaultRouter:
        async def call(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            return "ok"

        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, messages, tools, session_id
            return {"text": "ok", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            if False:  # pragma: no cover
                yield ""

    pipeline = ChatPipeline(
        router=DefaultRouter(),
        chat_model="Gemini3Pro",
        session_memory=StubSessionMemory(),
        skill_manager=StubSkillManager(),
    )

    assert pipeline.max_react_rounds <= 10


def test_pipeline_stream_reply_uses_heartbeat_specific_round_limit() -> None:
    memory = StubSessionMemory()
    skills = StubSkillManager()

    class StubRouter:
        def __init__(self) -> None:
            self.tools_history: list[list[dict] | None] = []

        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, messages, session_id
            self.tools_history.append(tools)
            return {
                "text": "",
                "tool_calls": [
                    {
                        "id": f"call_{len(self.tools_history)}",
                        "type": "function",
                        "function": {
                            "name": "exec_command",
                            "arguments": "{\"command\": \"uptime\"}",
                        },
                    }
                ],
            }

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called")
            yield ""  # pragma: no cover

    router = StubRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        max_react_rounds=15,
        heartbeat_max_react_rounds=3,
    )

    async def _collect() -> list[dict]:
        inbound = Message(
            text="heartbeat",
            sender="user",
            session_id="s1",
            channel="system",
            message_tag="heartbeat",
            metadata={"source": "heartbeat", "skip_memory_search": True},
        )
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    combined = "".join(event.get("text", "") for event in events if event.get("type") == "assistant_chunk")

    assert "Heartbeat 已达到工具轮次上限" in combined
    assert "exec_command" in combined
    assert "hi" in combined
    assert len(router.tools_history) == 3
    assert all(history is not None for history in router.tools_history)


def test_pipeline_heartbeat_compacts_tool_output_for_followup_round() -> None:
    memory = StubSessionMemory()
    huge_stdout = "\n".join(f"line {index}: {'x' * 80}" for index in range(240))
    skills = StubSkillManager(
        output=SkillOutput(
            status="success",
            result={"stdout": huge_stdout, "stderr": "", "exit_code": 0},
        )
    )

    class StubRouter:
        def __init__(self) -> None:
            self.calls = 0
            self.last_messages: list[dict] = []

        async def call_with_tools(
            self,
            model_name,
            messages,
            *,
            tools=None,
            session_id=None,
            timeout_seconds=None,
        ):
            del model_name, session_id, timeout_seconds
            self.calls += 1
            if self.calls == 1:
                assert tools is not None
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "exec_command",
                                "arguments": "{\"command\": \"uptime\"}",
                            },
                        }
                    ],
                }
            self.last_messages = messages
            return {"text": "heartbeat ok", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None, timeout_seconds=None):
            del model_name, messages, session_id, tools, timeout_seconds
            raise AssertionError("stream should not be called")
            yield ""  # pragma: no cover

    router = StubRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        heartbeat_allowed_tools={"exec_command"},
        heartbeat_model_timeout_seconds=60,
    )

    async def _collect() -> list[dict]:
        inbound = Message(
            text="heartbeat",
            sender="user",
            session_id="s1",
            channel="system",
            message_tag="heartbeat",
            metadata={"source": "heartbeat", "skip_memory_search": True},
        )
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    assert events[-1]["type"] == "assistant_done"
    tool_messages = [message for message in router.last_messages if message.get("role") == "tool"]
    assert len(tool_messages) == 1
    tool_content = str(tool_messages[0]["content"])
    assert len(tool_content) < 2500
    assert "truncated for heartbeat" in tool_content
    assert "exit_code" in tool_content


def test_pipeline_queued_heartbeat_event_forces_skip_memory_and_timeout() -> None:
    memory = StubSessionMemory()

    class SemanticMemory:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int]] = []

        def search(self, query: str, top_k: int = 5):
            self.calls.append((query, top_k))
            return []

    semantic_memory = SemanticMemory()
    captured: dict[str, object] = {}

    class StubRouter:
        async def call_with_tools(
            self,
            model_name,
            messages,
            *,
            tools=None,
            session_id=None,
            timeout_seconds=None,
        ):
            del model_name, tools
            captured["session_id"] = session_id
            captured["timeout_seconds"] = timeout_seconds
            captured["messages"] = messages
            return {"text": "heartbeat ok", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None, timeout_seconds=None):
            del model_name, messages, session_id, tools, timeout_seconds
            raise AssertionError("stream should not be called")
            yield ""  # pragma: no cover

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=StubSkillManager(),
        semantic_memory=semantic_memory,
        heartbeat_model_timeout_seconds=25,
        heartbeat_max_react_rounds=2,
    )

    emitted: list[dict] = []

    async def _run() -> None:
        inbound = Message(
            text="heartbeat",
            sender="user",
            session_id="main",
            channel="webui",
            metadata={},
            message_tag="heartbeat",
        )
        await pipeline._consume_user_message_event(
            {
                "event_type": "user_message",
                "message": inbound,
                "emit": emitted.append,
            }
        )

    asyncio.run(_run())

    assert semantic_memory.calls == []
    assert captured["session_id"] == "main"
    assert captured["timeout_seconds"] == 25.0
    assert emitted[-1]["type"] == "assistant_done"


def test_pipeline_stream_reply_emits_error_when_tool_blocked() -> None:
    memory = StubSessionMemory()
    skills = StubSkillManager(
        output=SkillOutput(status="error", error_info="tool circuit breaker is open"),
    )

    class StubRouter:
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
                            "id": "blocked_1",
                            "type": "function",
                            "function": {
                                "name": "exec_command",
                                "arguments": "{\"command\": \"echo hi\"}",
                            },
                        }
                    ],
                }
            return {"text": "", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            yield "fallback"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="run", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    assert events[1]["type"] == "tool_call_result"
    assert events[1]["status"] == "error"
    assert "circuit breaker" in events[1]["error_info"]


def test_pipeline_stream_reply_short_circuits_slash_command() -> None:
    memory = StubSessionMemory()
    skills = StubSkillManager()

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
            if (inbound.text or "").startswith("/"):
                return "slash stream ok"
            return None

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        slash_commands=StubSlashCommands(),
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="/help", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    assert [event["type"] for event in events] == ["assistant_chunk", "assistant_done"]
    assert events[0]["text"] == "slash stream ok"
    assert all(event["sender"] == "assistant" for event in events)
    assert all(event["session_id"] == "s1" for event in events)
    assert all(str(event["timestamp"]).endswith("+08:00") for event in events)
    assert memory.appended == []


def test_pipeline_stream_reply_compresses_tool_output_before_tool_message() -> None:
    memory = StubSessionMemory()
    skills = StubSkillManager(
        output=SkillOutput(
            status="success",
            result={"stdout": "a" * 5000, "stderr": "", "exit_code": 0},
        )
    )

    class StubOutputCompressor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, str | None]] = []

        async def compress_if_needed(
            self,
            output: str,
            metadata: dict,
            *,
            tool_name: str | None = None,
        ) -> tuple[str, bool]:
            self.calls.append((output, metadata, tool_name))
            metadata["compressed_meta"] = {
                "cache_id": "cache_1",
                "original_chars": 5000,
                "compressed_chars": 120,
            }
            return (
                "compressed\n"
                '[📦 原始输出 5000 字符，已压缩至 120 字符。如需查看原文请说"给我看原始输出"]'
            ), True

    compressor = StubOutputCompressor()

    class StubRouter:
        def __init__(self) -> None:
            self.calls = 0
            self.last_messages: list[dict] = []

        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, tools, session_id
            self.calls += 1
            self.last_messages = messages
            if self.calls == 1:
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "exec_command",
                                "arguments": "{\"command\": \"echo big\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called")
            yield ""  # pragma: no cover

    router = StubRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        output_compressor=compressor,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="run", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    assert compressor.calls
    assert compressor.calls[0][2] == "exec_command"
    assert events[1]["type"] == "tool_call_result"
    assert str(events[1]["result"]).endswith('如需查看原文请说"给我看原始输出"]')
    assert events[1]["compressed_meta"] == {
        "cache_id": "cache_1",
        "original_chars": 5000,
        "compressed_chars": 120,
    }
    tool_messages = [m for m in router.last_messages if m.get("role") == "tool"]
    assert tool_messages
    assert str(tool_messages[-1]["content"]).endswith('如需查看原文请说"给我看原始输出"]')


def test_pipeline_stream_reply_keeps_tool_output_uncompressed_when_compressor_disabled() -> None:
    memory = StubSessionMemory()
    original_output = SkillOutput(
        status="success",
        result={"stdout": "a" * 5000, "stderr": "", "exit_code": 0},
    )
    skills = StubSkillManager(output=original_output)

    class StubRouter:
        def __init__(self) -> None:
            self.calls = 0
            self.last_messages: list[dict] = []

        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, tools, session_id
            self.calls += 1
            self.last_messages = messages
            if self.calls == 1:
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "exec_command",
                                "arguments": "{\"command\": \"echo big\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called")
            yield ""  # pragma: no cover

    router = StubRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        output_compressor=None,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="run", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())

    assert events[1]["type"] == "tool_call_result"
    assert events[1]["result"] == original_output.result
    assert "compressed_meta" not in events[1]
    tool_messages = [message for message in router.last_messages if message.get("role") == "tool"]
    assert tool_messages == [
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": json.dumps(original_output.model_dump(mode="json"), ensure_ascii=False),
        }
    ]


def test_pipeline_stream_reply_passes_string_tool_result_to_llm_without_json_wrapper() -> None:
    memory = StubSessionMemory()
    original_output = SkillOutput(status="success", result="整理后的资讯摘要")
    skills = StubSkillManager(output=original_output)

    class StubRouter:
        def __init__(self) -> None:
            self.calls = 0
            self.last_messages: list[dict] = []

        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, tools, session_id
            self.calls += 1
            self.last_messages = messages
            if self.calls == 1:
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "exec_command",
                                "arguments": "{\"command\": \"echo digest\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called")
            yield ""  # pragma: no cover

    router = StubRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        output_compressor=None,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="run", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    asyncio.run(_collect())

    tool_messages = [message for message in router.last_messages if message.get("role") == "tool"]
    assert tool_messages == [
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "整理后的资讯摘要",
        }
    ]


def test_pipeline_retries_retryable_tool_once_after_failure() -> None:
    memory = StubSessionMemory()

    class RetryingSkillManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def get_tools_schema(self) -> list[dict]:
            return [{"type": "function", "function": {"name": "web_search"}}]

        async def invoke(
            self,
            tool_name: str,
            params: dict,
            *,
            session_id: str | None = None,
            skill_name: str | None = None,
        ) -> SkillOutput:
            del session_id, skill_name
            self.calls.append((tool_name, dict(params)))
            if len(self.calls) == 1:
                return SkillOutput(status="error", error_info="temporary upstream failure")
            return SkillOutput(status="success", result="search recovered")

    class StubRouter:
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
                                "name": "web_search",
                                "arguments": "{\"query\": \"hypo agent\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called")
            yield ""  # pragma: no cover

    skills = RetryingSkillManager()
    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="search", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())

    assert events[-1]["type"] == "assistant_done"
    assert skills.calls == [
        ("web_search", {"query": "hypo agent"}),
        ("web_search", {"query": "hypo agent"}),
    ]



def test_pipeline_emits_progress_events_via_event_emitter() -> None:
    memory = StubSessionMemory()
    skills = StubSkillManager()
    emitted: list[dict[str, object]] = []

    async def event_emitter(payload: dict[str, object]) -> None:
        emitted.append(dict(payload))

    class StubRouter:
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
                                "arguments": "{\"command\": \"echo hi\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called")
            yield ""  # pragma: no cover

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        event_emitter=event_emitter,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="run", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())

    assert events[-1]["type"] == "assistant_done"
    event_types = [str(item.get("type")) for item in emitted]
    assert event_types[:3] == ["pipeline_stage", "pipeline_stage", "pipeline_stage"]
    assert any(
        item.get("type") == "pipeline_stage"
        and item.get("stage") == "model_routing"
        and item.get("model") == "Gemini3Pro"
        for item in emitted
    )
    assert any(item.get("type") == "react_iteration" and item.get("iteration") == 1 for item in emitted)
    assert any(item.get("type") == "react_complete" and item.get("total_tool_calls") == 1 for item in emitted)


def test_pipeline_emits_compression_event_via_event_emitter() -> None:
    memory = StubSessionMemory()
    emitted: list[dict[str, object]] = []

    async def event_emitter(payload: dict[str, object]) -> None:
        emitted.append(dict(payload))

    skills = StubSkillManager(
        output=SkillOutput(
            status="success",
            result={"stdout": "a" * 5000, "stderr": "", "exit_code": 0},
        )
    )

    class StubOutputCompressor:
        async def compress_if_needed(
            self,
            output: str,
            metadata: dict,
            *,
            tool_name: str | None = None,
        ) -> tuple[str, bool]:
            del output, tool_name
            metadata["compressed_meta"] = {
                "cache_id": "cache_progress",
                "original_chars": 5000,
                "compressed_chars": 120,
            }
            return "compressed output", True

    class StubRouter:
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
                                "arguments": "{\"command\": \"echo big\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called")
            yield ""  # pragma: no cover

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        output_compressor=StubOutputCompressor(),
        event_emitter=event_emitter,
    )

    async def _collect() -> None:
        inbound = Message(text="run", sender="user", session_id="s1")
        async for _ in pipeline.stream_reply(inbound):
            pass

    asyncio.run(_collect())

    assert any(
        item.get("type") == "compression"
        and item.get("original_chars") == 5000
        and item.get("compressed_chars") == 120
        for item in emitted
    )


def test_pipeline_stream_reply_persists_compressed_meta_with_invocation_id() -> None:
    memory = StubSessionMemory()

    class RecordingSkillManager(StubSkillManager):
        def __init__(self) -> None:
            super().__init__(
                output=SkillOutput(
                    status="success",
                    result={"stdout": "a" * 5000, "stderr": "", "exit_code": 0},
                    metadata={"invocation_id": 42},
                )
            )
            self.compressed_meta_calls: list[tuple[int, dict]] = []

        async def attach_invocation_compressed_meta(
            self,
            *,
            invocation_id: int,
            compressed_meta: dict,
        ) -> None:
            self.compressed_meta_calls.append((invocation_id, compressed_meta))

    skills = RecordingSkillManager()

    class StubOutputCompressor:
        async def compress_if_needed(
            self,
            output: str,
            metadata: dict,
            *,
            tool_name: str | None = None,
        ) -> tuple[str, bool]:
            del output, tool_name
            metadata["compressed_meta"] = {
                "cache_id": "cache_2",
                "original_chars": 5000,
                "compressed_chars": 120,
            }
            return (
                "compressed\n"
                '[📦 原始输出 5000 字符，已压缩至 120 字符。如需查看原文请说"给我看原始输出"]'
            ), True

    class StubRouter:
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
                                "arguments": "{\"command\": \"echo big\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called")
            yield ""  # pragma: no cover

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        output_compressor=StubOutputCompressor(),
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="run", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    assert events[1]["type"] == "tool_call_result"
    assert skills.compressed_meta_calls == [
        (
            42,
            {
                "cache_id": "cache_2",
                "original_chars": 5000,
                "compressed_chars": 120,
            },
        )
    ]


def test_pipeline_appends_compression_marker_when_missing_from_llm_reply() -> None:
    memory = StubSessionMemory()
    skills = StubSkillManager(
        output=SkillOutput(
            status="success",
            result={"stdout": "a" * 5000, "stderr": "", "exit_code": 0},
        )
    )

    marker = (
        '[📦 原始输出 5000 字符，已压缩至 120 字符。如需查看原文请说"给我看原始输出"]'
    )

    class StubOutputCompressor:
        async def compress_if_needed(
            self,
            output: str,
            metadata: dict,
            *,
            tool_name: str | None = None,
        ) -> tuple[str, bool]:
            del output, tool_name
            metadata["compressed_meta"] = {
                "cache_id": "cache_3",
                "original_chars": 5000,
                "compressed_chars": 120,
            }
            return f"compressed\n{marker}", True

    class StubRouter:
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
                                "arguments": "{\"command\": \"echo big\"}",
                            },
                        }
                    ],
                }
            return {"text": "done", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called")
            yield ""  # pragma: no cover

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        output_compressor=StubOutputCompressor(),
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="run", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    combined = "".join(event.get("text", "") for event in events if event.get("type") == "assistant_chunk")

    assert combined.startswith("done")
    assert combined.endswith(marker)

class FailingSkill(BaseSkill):
    name = "fail_skill"
    description = "Always fails"

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "always_fail",
                    "parameters": {"type": "object"},
                },
            }
        ]

    async def execute(self, tool_name: str, params: dict) -> SkillOutput:
        return SkillOutput(status="error", error_info="boom")


def test_tool_fuse_after_3_failures_returns_fused_status() -> None:
    memory = StubSessionMemory()
    breaker = CircuitBreaker(
        CircuitBreakerConfig(
            tool_level_max_failures=3,
            session_level_max_failures=99,
            cooldown_seconds=10,
            global_kill_switch=False,
        )
    )
    skills = SkillManager(circuit_breaker=breaker)
    skills.register(FailingSkill())

    class StubRouter:
        def __init__(self) -> None:
            self.calls = 0

        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, messages, tools, session_id
            self.calls += 1
            if self.calls <= 4:
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": f"call_{self.calls}",
                            "type": "function",
                            "function": {
                                "name": "always_fail",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            return {"text": "", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            yield "done"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        max_react_rounds=5,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="run", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    tool_statuses = [event["status"] for event in events if event.get("type") == "tool_call_result"]

    assert tool_statuses[2] == "fused"


def test_session_fuse_after_5_errors_returns_message() -> None:
    memory = StubSessionMemory()
    breaker = CircuitBreaker(
        CircuitBreakerConfig(
            tool_level_max_failures=99,
            session_level_max_failures=5,
            cooldown_seconds=10,
            global_kill_switch=False,
        )
    )
    skills = SkillManager(circuit_breaker=breaker)
    skills.register(FailingSkill())

    class StubRouter:
        def __init__(self) -> None:
            self.calls = 0

        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, messages, tools, session_id
            self.calls += 1
            if self.calls <= 6:
                return {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": f"call_{self.calls}",
                            "type": "function",
                            "function": {
                                "name": "always_fail",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            return {"text": "", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            yield "done"

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skills,
        max_react_rounds=6,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="run", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    combined = "".join(event.get("text", "") for event in events if event.get("type") == "assistant_chunk")

    assert "累计错误过多" in combined


def test_pipeline_falls_back_to_tool_human_summary_when_model_returns_empty_after_tool_call() -> None:
    memory = StubSessionMemory()

    class StubSkillManager:
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
            del params, session_id, skill_name
            assert tool_name == "get_notion_todo_snapshot"
            return SkillOutput(
                status="success",
                result={
                    "available": False,
                    "human_summary": "我发现了一个候选 Notion 待办数据库：HYX的计划通。请回复“确认绑定 HYX的计划通”。",
                },
            )

    class StubRouter:
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
                                "name": "get_notion_todo_snapshot",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            return {"text": "", "tool_calls": []}

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            if False:
                yield ""  # pragma: no cover
            return

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=StubSkillManager(),
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="看看今日待办", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())
    combined = "".join(event.get("text", "") for event in events if event.get("type") == "assistant_chunk")

    assert "候选 Notion 待办数据库" in combined
    assert memory.appended[-1].text


def test_pipeline_stream_shortcuts_notion_todo_request_without_llm() -> None:
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
        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            raise AssertionError("LLM should not be called for notion todo shortcut")

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            raise AssertionError("stream() should not be called for notion todo shortcut")
            yield ""  # pragma: no cover

    skill_manager = StubSkillManager()
    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skill_manager,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="查看一下今天的计划通待办事项", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())

    assert [event["type"] for event in events] == ["assistant_chunk", "assistant_done"]
    assert events[0]["text"] == "今日到期未完成：\n- 提交周报"
    assert skill_manager.calls == [("get_notion_todo_snapshot", {}, "s1", "direct")]
    assert memory.appended[-1].text == "今日到期未完成：\n- 提交周报"


def test_pipeline_stream_shortcuts_notion_todo_followup_after_binding_without_llm() -> None:
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
        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            raise AssertionError("LLM should not be called for notion todo follow-up shortcut")

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            raise AssertionError("stream() should not be called for notion todo follow-up shortcut")
            yield ""  # pragma: no cover

    skill_manager = StubSkillManager()
    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skill_manager,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text="好，查看吧", sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())

    assert [event["type"] for event in events] == ["assistant_chunk", "assistant_done"]
    assert events[0]["text"] == "今日到期未完成：\n- 提交周报"
    assert skill_manager.calls == [("get_notion_todo_snapshot", {}, "s1", "direct")]
    assert memory.appended[-1].text == "今日到期未完成：\n- 提交周报"


class ReminderRoutingSkillManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str | None, str | None]] = []

    def get_tools_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_notion_todo_snapshot",
                    "parameters": {"type": "object"},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "update_reminder",
                    "parameters": {"type": "object"},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_reminder",
                    "parameters": {"type": "object"},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "create_reminder",
                    "parameters": {"type": "object"},
                },
            },
        ]

    async def invoke(
        self,
        tool_name: str,
        params: dict,
        *,
        session_id: str | None = None,
        skill_name: str | None = None,
    ) -> SkillOutput:
        self.calls.append((tool_name, dict(params), session_id, skill_name))
        if tool_name == "get_notion_todo_snapshot":
            return SkillOutput(
                status="success",
                result={
                    "available": True,
                    "human_summary": "今日到期未完成：\n- 提交周报",
                },
            )
        return SkillOutput(
            status="success",
            result={"summary": f"{tool_name} ok"},
        )


class ReminderRoutingRouter:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
        user_message = next(
            str(message.get("content") or "")
            for message in reversed(messages)
            if message.get("role") == "user"
        )
        tool_names = [tool["function"]["name"] for tool in tools or []]
        if len(self.calls) == 0:
            if user_message == "把普拉提改到下午四点":
                decision = {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_update",
                            "type": "function",
                            "function": {
                                "name": "update_reminder",
                                "arguments": json.dumps(
                                    {
                                        "title": "普拉提",
                                        "schedule_value": "2026-04-20T16:00:00+08:00",
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                }
            elif user_message == "删掉今天的抗焦虑药":
                decision = {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_delete",
                            "type": "function",
                            "function": {
                                "name": "delete_reminder",
                                "arguments": json.dumps(
                                    {"title": "抗焦虑药"},
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                }
            elif user_message == "在计划通里把我今天普拉提的时间改成下午四点":
                decision = {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_update",
                            "type": "function",
                            "function": {
                                "name": "update_reminder",
                                "arguments": json.dumps(
                                    {
                                        "title": "普拉提",
                                        "schedule_value": "2026-04-20T16:00:00+08:00",
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                }
            elif user_message == "在计划通里加一条晚上散步":
                decision = {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "call_create",
                            "type": "function",
                            "function": {
                                "name": "create_reminder",
                                "arguments": json.dumps(
                                    {
                                        "title": "晚上散步",
                                        "schedule_type": "daily",
                                        "schedule_value": "20:00",
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                }
            else:
                decision = {"text": "done", "tool_calls": []}
        else:
            decision = {"text": "done", "tool_calls": []}

        self.calls.append(
            {
                "model_name": model_name,
                "session_id": session_id,
                "user_message": user_message,
                "tool_names": tool_names,
                "tool_calls": decision.get("tool_calls", []),
            }
        )
        return decision

    async def stream(self, model_name, messages, *, session_id=None, tools=None):
        del model_name, messages, session_id, tools
        raise AssertionError("stream should not be called")
        yield ""  # pragma: no cover


@pytest.mark.parametrize("inbound_text", ["今日计划", "/计划"])
def test_pipeline_stream_shortcuts_explicit_plan_queries_without_llm(inbound_text: str) -> None:
    memory = StubSessionMemory()
    skill_manager = ReminderRoutingSkillManager()

    class StubRouter:
        async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
            del model_name, messages, tools, session_id
            raise AssertionError("LLM should not be called for explicit plan snapshot queries")

        async def stream(self, model_name, messages, *, session_id=None, tools=None):
            del model_name, messages, session_id, tools
            raise AssertionError("stream should not be called for explicit plan snapshot queries")
            yield ""  # pragma: no cover

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skill_manager,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text=inbound_text, sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())

    assert [event["type"] for event in events] == ["assistant_chunk", "assistant_done"]
    assert events[0]["text"] == "今日到期未完成：\n- 提交周报"
    assert skill_manager.calls == [("get_notion_todo_snapshot", {}, "s1", "direct")]


@pytest.mark.parametrize(
    ("inbound_text", "expected_tool", "expected_params"),
    [
        (
            "把普拉提改到下午四点",
            "update_reminder",
            {"title": "普拉提", "schedule_value": "2026-04-20T16:00:00+08:00"},
        ),
        (
            "删掉今天的抗焦虑药",
            "delete_reminder",
            {"title": "抗焦虑药"},
        ),
        (
            "在计划通里加一条晚上散步",
            "create_reminder",
            {
                "title": "晚上散步",
                "schedule_type": "daily",
                "schedule_value": "20:00",
            },
        ),
    ],
)
def test_pipeline_stream_routes_plan_mutations_through_llm_tools(
    inbound_text: str,
    expected_tool: str,
    expected_params: dict[str, str],
) -> None:
    memory = StubSessionMemory()
    skill_manager = ReminderRoutingSkillManager()
    router = ReminderRoutingRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skill_manager,
    )

    async def _collect() -> list[dict]:
        inbound = Message(text=inbound_text, sender="user", session_id="s1")
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())

    assert events[-1]["type"] == "assistant_done"
    assert len(router.calls) >= 2
    assert router.calls[0]["user_message"] == inbound_text
    assert router.calls[0]["tool_calls"] == [
        {
            "id": f"call_{expected_tool.split('_')[0]}",
            "type": "function",
            "function": {
                "name": expected_tool,
                "arguments": json.dumps(expected_params, ensure_ascii=False),
            },
        }
    ]
    assert skill_manager.calls[0][0] == expected_tool
    assert skill_manager.calls[0][1] == expected_params


def test_pipeline_stream_routes_plan_keyword_mutation_through_llm_tools() -> None:
    memory = StubSessionMemory()
    skill_manager = ReminderRoutingSkillManager()
    router = ReminderRoutingRouter()
    pipeline = ChatPipeline(
        router=router,
        chat_model="Gemini3Pro",
        session_memory=memory,
        skill_manager=skill_manager,
    )

    async def _collect() -> list[dict]:
        inbound = Message(
            text="在计划通里把我今天普拉提的时间改成下午四点",
            sender="user",
            session_id="s1",
        )
        return [event async for event in pipeline.stream_reply(inbound)]

    events = asyncio.run(_collect())

    assert events[-1]["type"] == "assistant_done"
    assert len(router.calls) >= 2
    assert router.calls[0]["user_message"] == "在计划通里把我今天普拉提的时间改成下午四点"
    assert router.calls[0]["tool_calls"] == [
        {
            "id": "call_update",
            "type": "function",
            "function": {
                "name": "update_reminder",
                "arguments": json.dumps(
                    {
                        "title": "普拉提",
                        "schedule_value": "2026-04-20T16:00:00+08:00",
                    },
                    ensure_ascii=False,
                ),
            },
        }
    ]
    assert skill_manager.calls[0][0] == "update_reminder"
    assert skill_manager.calls[0][1] == {
        "title": "普拉提",
        "schedule_value": "2026-04-20T16:00:00+08:00",
    }
