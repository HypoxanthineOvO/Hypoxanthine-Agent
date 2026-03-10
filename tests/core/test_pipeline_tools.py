from __future__ import annotations

import asyncio

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
                    "name": "run_command",
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
    ) -> SkillOutput:
        self.calls.append((tool_name, params, session_id))
        return self.output


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
    assert events == [
        {
            "type": "assistant_chunk",
            "text": "direct answer",
            "sender": "assistant",
            "session_id": "s1",
        },
        {
            "type": "assistant_done",
            "sender": "assistant",
            "session_id": "s1",
        },
    ]
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
                                "name": "run_command",
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
    assert skills.calls[0][0] == "run_command"
    assert skills.calls[0][1]["command"] == "echo hi"


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
                            "name": "run_command",
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
    assert "max react rounds" in events[-2]["text"].lower()
    assert events[-1]["type"] == "assistant_done"


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
                                "name": "run_command",
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
    assert events == [
        {
            "type": "assistant_chunk",
            "text": "slash stream ok",
            "sender": "assistant",
            "session_id": "s1",
        },
        {
            "type": "assistant_done",
            "sender": "assistant",
            "session_id": "s1",
        },
    ]
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
            return "[📦 输出已压缩 (5000 → 120 字符)。如需查看原文，请告知。]\ncompressed", True

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
                                "name": "run_command",
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
    assert events[1]["result"].startswith("[📦 输出已压缩")
    assert events[1]["compressed_meta"] == {
        "cache_id": "cache_1",
        "original_chars": 5000,
        "compressed_chars": 120,
    }
    tool_messages = [m for m in router.last_messages if m.get("role") == "tool"]
    assert tool_messages
    assert tool_messages[-1]["content"].startswith("[📦 输出已压缩")


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
            return "[📦 输出已压缩]\ncompressed", True

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
                                "name": "run_command",
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
