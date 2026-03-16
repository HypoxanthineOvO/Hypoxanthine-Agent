from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from hypo_agent.core.event_queue import EventQueue
from hypo_agent.core.heartbeat import HeartbeatService, SILENT_SENTINEL
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.models import Message, SkillOutput


class StubScheduler:
    def __init__(self, running: bool = True) -> None:
        self.is_running = running
        self._has_heartbeat_job = running
        self._active_jobs = 1 if running else 0

    def has_job_id(self, job_id: str) -> bool:
        return job_id == "heartbeat" and self._has_heartbeat_job

    def get_active_job_count(self) -> int:
        return self._active_jobs


class AutoRespondingQueue:
    def __init__(self, response_payloads: list[dict[str, Any]]) -> None:
        self.response_payloads = list(response_payloads)
        self.events: list[dict[str, Any]] = []

    async def put(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if str(event.get("event_type") or "").strip().lower() != "user_message":
            return
        emit = event.get("emit")
        assert callable(emit)
        for payload in self.response_payloads:
            result = emit(payload)
            if asyncio.iscoroutine(result):
                await result


class StubSessionMemory:
    def __init__(self) -> None:
        self.appended: list[Message] = []
        self.history: list[Message] = []

    def append(self, message: Message) -> None:
        self.appended.append(message)

    def get_recent_messages(self, session_id: str, limit: int | None = None) -> list[Message]:
        del session_id, limit
        return list(self.history)


class HeartbeatRouter:
    def __init__(self, final_text: str) -> None:
        self.final_text = final_text
        self.call_count = 0

    async def call(self, model_name, messages, *, session_id=None, tools=None):
        del model_name, messages, session_id, tools
        return self.final_text

    async def call_with_tools(self, model_name, messages, *, tools=None, session_id=None):
        del model_name, tools, session_id
        self.call_count += 1
        if self.call_count == 1:
            return {
                "text": "",
                "tool_calls": [
                    {
                        "id": "call-run-command",
                        "type": "function",
                        "function": {
                            "name": "run_command",
                            "arguments": json.dumps({"command": "uptime"}, ensure_ascii=False),
                        },
                    },
                    {
                        "id": "call-scan-emails",
                        "type": "function",
                        "function": {
                            "name": "scan_emails",
                            "arguments": json.dumps({"unread_only": True}, ensure_ascii=False),
                        },
                    },
                    {
                        "id": "call-list-reminders",
                        "type": "function",
                        "function": {
                            "name": "list_reminders",
                            "arguments": json.dumps({"status": "active"}, ensure_ascii=False),
                        },
                    },
                ],
            }
        assert any(item.get("role") == "tool" for item in messages)
        return {"text": self.final_text, "tool_calls": []}

    async def stream(self, model_name, messages, *, session_id=None, tools=None):
        del model_name, messages, session_id, tools
        if False:  # pragma: no cover
            yield ""


class RecordingSkillManager:
    def __init__(self) -> None:
        self.invocations: list[tuple[str, dict[str, Any], str | None]] = []

    def get_tools_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "scan_emails",
                    "parameters": {
                        "type": "object",
                        "properties": {"unread_only": {"type": "boolean"}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_reminders",
                    "parameters": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                    },
                },
            },
        ]

    async def invoke(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> SkillOutput:
        self.invocations.append((tool_name, dict(params), session_id))
        return SkillOutput(
            status="success",
            result={"tool": tool_name, "ok": True},
            metadata={},
        )


def test_heartbeat_reads_prompt_and_enqueues_non_silent_push(tmp_path: Path) -> None:
    async def _run() -> None:
        prompt_path = tmp_path / "heartbeat_prompt.md"
        prompt_path.write_text("# prompt\ncheck it", encoding="utf-8")
        queue = AutoRespondingQueue(
            [
                {"type": "assistant_chunk", "text": "发现 1 条异常"},
                {"type": "assistant_done"},
            ]
        )
        service = HeartbeatService(
            message_queue=queue,
            scheduler=StubScheduler(running=True),
            default_session_id="main",
            prompt_path=prompt_path,
        )

        result = await service.run()

        assert result["should_push"] is True
        assert len(queue.events) == 2
        user_event = queue.events[0]
        assert user_event["event_type"] == "user_message"
        inbound = user_event["message"]
        assert isinstance(inbound, Message)
        assert inbound.metadata["source"] == "heartbeat"
        assert inbound.text == "# prompt\ncheck it"

        proactive_event = queue.events[1]
        assert proactive_event["event_type"] == "heartbeat_trigger"
        assert proactive_event["summary"] == "发现 1 条异常"

    asyncio.run(_run())


def test_heartbeat_silent_sentinel_does_not_enqueue_proactive_push(tmp_path: Path) -> None:
    async def _run() -> None:
        prompt_path = tmp_path / "heartbeat_prompt.md"
        prompt_path.write_text("silent test", encoding="utf-8")
        queue = AutoRespondingQueue(
            [
                {"type": "assistant_chunk", "text": SILENT_SENTINEL},
                {"type": "assistant_done"},
            ]
        )
        service = HeartbeatService(
            message_queue=queue,
            scheduler=StubScheduler(running=True),
            default_session_id="main",
            prompt_path=prompt_path,
        )

        result = await service.run()

        assert result["should_push"] is False
        assert result["summary"] == SILENT_SENTINEL
        assert len(queue.events) == 1
        assert queue.events[0]["event_type"] == "user_message"

    asyncio.run(_run())


def test_heartbeat_pipeline_invokes_tools_and_stays_silent_when_agent_returns_sentinel(
    tmp_path: Path,
) -> None:
    async def _run() -> None:
        prompt_path = tmp_path / "heartbeat_prompt.md"
        prompt_path.write_text("run heartbeat", encoding="utf-8")
        queue = EventQueue()
        memory = StubSessionMemory()
        pushed: list[Message] = []
        skill_manager = RecordingSkillManager()
        pipeline = ChatPipeline(
            router=HeartbeatRouter(SILENT_SENTINEL),
            chat_model="Gemini3Pro",
            session_memory=memory,
            skill_manager=skill_manager,
            event_queue=queue,
            on_proactive_message=pushed.append,
        )
        service = HeartbeatService(
            message_queue=queue,
            scheduler=StubScheduler(running=True),
            default_session_id="main",
            prompt_path=prompt_path,
        )

        await pipeline.start_event_consumer()
        result = await service.run()
        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert result["should_push"] is False
        assert [name for name, _, _ in skill_manager.invocations] == [
            "run_command",
            "scan_emails",
            "list_reminders",
        ]
        assert memory.appended == []
        assert pushed == []

    asyncio.run(_run())


def test_heartbeat_pipeline_invokes_tools_and_emits_single_final_push(tmp_path: Path) -> None:
    async def _run() -> None:
        prompt_path = tmp_path / "heartbeat_prompt.md"
        prompt_path.write_text("run heartbeat", encoding="utf-8")
        queue = EventQueue()
        memory = StubSessionMemory()
        pushed: list[Message] = []
        skill_manager = RecordingSkillManager()
        pipeline = ChatPipeline(
            router=HeartbeatRouter("⚠️ 检查到 1 封重要邮件"),
            chat_model="Gemini3Pro",
            session_memory=memory,
            skill_manager=skill_manager,
            event_queue=queue,
            on_proactive_message=pushed.append,
        )
        service = HeartbeatService(
            message_queue=queue,
            scheduler=StubScheduler(running=True),
            default_session_id="main",
            prompt_path=prompt_path,
        )

        await pipeline.start_event_consumer()
        result = await service.run()
        await asyncio.sleep(0.05)
        await pipeline.stop_event_consumer()

        assert result["should_push"] is True
        assert [name for name, _, _ in skill_manager.invocations] == [
            "run_command",
            "scan_emails",
            "list_reminders",
        ]
        assert len(memory.appended) == 1
        assert memory.appended[0].message_tag == "heartbeat"
        assert "重要邮件" in str(memory.appended[0].text)
        assert len(pushed) == 1
        assert pushed[0].message_tag == "heartbeat"
        assert "重要邮件" in str(pushed[0].text)

    asyncio.run(_run())


def test_heartbeat_status_reports_running_when_scheduler_has_active_jobs() -> None:
    service = HeartbeatService(
        message_queue=AutoRespondingQueue([]),
        scheduler=StubScheduler(running=True),
        default_session_id="main",
    )
    service.last_heartbeat_at = "2026-03-13T12:00:00+00:00"

    class SchedulerWithoutDedicatedHeartbeatJob:
        is_running = True

        def has_job_id(self, job_id: str) -> bool:
            del job_id
            return False

        def get_active_job_count(self) -> int:
            return 2

    status = service.get_status(scheduler=SchedulerWithoutDedicatedHeartbeatJob())

    assert status["status"] == "running"
    assert status["active_tasks"] == 2

