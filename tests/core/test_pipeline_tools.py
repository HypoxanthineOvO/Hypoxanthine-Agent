from __future__ import annotations

import asyncio
import json

import hypo_agent.core.pipeline as pipeline_module
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.models import Message, SkillOutput


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
    assert all(event["sender"] == "assistant" for event in events)
    assert all(event["session_id"] == "s1" for event in events)
    assert all(str(event["timestamp"]).endswith("Z") for event in events)
    assert memory.appended[-1].text == "direct answer"


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
    skills = StubSkillManager()
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

    assert pipeline.max_react_rounds >= 15


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
    assert all(str(event["timestamp"]).endswith("Z") for event in events)
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
            self.calls: list[tuple[str, dict]] = []

        async def compress_if_needed(self, output: str, metadata: dict) -> tuple[str, bool]:
            self.calls.append((output, metadata))
            metadata["compressed_meta"] = {
                "cache_id": "cache_1",
                "original_chars": 5000,
                "compressed_chars": 120,
            }
            return (
                "compressed\n"
                "[📦 Output compressed from 5000 → 120 chars. Original saved to logs. "
                "Ask me for details.]"
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
    assert events[1]["type"] == "tool_call_result"
    assert str(events[1]["result"]).endswith("Ask me for details.]")
    assert events[1]["compressed_meta"] == {
        "cache_id": "cache_1",
        "original_chars": 5000,
        "compressed_chars": 120,
    }
    tool_messages = [m for m in router.last_messages if m.get("role") == "tool"]
    assert tool_messages
    assert str(tool_messages[-1]["content"]).endswith("Ask me for details.]")


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
        async def compress_if_needed(self, output: str, metadata: dict) -> tuple[str, bool]:
            del output
            metadata["compressed_meta"] = {
                "cache_id": "cache_2",
                "original_chars": 5000,
                "compressed_chars": 120,
            }
            return (
                "compressed\n"
                "[📦 Output compressed from 5000 → 120 chars. Original saved to logs. "
                "Ask me for details.]"
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
        "[📦 Output compressed from 5000 → 120 chars. Original saved to logs. "
        "Ask me for details.]"
    )

    class StubOutputCompressor:
        async def compress_if_needed(self, output: str, metadata: dict) -> tuple[str, bool]:
            del output
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


from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.models import CircuitBreakerConfig
from hypo_agent.security.circuit_breaker import CircuitBreaker
from hypo_agent.skills.base import BaseSkill


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
            if self.calls <= 3:
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
            if self.calls <= 5:
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
