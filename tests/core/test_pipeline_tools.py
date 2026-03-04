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
    assert events[-1]["type"] == "assistant_done"
    assert skills.calls[0][0] == "run_command"
    assert skills.calls[0][1]["command"] == "echo hi"


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
