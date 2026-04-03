from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import hypo_agent.core.heartbeat as heartbeat_module
from hypo_agent.core.config_loader import RuntimeModelConfig
from hypo_agent.core.event_queue import EventQueue
from hypo_agent.core.heartbeat import HeartbeatService, SILENT_SENTINEL
from hypo_agent.core.model_router import ModelRouter
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
                            "name": "exec_command",
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
                    "name": "exec_command",
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
        skill_name: str | None = None,
    ) -> SkillOutput:
        self.invocations.append((tool_name, dict(params), session_id, skill_name))
        return SkillOutput(
            status="success",
            result={"tool": tool_name, "ok": True},
            metadata={},
        )


class RecordingLogger:
    def __init__(self) -> None:
        self.info_calls: list[tuple[str, dict[str, Any]]] = []
        self.warning_calls: list[tuple[str, dict[str, Any]]] = []
        self.error_calls: list[tuple[str, dict[str, Any]]] = []
        self.exception_calls: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.info_calls.append((event, kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warning_calls.append((event, kwargs))

    def error(self, event: str, **kwargs: Any) -> None:
        self.error_calls.append((event, kwargs))

    def exception(self, event: str, **kwargs: Any) -> None:
        self.exception_calls.append((event, kwargs))


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
        assert [entry[0] for entry in skill_manager.invocations] == [
            "exec_command",
            "scan_emails",
            "list_reminders",
        ]
        assert skill_manager.invocations[1][1]["triggered_by"] == "heartbeat"
        assert skill_manager.invocations[1][3] == "direct"
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
        assert [entry[0] for entry in skill_manager.invocations] == [
            "exec_command",
            "scan_emails",
            "list_reminders",
        ]
        assert skill_manager.invocations[1][1]["triggered_by"] == "heartbeat"
        assert skill_manager.invocations[1][3] == "direct"
        assert len(memory.appended) == 1
        assert memory.appended[0].message_tag == "heartbeat"
        assert "重要邮件" in str(memory.appended[0].text)
        assert len(pushed) == 1
        assert pushed[0].message_tag == "heartbeat"
        assert "重要邮件" in str(pushed[0].text)

    asyncio.run(_run())


def test_heartbeat_model_timeout_falls_back_to_backup_model(tmp_path: Path) -> None:
    async def _run() -> None:
        prompt_path = tmp_path / "heartbeat_prompt.md"
        prompt_path.write_text("run heartbeat", encoding="utf-8")
        queue = EventQueue()
        memory = StubSessionMemory()
        pushed: list[Message] = []
        calls: list[dict[str, Any]] = []

        runtime = RuntimeModelConfig.model_validate(
            {
                "default_model": "GPT",
                "task_routing": {"chat": "GPT"},
                "models": {
                    "GPT": {
                        "provider": "AISTOCK",
                        "litellm_model": "openai/gpt-5.2",
                        "fallback": "KimiK25",
                        "api_base": "https://example.invalid/v1",
                        "api_key": "primary-key",
                    },
                    "KimiK25": {
                        "provider": "volcengine_coding",
                        "litellm_model": "openai/kimi-k2.5",
                        "fallback": None,
                        "api_base": "https://example.invalid/v1",
                        "api_key": "fallback-key",
                    },
                },
            }
        )

        async def fake_acompletion(**kwargs):
            calls.append({"model": kwargs["model"], "timeout": kwargs.get("timeout")})
            if kwargs["model"] == "openai/gpt-5.2":
                raise TimeoutError("primary timed out")

            async def _gen():
                yield {"choices": [{"delta": {"content": "fallback heartbeat ok"}}]}

            return _gen()

        pipeline = ChatPipeline(
            router=ModelRouter(runtime, acompletion_fn=fake_acompletion),
            chat_model="GPT",
            session_memory=memory,
            event_queue=queue,
            on_proactive_message=pushed.append,
            heartbeat_model_timeout_seconds=60,
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
        assert result["summary"] == "fallback heartbeat ok"
        assert [call["model"] for call in calls] == [
            "openai/gpt-5.2",
            "openai/kimi-k2.5",
        ]
        assert calls[0]["timeout"] == 60.0
        assert calls[1]["timeout"] == 60.0
        assert pushed[0].text == "💓 fallback heartbeat ok"

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


def test_heartbeat_tracks_consecutive_failures_and_last_success(tmp_path: Path) -> None:
    async def _run() -> None:
        prompt_path = tmp_path / "heartbeat_prompt.md"
        prompt_path.write_text("ok", encoding="utf-8")
        service = HeartbeatService(
            message_queue=AutoRespondingQueue([]),
            scheduler=StubScheduler(running=True),
            default_session_id="main",
            prompt_path=prompt_path,
        )

        async def fake_wait_for(awaitable, timeout):
            del awaitable, timeout
            raise TimeoutError

        original_wait_for = heartbeat_module.asyncio.wait_for
        heartbeat_module.asyncio.wait_for = fake_wait_for
        try:
            result = await service.run()
        finally:
            heartbeat_module.asyncio.wait_for = original_wait_for

        assert result["error"] == "timeout"
        assert service.consecutive_failures == 1
        assert service.last_success_at is None

        queue = AutoRespondingQueue(
            [
                {"type": "assistant_chunk", "text": SILENT_SENTINEL},
                {"type": "assistant_done"},
            ]
        )
        service.message_queue = queue

        result = await service.run()

        assert result["summary"] == SILENT_SENTINEL
        assert service.consecutive_failures == 0
        assert service.last_success_at is not None

    asyncio.run(_run())


def test_heartbeat_logs_start_and_push_on_success(tmp_path: Path, monkeypatch) -> None:
    async def _run() -> None:
        prompt_path = tmp_path / "heartbeat_prompt.md"
        prompt_path.write_text("ok", encoding="utf-8")
        queue = AutoRespondingQueue(
            [
                {"type": "assistant_chunk", "text": "发现异常"},
                {"type": "assistant_done"},
            ]
        )
        logger = RecordingLogger()
        monkeypatch.setattr(heartbeat_module, "logger", logger)
        service = HeartbeatService(
            message_queue=queue,
            scheduler=StubScheduler(running=True),
            default_session_id="main",
            prompt_path=prompt_path,
        )

        await service.run()

        assert logger.info_calls[0][0] == "heartbeat.start"
        assert logger.info_calls[0][1]["session_id"] == "main"
        assert logger.info_calls[-1][0] == "heartbeat.push"

    asyncio.run(_run())


def test_heartbeat_logs_error_when_prompt_missing(tmp_path: Path, monkeypatch) -> None:
    async def _run() -> None:
        logger = RecordingLogger()
        monkeypatch.setattr(heartbeat_module, "logger", logger)
        service = HeartbeatService(
            message_queue=AutoRespondingQueue([]),
            scheduler=StubScheduler(running=True),
            default_session_id="main",
            prompt_path=tmp_path / "missing-heartbeat-prompt.md",
        )

        result = await service.run()

        assert result["error"] == "prompt_missing"
        assert logger.error_calls[-1][0] == "heartbeat.prompt.missing"

    asyncio.run(_run())


def test_heartbeat_logs_timeout_as_error(tmp_path: Path, monkeypatch) -> None:
    async def _run() -> None:
        prompt_path = tmp_path / "heartbeat_prompt.md"
        prompt_path.write_text("ok", encoding="utf-8")
        logger = RecordingLogger()
        monkeypatch.setattr(heartbeat_module, "logger", logger)

        async def fake_wait_for(awaitable, timeout):
            del awaitable, timeout
            raise TimeoutError

        monkeypatch.setattr(heartbeat_module.asyncio, "wait_for", fake_wait_for)
        service = HeartbeatService(
            message_queue=AutoRespondingQueue([]),
            scheduler=StubScheduler(running=True),
            default_session_id="main",
            prompt_path=prompt_path,
        )

        result = await service.run()

        assert result["error"] == "timeout"
        assert logger.info_calls[0][0] == "heartbeat.start"
        assert logger.error_calls[-1][0] == "heartbeat.timeout"

    asyncio.run(_run())


def test_heartbeat_logs_pipeline_error_as_error(tmp_path: Path, monkeypatch) -> None:
    async def _run() -> None:
        prompt_path = tmp_path / "heartbeat_prompt.md"
        prompt_path.write_text("ok", encoding="utf-8")
        logger = RecordingLogger()
        monkeypatch.setattr(heartbeat_module, "logger", logger)
        queue = AutoRespondingQueue(
            [
                {
                    "type": "error",
                    "message": "pipeline exploded",
                }
            ]
        )
        service = HeartbeatService(
            message_queue=queue,
            scheduler=StubScheduler(running=True),
            default_session_id="main",
            prompt_path=prompt_path,
        )

        result = await service.run()

        assert result["error"] == "pipeline_error"
        assert logger.error_calls[-1][0] == "heartbeat.failed"
        assert logger.error_calls[-1][1]["error"] == "pipeline exploded"

    asyncio.run(_run())


def test_heartbeat_skips_overlapping_run_and_logs_it(tmp_path: Path, monkeypatch) -> None:
    async def _run() -> None:
        prompt_path = tmp_path / "heartbeat_prompt.md"
        prompt_path.write_text("ok", encoding="utf-8")
        logger = RecordingLogger()
        monkeypatch.setattr(heartbeat_module, "logger", logger)
        queue = EventQueue()
        service = HeartbeatService(
            message_queue=queue,
            scheduler=StubScheduler(running=True),
            default_session_id="main",
            prompt_path=prompt_path,
        )

        await service._run_lock.acquire()
        try:
            result = await service.run()
        finally:
            service._run_lock.release()

        assert result["should_push"] is False
        assert result["summary"] == SILENT_SENTINEL
        assert result["error"] == "overlap_skipped"
        assert queue.empty() is True
        assert logger.warning_calls[-1][0] == "heartbeat.skipped_overlap"

    asyncio.run(_run())


def test_heartbeat_appends_registered_event_source_context_to_prompt(tmp_path: Path) -> None:
    async def _run() -> None:
        prompt_path = tmp_path / "heartbeat_prompt.md"
        prompt_path.write_text("base prompt", encoding="utf-8")
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

        async def fake_source() -> dict[str, Any]:
            return {
                "name": "hypo_info",
                "new_items": 1,
                "items": [{"subscription": "ai-watch", "title": "阿里云 AI 产品涨价"}],
            }

        service.register_event_source("hypo_info", fake_source)

        result = await service.run()

        assert result["should_push"] is False
        inbound = queue.events[0]["message"]
        assert "hypo_info" in inbound.text
        assert "阿里云 AI 产品涨价" in inbound.text

    asyncio.run(_run())


def test_heartbeat_event_source_failure_is_isolated_and_reported(tmp_path: Path, monkeypatch) -> None:
    async def _run() -> None:
        prompt_path = tmp_path / "heartbeat_prompt.md"
        prompt_path.write_text("base prompt", encoding="utf-8")
        queue = AutoRespondingQueue(
            [
                {"type": "assistant_chunk", "text": SILENT_SENTINEL},
                {"type": "assistant_done"},
            ]
        )
        logger = RecordingLogger()
        monkeypatch.setattr(heartbeat_module, "logger", logger)
        service = HeartbeatService(
            message_queue=queue,
            scheduler=StubScheduler(running=True),
            default_session_id="main",
            prompt_path=prompt_path,
        )

        async def broken_source() -> dict[str, Any]:
            raise TimeoutError("notion api timeout")

        async def working_source() -> dict[str, Any]:
            return {"items": [{"title": "正常条目"}]}

        service.register_event_source("notion_todo", broken_source)
        service.register_event_source("hypo_info", working_source)

        result = await service.run()

        assert result["should_push"] is False
        assert result["event_sources"]["notion_todo"]["status"] == "timeout"
        assert result["event_sources"]["hypo_info"]["status"] == "success"
        inbound = queue.events[0]["message"]
        assert "正常条目" in inbound.text
        assert "notion api timeout" not in inbound.text
        assert logger.warning_calls[-1][0] == "heartbeat.event_source.failed"

    asyncio.run(_run())
