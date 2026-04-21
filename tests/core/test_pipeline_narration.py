from __future__ import annotations

import asyncio

from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.models import Message, SkillOutput


class StubSessionMemory:
    def __init__(self) -> None:
        self.appended: list[Message] = []

    def get_recent_messages(self, session_id: str, limit: int | None = None) -> list[Message]:
        del session_id, limit
        return []

    def append(self, message: Message) -> None:
        self.appended.append(message)


class StubRouter:
    def __init__(self) -> None:
        self._step = 0

    async def call_with_tools(
        self,
        model_name: str,
        messages: list[dict[str, object]],
        *,
        tools: list[dict[str, object]] | None = None,
        session_id: str | None = None,
    ) -> dict[str, object]:
        del model_name, messages, tools, session_id
        self._step += 1
        if self._step == 1:
            return {
                "text": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "scan_emails",
                            "arguments": "{\"limit\": 5}",
                        },
                    }
                ],
            }
        return {
            "text": "扫描完了",
            "tool_calls": [],
        }


class StubSkillManager:
    def __init__(self, *, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds

    def get_tools_schema(self) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "scan_emails",
                    "description": "扫描邮箱",
                    "parameters": {"type": "object"},
                },
            }
        ]

    async def invoke(
        self,
        tool_name: str,
        params: dict[str, object],
        *,
        session_id: str | None = None,
        skill_name: str | None = None,
    ) -> SkillOutput:
        assert tool_name == "scan_emails"
        assert params == {"limit": 5}
        assert session_id == "main"
        assert skill_name == "direct"
        await asyncio.sleep(self.delay_seconds)
        return SkillOutput(status="success", result={"emails": []})


class StubObserver:
    def __init__(self, *, delay_seconds: float = 0.0, text: str = "我去翻一下你的收件箱。") -> None:
        self.delay_seconds = delay_seconds
        self.text = text
        self.calls: list[dict[str, object]] = []

    async def maybe_narrate(
        self,
        tool_name: str,
        tool_args: dict[str, object],
        user_message_context: str,
        *,
        session_id: str | None = None,
        iteration_number: int = 0,
        total_tools_called: int = 0,
    ) -> str | None:
        self.calls.append(
            {
                "tool_name": tool_name,
                "tool_args": dict(tool_args),
                "user_message_context": user_message_context,
                "session_id": session_id,
                "iteration_number": iteration_number,
                "total_tools_called": total_tools_called,
            }
        )
        await asyncio.sleep(self.delay_seconds)
        return self.text


async def _collect_events(pipeline: ChatPipeline, inbound: Message) -> list[dict[str, object]]:
    return [event async for event in pipeline.stream_reply(inbound)]


def test_pipeline_emits_narration_callback_without_persisting_it() -> None:
    memory = StubSessionMemory()
    observer = StubObserver()
    narrations: list[dict[str, object]] = []

    async def on_narration(
        payload: dict[str, object],
        *,
        origin_channel: str | None = None,
        sender_id: str | None = None,
    ) -> None:
        narrations.append(
            {
                **payload,
                "origin_channel": origin_channel,
                "sender_id": sender_id,
            }
        )

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        skill_manager=StubSkillManager(delay_seconds=0.05),
        narration_observer=observer,
        on_narration=on_narration,
    )

    events = asyncio.run(
        _collect_events(
            pipeline,
            Message(text="帮我看邮件", sender="user", session_id="main"),
        )
    )

    assert narrations
    assert narrations[0]["type"] == "narration"
    assert narrations[0]["text"] == "我去翻一下你的收件箱。"
    assert narrations[0]["session_id"] == "main"
    assert narrations[0]["origin_channel"] == "webui"
    assert "timestamp" in narrations[0]
    assert [message.sender for message in memory.appended] == ["user", "assistant"]
    assert all(message.text != "我去翻一下你的收件箱。" for message in memory.appended)
    assert events[-1]["type"] == "assistant_done"


def test_pipeline_cancels_pending_qq_narration_for_fast_tool() -> None:
    memory = StubSessionMemory()
    observer = StubObserver(delay_seconds=0.05)
    narrations: list[dict[str, object]] = []

    async def on_narration(payload: dict[str, object], **_: object) -> None:
        narrations.append(payload)

    pipeline = ChatPipeline(
        router=StubRouter(),
        chat_model="Gemini3Pro",
        session_memory=memory,
        history_window=20,
        skill_manager=StubSkillManager(delay_seconds=0.0),
        narration_observer=observer,
        on_narration=on_narration,
    )

    async def _run() -> None:
        await _collect_events(
            pipeline,
            Message(
                text="帮我看邮件",
                sender="user",
                session_id="main",
                channel="qq",
                sender_id="1654164391",
            ),
        )
        await asyncio.sleep(0.06)

    asyncio.run(_run())

    assert observer.calls
    assert narrations == []
