from __future__ import annotations

import asyncio
from pathlib import Path

from hypo_agent.core.config_loader import RuntimeModelConfig
from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.core.slash_commands import SlashCommandEntry, SlashCommandHandler
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import CircuitBreakerConfig, Message, SkillOutput
from hypo_agent.security.circuit_breaker import CircuitBreaker
from hypo_agent.skills.base import BaseSkill


class StubRouter:
    def __init__(self, config: RuntimeModelConfig) -> None:
        self.config = config

    def get_model_for_task(self, task_type: str) -> str:
        return self.config.task_routing.get(task_type, self.config.default_model)

    def get_fallback_chain(self, start_model: str) -> list[str]:
        chain: list[str] = []
        seen: set[str] = set()
        current: str | None = start_model
        while current is not None and current not in seen:
            chain.append(current)
            seen.add(current)
            current = self.config.models[current].fallback
        return chain


class EchoSkill(BaseSkill):
    name = "echo"
    description = "Echo test skill"
    required_permissions: list[str] = []

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "echo",
                    "description": "Echo input",
                    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
                },
            }
        ]

    async def execute(self, tool_name: str, params: dict) -> SkillOutput:
        del tool_name
        return SkillOutput(status="success", result={"echo": params.get("text", "")})


class FakeRepairSkill(BaseSkill):
    name = "repair_diag"
    description = "Repair diagnostics"
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        error_summary: dict[str, object] | None = None,
        tool_history: dict[str, object] | None = None,
        recent_logs: dict[str, object] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.error_summary = error_summary or {
            "hours": 24,
            "counts": {"logs": 2, "tool_failures": 3, "total": 5},
            "error_types": {"tool:agent_search.web_search": 2},
            "recent_errors": [
                {
                    "source": "tool",
                    "timestamp": "2026-04-12T00:00:00+00:00",
                    "type": "tool:agent_search.web_search",
                    "summary": "web_search",
                    "detail": "timeout",
                }
            ],
        }
        self.tool_history = tool_history or {
            "count": 2,
            "items": [
                {
                    "tool_name": "web_search",
                    "skill_name": "agent_search",
                    "error_info": "timeout",
                    "input_summary": '{"query": "Claude news"}',
                    "created_at": "2026-04-12T00:00:00+00:00",
                },
                {
                    "tool_name": "read_file",
                    "skill_name": "filesystem",
                    "error_info": "permission denied",
                    "input_summary": '{"path": "/tmp/x"}',
                    "created_at": "2026-04-11T23:00:00+00:00",
                },
            ],
        }
        self.recent_logs = recent_logs or {
            "available": True,
            "count": 2,
            "items": [
                {
                    "timestamp": "2026-04-12T00:00:00+00:00",
                    "event": "web_search timeout",
                    "logger": "hypo_agent.agent_search",
                    "context": {"error": "timeout"},
                    "raw": '{"event":"web_search timeout"}',
                }
            ],
        }

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_error_summary",
                    "parameters": {"type": "object", "properties": {"hours": {"type": "integer"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_tool_history",
                    "parameters": {"type": "object", "properties": {"hours": {"type": "integer"}}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_recent_logs",
                    "parameters": {"type": "object", "properties": {"minutes": {"type": "integer"}}},
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict) -> SkillOutput:
        self.calls.append((tool_name, dict(params)))
        if tool_name == "get_error_summary":
            return SkillOutput(status="success", result=dict(self.error_summary))
        if tool_name == "get_tool_history":
            return SkillOutput(status="success", result=dict(self.tool_history))
        if tool_name == "get_recent_logs":
            return SkillOutput(status="success", result=dict(self.recent_logs))
        return SkillOutput(status="error", error_info=f"unsupported tool: {tool_name}")


class FakeCoderSubmitSkill(BaseSkill):
    name = "coder_submit"
    description = "Coder submit"
    required_permissions: list[str] = []

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @property
    def tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "coder_submit_task",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string"},
                            "working_directory": {"type": "string"},
                        },
                    },
                },
            }
        ]

    async def execute(self, tool_name: str, params: dict) -> SkillOutput:
        del tool_name
        self.calls.append(dict(params))
        return SkillOutput(
            status="success",
            result={"task_id": "coder-fix-123", "status": "queued"},
        )


class FakeCoderTaskService:
    def __init__(self) -> None:
        self.submit_calls: list[dict[str, object]] = []
        self.status_calls: list[dict[str, object]] = []
        self.list_calls: list[str | None] = []
        self.abort_calls: list[dict[str, object]] = []
        self.attach_calls: list[dict[str, object]] = []
        self.detach_calls: list[str] = []
        self.done_calls: list[str] = []
        self.send_calls: list[dict[str, object]] = []
        self.output_calls: list[dict[str, object]] = []
        self.submit_result = {
            "task_id": "task-123",
            "status": "running",
            "working_directory": "/home/heyx/Hypo-Agent",
        }
        self.status_result = {"task_id": "task-123", "status": "running"}
        self.output_result = {"cursor": "cursor-3", "lines": [], "done": False}
        self.list_result = [
            {"taskId": "task-123", "status": "running", "model": "o4-mini"},
            {"taskId": "task-456", "status": "completed", "model": "o4-mini"},
        ]
        self.abort_result = {"task_id": "task-123", "status": "aborted"}
        self.health_result = {"status": "ok"}
        self.send_result = "Hypo-Coder API 暂不支持 session continuation。"

    async def submit_task(
        self,
        *,
        session_id: str,
        prompt: str,
        working_directory: str | None = None,
        model: str | None = None,
    ) -> dict[str, object]:
        self.submit_calls.append(
            {
                "session_id": session_id,
                "prompt": prompt,
                "working_directory": working_directory,
                "model": model,
            }
        )
        payload = dict(self.submit_result)
        if working_directory:
            payload["working_directory"] = working_directory
        return payload

    async def get_task_status(
        self,
        *,
        task_id: str,
        session_id: str | None = None,
    ) -> dict[str, object]:
        self.status_calls.append({"task_id": task_id, "session_id": session_id})
        return dict(self.status_result)

    async def list_tasks(self, *, status: str | None = None) -> list[dict[str, object]]:
        self.list_calls.append(status)
        return [dict(item) for item in self.list_result]

    async def abort_task(
        self,
        *,
        task_id: str,
        session_id: str | None = None,
    ) -> dict[str, object]:
        self.abort_calls.append({"task_id": task_id, "session_id": session_id})
        return dict(self.abort_result)

    async def attach_task(
        self,
        *,
        session_id: str,
        task_id: str,
        initial_cursor: str | None = None,
    ) -> None:
        self.attach_calls.append(
            {"session_id": session_id, "task_id": task_id, "initial_cursor": initial_cursor}
        )

    async def detach_task(self, session_id: str) -> None:
        self.detach_calls.append(session_id)

    async def mark_done(self, session_id: str) -> None:
        self.done_calls.append(session_id)

    async def send_to_task(
        self,
        *,
        session_id: str,
        instruction: str,
        task_id: str = "last",
    ) -> str:
        self.send_calls.append(
            {"session_id": session_id, "instruction": instruction, "task_id": task_id}
        )
        return self.send_result

    async def health(self) -> dict[str, object]:
        return dict(self.health_result)

    async def get_task_output(
        self,
        *,
        task_id: str,
        after: str | None = None,
    ) -> dict[str, object]:
        self.output_calls.append({"task_id": task_id, "after": after})
        return dict(self.output_result)


class FakeRepairService:
    def __init__(self) -> None:
        self.help_calls = 0
        self.report_calls: list[dict[str, object]] = []
        self.start_calls: list[dict[str, object]] = []
        self.status_calls: list[dict[str, object]] = []
        self.log_calls: list[dict[str, object]] = []
        self.abort_calls: list[dict[str, object]] = []
        self.retry_calls: list[dict[str, object]] = []

    def render_help(self) -> str:
        self.help_calls += 1
        return "repair help text"

    async def render_report(
        self,
        *,
        session_id: str,
        scope: str = "global",
        hours: int = 24,
    ) -> str:
        self.report_calls.append({"session_id": session_id, "scope": scope, "hours": hours})
        return f"repair report scope={scope} hours={hours}"

    async def start_run(
        self,
        *,
        session_id: str,
        issue: str,
        finding_id: str | None = None,
        verify_commands: list[str] | None = None,
    ) -> dict[str, object]:
        self.start_calls.append(
            {
                "session_id": session_id,
                "issue": issue,
                "finding_id": finding_id,
                "verify_commands": list(verify_commands or []),
            }
        )
        return {"status": "running", "run_id": "repair-1", "issue_text": issue or finding_id or ""}

    async def render_status(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
    ) -> str:
        self.status_calls.append({"session_id": session_id, "run_id": run_id})
        return "repair status"

    async def render_logs(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
        line_count: int = 30,
        follow: bool = False,
    ) -> str:
        self.log_calls.append(
            {
                "session_id": session_id,
                "run_id": run_id,
                "line_count": line_count,
                "follow": follow,
            }
        )
        return "repair logs"

    async def abort_run(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
    ) -> dict[str, object]:
        self.abort_calls.append({"session_id": session_id, "run_id": run_id})
        return {"status": "aborted", "run_id": run_id or "repair-1"}

    async def retry_run(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
    ) -> dict[str, object]:
        self.retry_calls.append({"session_id": session_id, "run_id": run_id})
        return {"status": "running", "run_id": "repair-2", "retry_of_run_id": run_id or "repair-1"}


async def _build_handler(tmp_path: Path) -> tuple[
    SlashCommandHandler,
    SessionMemory,
    StructuredStore,
    CircuitBreaker,
]:
    session_memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
    store = StructuredStore(db_path=tmp_path / "hypo.db")
    await store.init()
    breaker = CircuitBreaker(CircuitBreakerConfig())
    skill_manager = SkillManager(circuit_breaker=breaker)
    skill_manager.register(EchoSkill())

    runtime = RuntimeModelConfig.model_validate(
        {
            "default_model": "KimiK25",
            "task_routing": {"chat": "KimiK25", "lightweight": "DeepseekV3_2"},
            "models": {
                "KimiK25": {
                    "provider": "volcengine_coding",
                    "litellm_model": "openai/kimi-k2.5",
                    "fallback": "DeepseekV3_2",
                    "description": "chat model",
                    "api_base": "https://ark.cn-beijing.volces.com/api/coding/v3",
                    "api_key": "volc-key",
                },
                "DeepseekV3_2": {
                    "provider": "Volcengine",
                    "litellm_model": "openai/deepseek-v3.2",
                    "fallback": None,
                    "description": "lightweight model",
                    "api_base": "https://ark.cn-beijing.volces.com/api/v3",
                    "api_key": "volc-key",
                },
                "DisabledModel": {
                    "provider": None,
                    "litellm_model": None,
                    "fallback": None,
                },
            },
        }
    )
    router = StubRouter(runtime)
    coder_task_service = FakeCoderTaskService()
    handler = SlashCommandHandler(
        router=router,
        session_memory=session_memory,
        structured_store=store,
        circuit_breaker=breaker,
        skill_manager=skill_manager,
        coder_task_service=coder_task_service,
        model_probe_fn=None,
    )
    handler.coder_task_service = coder_task_service
    return handler, session_memory, store, breaker


async def _build_handler_with_repair_service(
    tmp_path: Path,
    repair_service: FakeRepairService,
) -> tuple[
    SlashCommandHandler,
    SessionMemory,
    StructuredStore,
    CircuitBreaker,
]:
    handler, session_memory, store, breaker = await _build_handler(tmp_path)
    handler = SlashCommandHandler(
        router=handler.router,
        session_memory=session_memory,
        structured_store=store,
        circuit_breaker=breaker,
        skill_manager=handler.skill_manager,
        coder_task_service=handler.coder_task_service,
        memory_gc=handler.memory_gc,
        model_probe_fn=None,
        repair_service=repair_service,
    )
    handler.coder_task_service = getattr(handler, "coder_task_service", None)
    return handler, session_memory, store, breaker


async def _build_handler_with_gc(
    tmp_path: Path,
    memory_gc,
) -> tuple[
    SlashCommandHandler,
    SessionMemory,
    StructuredStore,
    CircuitBreaker,
]:
    handler, session_memory, store, breaker = await _build_handler(tmp_path)
    handler.memory_gc = memory_gc
    return handler, session_memory, store, breaker


def test_slash_commands_returns_none_for_non_slash_message(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(Message(text="hello", sender="user", session_id="s1"))
        assert text is None

    asyncio.run(_run())


def test_help_contains_all_commands(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(Message(text="/help", sender="user", session_id="s1"))
        assert text is not None
        for entry in handler._registry:
            assert entry.command in text

    asyncio.run(_run())


def test_all_slash_commands_have_help(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)

        for entry in handler._registry:
            assert getattr(entry, "help", None) is not None, entry.command
            assert str(entry.help.brief or "").strip(), entry.command
            assert str(entry.help.usage or "").strip(), entry.command
            assert str(entry.help.description or "").strip(), entry.command
            assert isinstance(entry.help.examples, list) and entry.help.examples, entry.command
            assert str(entry.help.category or "").strip(), entry.command

    asyncio.run(_run())


def test_help_without_args_lists_by_category(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(Message(text="/help", sender="user", session_id="s1"))
        assert text is not None
        assert "## System" in text
        assert "## Session" in text
        assert "## Debug" in text
        assert "## Dev" in text
        assert "- `/codex`" in text
        assert "提交、查看、挂载和管理 Codex 编码任务" in text

    asyncio.run(_run())


def test_help_with_command_name_shows_detail(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(Message(text="/help codex", sender="user", session_id="s1"))
        assert text is not None
        assert "# /codex" in text
        assert "用法" in text
        assert "/codex <prompt>" in text
        assert "示例" in text
        assert "coder_submit_task" in text

    asyncio.run(_run())


def test_help_with_unknown_command(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(Message(text="/help xxx", sender="user", session_id="s1"))
        assert text is not None
        assert "未找到" in text
        assert "xxx" in text

    asyncio.run(_run())


def test_help_with_category(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(Message(text="/help dev", sender="user", session_id="s1"))
        assert text is not None
        assert "## Dev" in text
        assert "/codex" in text
        assert "/restart" not in text

    asyncio.run(_run())


def test_codex_help_has_examples(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        entry = next(item for item in handler._registry if item.command == "/codex")
        assert getattr(entry, "help", None) is not None
        assert any(str(example).strip() for example in entry.help.examples)

    asyncio.run(_run())


def test_help_chinese(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(Message(text="/help", sender="user", session_id="s1"))
        assert text is not None
        assert "# 斜杠指令帮助" in text
        assert "可用分组" in text
        assert "查看全部指令，或查询单条指令帮助" in text
        assert "查看当前模型路由、探测状态和用量" in text
        assert "/h, /帮助" in text

    asyncio.run(_run())


def test_registry_new_command(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        handler._registry.append(
            SlashCommandEntry(
                command="/newcmd",
                aliases=[],
                description="新增测试指令",
                handler=lambda _: "ok",
            )
        )
        text = await handler.try_handle(Message(text="/help", sender="user", session_id="s1"))
        assert text is not None
        assert "/newcmd" in text
        assert "新增测试指令" in text

    asyncio.run(_run())


def test_slash_commands_unknown_command_returns_hint(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(Message(text="/unknown", sender="user", session_id="s1"))
        assert text is not None
        assert "未知斜杠指令" in text
        assert "/help" in text

    asyncio.run(_run())


def test_slash_commands_kill_enables_and_resume_disables(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, breaker = await _build_handler(tmp_path)
        first = await handler.try_handle(Message(text="/kill", sender="user", session_id="s1"))
        second = await handler.try_handle(Message(text="/kill", sender="user", session_id="s1"))
        resume = await handler.try_handle(Message(text="/resume", sender="user", session_id="s1"))

        assert first is not None and "Kill Switch 已激活" in first
        assert second is not None and "Kill Switch 已激活" in second
        assert resume is not None and "Kill Switch 已解除" in resume
        assert breaker.get_global_kill_switch() is False

    asyncio.run(_run())


def test_slash_commands_resume_without_kill(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, breaker = await _build_handler(tmp_path)
        breaker.set_global_kill_switch(False)
        result = await handler.try_handle(Message(text="/resume", sender="user", session_id="s1"))
        assert result is not None
        assert "未处于" in result

    asyncio.run(_run())


def test_slash_commands_clear_clears_current_session(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, session_memory, _, _ = await _build_handler(tmp_path)
        session_memory.append(Message(text="u1", sender="user", session_id="s1"))
        session_memory.append(Message(text="a1", sender="assistant", session_id="s1"))

        result = await handler.try_handle(Message(text="/clear", sender="user", session_id="s1"))

        assert result is not None
        assert "清空" in result
        assert session_memory.get_messages("s1") == []

    asyncio.run(_run())


def test_slash_commands_session_list_returns_sessions(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, session_memory, _, _ = await _build_handler(tmp_path)
        session_memory.append(Message(text="u1", sender="user", session_id="s1"))
        session_memory.append(Message(text="u2", sender="user", session_id="s2"))

        result = await handler.try_handle(
            Message(text="/session list", sender="user", session_id="s1")
        )
        assert result is not None
        assert "s1" in result
        assert "s2" in result

    asyncio.run(_run())


def test_slash_commands_reminders_lists_non_deleted_by_default(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, store, _ = await _build_handler(tmp_path)
        await store.create_reminder(
            title="喝水",
            description="每小时提醒",
            schedule_type="cron",
            schedule_value="0 * * * *",
            channel="all",
            status="active",
            next_run_at="2026-03-07T09:00:00+00:00",
            heartbeat_config=None,
        )
        await store.create_reminder(
            title="错过提醒",
            description="过去时间",
            schedule_type="once",
            schedule_value="2026-03-07T08:00:00+00:00",
            channel="all",
            status="missed",
            next_run_at=None,
            heartbeat_config=None,
        )

        text = await handler.try_handle(Message(text="/reminders", sender="user", session_id="s1"))
        assert text is not None
        assert "提醒列表" in text
        assert "🟢 active" in text
        assert "⏰ missed" in text
        assert "喝水" in text
        assert "错过提醒" in text

    asyncio.run(_run())


def test_slash_commands_reminders_support_status_filter(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, store, _ = await _build_handler(tmp_path)
        await store.create_reminder(
            title="喝水",
            description="每小时提醒",
            schedule_type="cron",
            schedule_value="0 * * * *",
            channel="all",
            status="active",
            next_run_at="2026-03-07T09:00:00+00:00",
            heartbeat_config=None,
        )
        await store.create_reminder(
            title="错过提醒",
            description="过去时间",
            schedule_type="once",
            schedule_value="2026-03-07T08:00:00+00:00",
            channel="all",
            status="missed",
            next_run_at=None,
            heartbeat_config=None,
        )

        text = await handler.try_handle(
            Message(text="/reminders active", sender="user", session_id="s1")
        )
        assert text is not None
        assert "提醒列表（active）" in text
        assert "喝水" in text
        assert "错过提醒" not in text

    asyncio.run(_run())


def test_slash_commands_token_and_token_total_use_store_stats(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, store, _ = await _build_handler(tmp_path)
        await store.record_token_usage(
            session_id="s1",
            requested_model="KimiK25",
            resolved_model="KimiK25",
            input_tokens=1200,
            output_tokens=800,
            total_tokens=2000,
            latency_ms=100.0,
        )
        await store.record_token_usage(
            session_id="s2",
            requested_model="DeepseekV3_2",
            resolved_model="DeepseekV3_2",
            input_tokens=3,
            output_tokens=4,
            total_tokens=7,
            latency_ms=80.0,
        )

        token_text = await handler.try_handle(
            Message(text="/token", sender="user", session_id="s1")
        )
        total_text = await handler.try_handle(
            Message(text="/token total", sender="user", session_id="s1")
        )

        assert token_text is not None
        assert "s1" in token_text
        assert "KimiK25" in token_text

        assert total_text is not None
        assert "KimiK25" in total_text
        assert "DeepseekV3_2" in total_text
        assert "2007" in total_text

    asyncio.run(_run())


def test_slash_commands_gc_runs_memory_gc(tmp_path: Path) -> None:
    async def _run() -> None:
        class StubMemoryGC:
            def __init__(self) -> None:
                self.calls = 0

            async def run(self) -> dict[str, int]:
                self.calls += 1
                return {"processed_count": 2, "skipped_count": 3}

        memory_gc = StubMemoryGC()
        handler, _, _, _ = await _build_handler_with_gc(tmp_path, memory_gc)

        result = await handler.try_handle(Message(text="/gc", sender="user", session_id="s1"))

        assert result is not None
        assert "Memory GC" in result
        assert "processed=2" in result
        assert memory_gc.calls == 1

    asyncio.run(_run())


def test_repair_help(tmp_path: Path) -> None:
    async def _run() -> None:
        repair_service = FakeRepairService()
        handler, _, _, _ = await _build_handler_with_repair_service(tmp_path, repair_service)

        text = await handler.try_handle(Message(text="/repair help", sender="user", session_id="s1"))

        assert text == "repair help text"
        assert repair_service.help_calls == 1

    asyncio.run(_run())


def test_repair_report_and_report_session(tmp_path: Path) -> None:
    async def _run() -> None:
        repair_service = FakeRepairService()
        handler, _, _, _ = await _build_handler_with_repair_service(tmp_path, repair_service)

        report_text = await handler.try_handle(
            Message(text="/repair report", sender="user", session_id="s1")
        )
        session_text = await handler.try_handle(
            Message(text="/repair report session --hours 12", sender="user", session_id="s1")
        )

        assert report_text == "repair report scope=global hours=24"
        assert session_text == "repair report scope=session hours=12"
        assert repair_service.report_calls == [
            {"session_id": "s1", "scope": "global", "hours": 24},
            {"session_id": "s1", "scope": "session", "hours": 12},
        ]

    asyncio.run(_run())


def test_repair_do_status_logs_abort_and_retry(tmp_path: Path) -> None:
    async def _run() -> None:
        repair_service = FakeRepairService()
        handler, _, _, _ = await _build_handler_with_repair_service(tmp_path, repair_service)

        do_text = await handler.try_handle(
            Message(
                text='/repair do "Genesis QWen 工具调用后误报无法访问" --verify "pytest tests/core/test_pipeline_tools.py -q"',
                sender="user",
                session_id="s1",
            )
        )
        from_text = await handler.try_handle(
            Message(text="/repair do --from F1", sender="user", session_id="s1")
        )
        status_text = await handler.try_handle(
            Message(text="/repair status", sender="user", session_id="s1")
        )
        logs_text = await handler.try_handle(
            Message(text="/repair logs --run repair-1 -n 10 --follow", sender="user", session_id="s1")
        )
        abort_text = await handler.try_handle(
            Message(text="/repair abort --run repair-1", sender="user", session_id="s1")
        )
        retry_text = await handler.try_handle(
            Message(text="/repair retry repair-1", sender="user", session_id="s1")
        )

        assert do_text is not None and "repair-1" in do_text
        assert from_text is not None and "repair-1" in from_text
        assert status_text == "repair status"
        assert logs_text == "repair logs"
        assert abort_text is not None and "repair-1" in abort_text
        assert retry_text is not None and "repair-2" in retry_text
        assert repair_service.start_calls == [
            {
                "session_id": "s1",
                "issue": "Genesis QWen 工具调用后误报无法访问",
                "finding_id": None,
                "verify_commands": ["pytest tests/core/test_pipeline_tools.py -q"],
            },
            {
                "session_id": "s1",
                "issue": "",
                "finding_id": "F1",
                "verify_commands": [],
            },
        ]
        assert repair_service.status_calls == [{"session_id": "s1", "run_id": None}]
        assert repair_service.log_calls == [
            {"session_id": "s1", "run_id": "repair-1", "line_count": 10, "follow": True}
        ]
        assert repair_service.abort_calls == [{"session_id": "s1", "run_id": "repair-1"}]
        assert repair_service.retry_calls == [{"session_id": "s1", "run_id": "repair-1"}]

    asyncio.run(_run())


def test_repair_invalid_usage(tmp_path: Path) -> None:
    async def _run() -> None:
        repair_service = FakeRepairService()
        handler, _, _, _ = await _build_handler_with_repair_service(tmp_path, repair_service)

        text = await handler.try_handle(Message(text="/repair do", sender="user", session_id="s1"))

        assert text is not None
        assert "用法" in text

    asyncio.run(_run())


def test_restart_command(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)

        text = await handler.try_handle(Message(text="/restart", sender="user", session_id="s1"))

        assert text is not None
        assert "确认" in text
        assert "/restart confirm" in text
        assert "/restart force" in text

    asyncio.run(_run())


def test_codex_submit_uses_service_and_parses_dir(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)

        text = await handler.try_handle(
            Message(
                text="/codex 修复登录页 --dir /tmp/repo",
                sender="user",
                session_id="s1",
            )
        )

        assert text is not None
        assert "Codex 任务已提交" in text
        assert "task-123" in text
        assert "/tmp/repo" in text
        assert handler.coder_task_service.submit_calls == [
            {
                "session_id": "s1",
                "prompt": "修复登录页",
                "working_directory": "/tmp/repo",
                "model": None,
            }
        ]

    asyncio.run(_run())


def test_codex_send_status_list_abort_attach_detach_done_and_health(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)

        send_text = await handler.try_handle(
            Message(text="/codex send 再补测试", sender="user", session_id="s1")
        )
        status_text = await handler.try_handle(
            Message(text="/codex status last", sender="user", session_id="s1")
        )
        list_text = await handler.try_handle(
            Message(text="/codex list running", sender="user", session_id="s1")
        )
        abort_text = await handler.try_handle(
            Message(text="/codex abort last", sender="user", session_id="s1")
        )
        attach_text = await handler.try_handle(
            Message(text="/codex attach task-456", sender="user", session_id="s1")
        )
        detach_text = await handler.try_handle(
            Message(text="/codex detach", sender="user", session_id="s1")
        )
        done_text = await handler.try_handle(
            Message(text="/codex done", sender="user", session_id="s1")
        )
        health_text = await handler.try_handle(
            Message(text="/codex health", sender="user", session_id="s1")
        )

        assert send_text is not None
        assert "暂不支持" in send_text
        assert status_text is not None
        assert "task-123" in status_text
        assert "running" in status_text
        assert list_text is not None
        assert "Codex 任务列表" in list_text
        assert "task-456" in list_text
        assert abort_text is not None
        assert "aborted" in abort_text
        assert attach_text is not None and "task-456" in attach_text
        assert detach_text is not None and "解除" in detach_text
        assert done_text is not None and "结束" in done_text
        assert health_text is not None and "ok" in health_text

        assert handler.coder_task_service.send_calls == [
            {"session_id": "s1", "instruction": "再补测试", "task_id": "last"}
        ]
        assert handler.coder_task_service.status_calls == [
            {"task_id": "last", "session_id": "s1"},
            {"task_id": "task-456", "session_id": "s1"},
        ]
        assert handler.coder_task_service.list_calls == ["running"]
        assert handler.coder_task_service.abort_calls == [
            {"task_id": "last", "session_id": "s1"}
        ]
        assert handler.coder_task_service.attach_calls == [
            {"session_id": "s1", "task_id": "task-456", "initial_cursor": "cursor-3"}
        ]
        assert handler.coder_task_service.detach_calls == ["s1"]
        assert handler.coder_task_service.done_calls == ["s1"]
        assert handler.coder_task_service.output_calls == [{"task_id": "task-456", "after": None}]

    asyncio.run(_run())


def test_codex_attach_replays_recent_lines_and_sets_initial_cursor(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        handler.coder_task_service.status_result = {"task_id": "task-456", "status": "running"}
        handler.coder_task_service.output_result = {
            "cursor": "cursor-847",
            "lines": [f"line {idx}" for idx in range(1, 41)],
            "done": False,
        }

        text = await handler.try_handle(
            Message(text="/codex attach task-456 -n 5", sender="user", session_id="s1")
        )

        assert text is not None
        assert "📜 task-456 已产出 40 行输出，以下是最近 5 行：" in text
        assert "[Codex | task-456]" in text
        assert "line 36" in text
        assert "line 40" in text
        assert "line 35" not in text
        assert "/codex logs task-456 查看完整历史" in text
        assert handler.coder_task_service.attach_calls == [
            {"session_id": "s1", "task_id": "task-456", "initial_cursor": "cursor-847"}
        ]
        assert handler.coder_task_service.output_calls == [{"task_id": "task-456", "after": None}]

    asyncio.run(_run())


def test_codex_attach_n_zero_only_reports_status_and_count(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        handler.coder_task_service.status_result = {"task_id": "task-456", "status": "running"}
        handler.coder_task_service.output_result = {
            "cursor": "cursor-847",
            "lines": [f"line {idx}" for idx in range(1, 41)],
            "done": False,
        }

        text = await handler.try_handle(
            Message(text="/codex attach task-456 -n 0", sender="user", session_id="s1")
        )

        assert text is not None
        assert "📜 已挂载 task-456 | 状态: RUNNING | 已产出 40 行" in text
        assert "/codex logs task-456 查看历史" in text
        assert "[Codex | task-456]" not in text
        assert handler.coder_task_service.attach_calls == [
            {"session_id": "s1", "task_id": "task-456", "initial_cursor": "cursor-847"}
        ]

    asyncio.run(_run())


def test_codex_logs_returns_full_history(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        handler.coder_task_service.status_result = {"task_id": "task-456", "status": "running"}
        handler.coder_task_service.output_result = {
            "cursor": "cursor-3",
            "lines": ["first", "second", "third"],
            "done": False,
        }

        text = await handler.try_handle(
            Message(text="/codex logs task-456", sender="user", session_id="s1")
        )

        assert text is not None
        assert "📜 task-456 已产出 3 行输出：" in text
        assert "[Codex | task-456]" in text
        assert "first\nsecond\nthird" in text
        assert handler.coder_task_service.output_calls == [{"task_id": "task-456", "after": None}]

    asyncio.run(_run())


def test_model_status_markdown_table(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, store, _ = await _build_handler(tmp_path)
        async def fake_probe(model_name, config):
            del config
            return {
                "KimiK25": {"ok": True, "latency_ms": 120.0, "status_text": "✅ 成功"},
                "DeepseekV3_2": {"ok": False, "latency_ms": 95.0, "status_text": "❌ 失败: timeout"},
                "DisabledModel": {"ok": False, "latency_ms": 0.0, "status_text": "➖ 未配置"},
            }[model_name]
        handler.model_probe_fn = fake_probe
        await store.record_token_usage(
            session_id="s1",
            requested_model="KimiK25",
            resolved_model="KimiK25",
            input_tokens=5,
            output_tokens=6,
            total_tokens=11,
            latency_ms=350.0,
        )

        text = await handler.try_handle(
            Message(text="/model status", sender="user", session_id="s1")
        )
        assert text is not None
        assert "## 🤖 模型状态" in text
        assert "| 任务类型 | 模型 |" in text
        assert "| 模型 | Provider | Fallback | 最近探测 | 历史延迟 | Token (入/出/总) |" in text
        assert "✅ 成功" in text
        assert "❌ 失败: timeout" in text
        assert "|" in text and "---" in text

    asyncio.run(_run())


def test_model_status_token_format(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, store, _ = await _build_handler(tmp_path)
        async def fake_probe(model_name, config):
            del model_name, config
            return {"ok": True, "latency_ms": 120.0, "status_text": "✅ 成功"}
        handler.model_probe_fn = fake_probe
        await store.record_token_usage(
            session_id="s1",
            requested_model="KimiK25",
            resolved_model="KimiK25",
            input_tokens=1200,
            output_tokens=800,
            total_tokens=2000,
            latency_ms=350.0,
        )
        text = await handler.try_handle(
            Message(text="/model status", sender="user", session_id="s1")
        )
        assert text is not None
        assert "1.2K/800/2.0K" in text

    asyncio.run(_run())


def test_model_alias_maps_to_model_status(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)

        async def fake_probe(model_name, config):
            del model_name, config
            return {"ok": True, "latency_ms": 50.0, "status_text": "✅ 成功"}

        handler.model_probe_fn = fake_probe
        text = await handler.try_handle(Message(text="/model", sender="user", session_id="s1"))

        assert text is not None
        assert "## 🤖 模型状态" in text

    asyncio.run(_run())


def test_model_status_probe_failure_does_not_crash(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)

        async def fake_probe(model_name, config):
            del model_name, config
            raise RuntimeError("InvalidSubscription from provider")

        handler.model_probe_fn = fake_probe
        text = await handler.try_handle(
            Message(text="/model", sender="user", session_id="s1")
        )

        assert text is not None
        assert "## 🤖 模型状态" in text
        assert "❌ 失败: InvalidSubscription" in text

    asyncio.run(_run())


def test_skills_markdown_table(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(
            Message(text="/skills", sender="user", session_id="s1")
        )
        assert text is not None
        assert "## 🔧 已注册技能" in text
        assert "| 技能 | 状态 | 熔断器 | 工具 | 说明 |" in text

    asyncio.run(_run())


def test_skills_chinese(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, _ = await _build_handler(tmp_path)
        text = await handler.try_handle(
            Message(text="/skills", sender="user", session_id="s1")
        )
        assert text is not None
        assert "回显测试工具" in text
        assert "✅ 启用" in text
        assert "🟢 正常" in text

    asyncio.run(_run())


def test_skills_kill_switch_status(tmp_path: Path) -> None:
    async def _run() -> None:
        handler, _, _, breaker = await _build_handler(tmp_path)
        breaker.set_global_kill_switch(True)
        text = await handler.try_handle(
            Message(text="/skills", sender="user", session_id="s1")
        )
        assert text is not None
        assert "⚡ Kill Switch: 开启" in text
        assert "🔴 熔断" in text

    asyncio.run(_run())
