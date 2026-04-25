from __future__ import annotations

import asyncio
import json
from pathlib import Path

from hypo_agent.channels.codex_bridge import CodexThread
from hypo_agent.core.recent_logs import clear_recent_logs, record_recent_log
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import Message


class FakeCodexBridge:
    def __init__(self) -> None:
        self.submit_calls: list[dict[str, object]] = []
        self.continue_calls: list[dict[str, object]] = []
        self.abort_calls: list[str] = []
        self.callbacks: dict[str, object] = {}
        self.event_callbacks: dict[str, object] = {}
        self.thread_counter = 0
        self.fail_next_submit = False

    async def submit(
        self,
        run_id: str,
        prompt: str,
        working_dir: str,
        on_complete,
        on_event=None,
    ) -> CodexThread:
        self.submit_calls.append(
            {
                "run_id": run_id,
                "prompt": prompt,
                "working_dir": working_dir,
            }
        )
        self.callbacks[run_id] = on_complete
        self.event_callbacks[run_id] = on_event
        self.thread_counter += 1
        if self.fail_next_submit:
            thread = CodexThread(
                thread_id="",
                run_id=run_id,
                working_dir=working_dir,
                status="failed",
                result="spawn codex ENOENT",
            )
            resolved = on_complete(run_id, "failed", thread.result)
            if hasattr(resolved, "__await__"):
                await resolved
            return thread
        return CodexThread(
            thread_id=f"thread-{self.thread_counter}",
            run_id=run_id,
            working_dir=working_dir,
            status="running",
        )

    async def continue_thread(
        self,
        run_id: str,
        thread_id: str,
        prompt: str,
        working_dir: str,
        on_complete,
        on_event=None,
    ) -> CodexThread:
        self.continue_calls.append(
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "prompt": prompt,
                "working_dir": working_dir,
            }
        )
        self.callbacks[run_id] = on_complete
        self.event_callbacks[run_id] = on_event
        return CodexThread(
            thread_id=thread_id,
            run_id=run_id,
            working_dir=working_dir,
            status="running",
        )

    async def abort(self, run_id: str) -> None:
        self.abort_calls.append(run_id)

    def get_status(self, run_id: str) -> CodexThread | None:
        return None

    async def complete(self, run_id: str, status: str, result: str | None) -> None:
        callback = self.callbacks[run_id]
        resolved = callback(run_id, status, result)
        if hasattr(resolved, "__await__"):
            await resolved

    async def emit_event(self, run_id: str, event_type: str, payload: dict[str, object]) -> None:
        callback = self.event_callbacks.get(run_id)
        if callback is None:
            return
        resolved = callback(run_id, event_type, payload)
        if hasattr(resolved, "__await__"):
            await resolved


def test_repair_service_report_detects_known_pattern_and_lists_history(tmp_path: Path) -> None:
    async def _run() -> None:
        from hypo_agent.core.repair_service import RepairService

        clear_recent_logs()
        store = StructuredStore(db_path=tmp_path / "hypo.db")
        await store.init()
        memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
        bridge = FakeCodexBridge()

        memory.append(
            Message(text="帮我看一下配置", sender="user", session_id="s1")
        )
        memory.append(
            Message(
                text="我无法访问该文件，但前面工具已经拿到了结果。",
                sender="assistant",
                session_id="s1",
                metadata={"provider": "Genesis", "model": "openai/qwen3.5-122b"},
            )
        )
        await store.record_tool_invocation(
            session_id="s1",
            tool_name="read_file",
            skill_name="filesystem",
            params_json='{"path":"src/hypo_agent/core/pipeline.py"}',
            status="success",
            result_summary="read ok",
            duration_ms=12,
            error_info=None,
        )
        record_recent_log(
            level="error",
            message="web_search timeout",
            detail="tool timeout",
            source="hypo_agent.search",
        )
        await store.create_repair_run(
            run_id="repair-old",
            session_id="s1",
            issue_text="repair old issue",
            working_directory="/home/heyx/Hypo-Agent",
            status="completed",
            verification_state="passed",
            restart_state="skipped",
            diagnostic_snapshot_json="{}",
            report_markdown="done",
        )

        service = RepairService(
            structured_store=store,
            session_memory=memory,
            codex_bridge=bridge,
            repo_root="/home/heyx/Hypo-Agent",
        )

        text = await service.render_report(session_id="s1", scope="global", hours=24)

        assert "当前状态" in text
        assert "错误摘要" in text
        assert "repair 历史" in text
        assert "genesis_qwen_tool_access_false_negative" in text
        assert "F1" in text
        assert "repair-old" in text

    asyncio.run(_run())


def test_repair_service_start_run_rejects_when_active_run_exists(tmp_path: Path) -> None:
    async def _run() -> None:
        from hypo_agent.core.repair_service import RepairService

        store = StructuredStore(db_path=tmp_path / "hypo.db")
        await store.init()
        memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
        bridge = FakeCodexBridge()
        await store.create_repair_run(
            run_id="repair-active",
            session_id="s1",
            issue_text="running issue",
            working_directory="/home/heyx/Hypo-Agent",
            status="running",
            verification_state="pending",
            restart_state="not_requested",
            diagnostic_snapshot_json="{}",
        )

        service = RepairService(
            structured_store=store,
            session_memory=memory,
            codex_bridge=bridge,
            repo_root="/home/heyx/Hypo-Agent",
        )

        payload = await service.start_run(session_id="s1", issue="new issue")

        assert payload["status"] == "blocked"
        assert payload["run_id"] == "repair-active"
        assert bridge.submit_calls == []

    asyncio.run(_run())


def test_repair_service_start_run_injects_verify_commands_and_finding_context(tmp_path: Path) -> None:
    async def _run() -> None:
        from hypo_agent.core.repair_service import RepairService

        clear_recent_logs()
        store = StructuredStore(db_path=tmp_path / "hypo.db")
        await store.init()
        memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
        bridge = FakeCodexBridge()

        memory.append(
            Message(
                text="工具已经执行成功，但我还是无法访问文件。",
                sender="assistant",
                session_id="s1",
                metadata={"provider": "Genesis", "model": "openai/qwen3.5-122b"},
            )
        )
        await store.record_tool_invocation(
            session_id="s1",
            tool_name="read_file",
            skill_name="filesystem",
            params_json='{"path":"src/hypo_agent/core/pipeline.py"}',
            status="success",
            result_summary="read ok",
            duration_ms=8,
            error_info=None,
        )

        service = RepairService(
            structured_store=store,
            session_memory=memory,
            codex_bridge=bridge,
            repo_root="/home/heyx/Hypo-Agent",
        )

        report = await service.render_report(session_id="s1", scope="global", hours=24)
        assert "F1" in report

        payload = await service.start_run(
            session_id="s1",
            issue="",
            finding_id="F1",
            verify_commands=["pytest tests/core/test_pipeline_tools.py -q", "bash test_run.sh"],
        )

        assert payload["status"] == "running"
        assert payload["working_directory"] == "/home/heyx/Hypo-Agent"
        prompt = str(bridge.submit_calls[0]["prompt"])
        assert "Genesis QWen" in prompt or "genesis_qwen_tool_access_false_negative" in prompt
        assert "pytest tests/core/test_pipeline_tools.py -q" in prompt
        assert "bash test_run.sh" in prompt
        assert "git status" in prompt
        run = await store.get_repair_run(str(payload["run_id"]))
        assert run is not None
        assert run["codex_thread_id"] == "thread-1"

    asyncio.run(_run())


def test_repair_service_start_run_marks_failed_when_bridge_submit_fails_immediately(tmp_path: Path) -> None:
    async def _run() -> None:
        from hypo_agent.core.repair_service import RepairService

        store = StructuredStore(db_path=tmp_path / "hypo.db")
        await store.init()
        memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
        bridge = FakeCodexBridge()
        bridge.fail_next_submit = True

        service = RepairService(
            structured_store=store,
            session_memory=memory,
            codex_bridge=bridge,
            repo_root="/home/heyx/Hypo-Agent",
        )

        payload = await service.start_run(session_id="s1", issue="broken")
        row = await store.get_latest_repair_run_for_session("s1")

        assert payload["status"] == "failed"
        assert row is not None
        assert row["status"] == "failed"
        assert "ENOENT" in row["last_error"]

    asyncio.run(_run())


def test_repair_service_retry_links_new_run(tmp_path: Path) -> None:
    async def _run() -> None:
        from hypo_agent.core.repair_service import RepairService

        store = StructuredStore(db_path=tmp_path / "hypo.db")
        await store.init()
        memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
        bridge = FakeCodexBridge()
        await store.create_repair_run(
            run_id="repair-1",
            session_id="s1",
            issue_text="repair issue",
            working_directory="/home/heyx/Hypo-Agent",
            status="failed",
            verification_state="failed",
            restart_state="skipped",
            diagnostic_snapshot_json=json.dumps({"summary": "old"}, ensure_ascii=False),
            codex_thread_id="thread-old",
            report_markdown="failed",
        )

        service = RepairService(
            structured_store=store,
            session_memory=memory,
            codex_bridge=bridge,
            repo_root="/home/heyx/Hypo-Agent",
        )

        payload = await service.retry_run(session_id="s1", run_id="repair-1")
        latest = await store.get_latest_repair_run_for_session("s1")

        assert payload["status"] == "running"
        assert latest is not None
        assert latest["retry_of_run_id"] == "repair-1"
        assert bridge.continue_calls[0]["thread_id"] == "thread-old"
        assert "上次 repair 失败信息" in str(bridge.continue_calls[0]["prompt"])

    asyncio.run(_run())


def test_repair_service_terminal_update_parses_report_and_requests_restart(tmp_path: Path) -> None:
    async def _run() -> None:
        from hypo_agent.core.repair_service import RepairService

        pushed: list[str] = []
        restart_calls: list[dict[str, object]] = []

        async def push(message) -> None:
            pushed.append(str(message.text))

        async def restart_handler(*, reason: str, force: bool = False) -> str:
            restart_calls.append({"reason": reason, "force": force})
            return "正在执行有限自重启。"

        store = StructuredStore(db_path=tmp_path / "hypo.db")
        await store.init()
        memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
        bridge = FakeCodexBridge()
        await store.create_repair_run(
            run_id="repair-1",
            session_id="s1",
            issue_text="repair issue",
            working_directory="/home/heyx/Hypo-Agent",
            status="running",
            verification_state="pending",
            restart_state="not_requested",
            diagnostic_snapshot_json="{}",
            codex_thread_id="thread-1",
        )

        service = RepairService(
            structured_store=store,
            session_memory=memory,
            codex_bridge=bridge,
            repo_root="/home/heyx/Hypo-Agent",
            proactive_callback=push,
            restart_handler=restart_handler,
        )
        await service._on_repair_complete(
            "repair-1",
            "completed",
            (
                "修复完成。\n"
                "```json\n"
                '{"status":"completed","root_cause":"prompt mismatch","changed_files":["src/hypo_agent/core/pipeline.py"],'
                '"verification":{"passed":true,"commands":["pytest tests/core/test_pipeline_tools.py -q"]},'
                '"needs_restart":true,"confidence":"high","followups":[]}\n'
                "```"
            ),
        )
        row = await store.get_repair_run("repair-1")

        assert row is not None
        assert row["status"] == "completed"
        assert row["verification_state"] == "passed"
        assert row["restart_state"] == "executed"
        assert restart_calls == [{"reason": "repair repair-1 completed", "force": False}]
        assert any("Repair Report" in item for item in pushed)

    asyncio.run(_run())


def test_repair_service_terminal_update_downgrades_unparseable_report(tmp_path: Path) -> None:
    async def _run() -> None:
        from hypo_agent.core.repair_service import RepairService

        store = StructuredStore(db_path=tmp_path / "hypo.db")
        await store.init()
        memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
        bridge = FakeCodexBridge()
        await store.create_repair_run(
            run_id="repair-1",
            session_id="s1",
            issue_text="repair issue",
            working_directory="/home/heyx/Hypo-Agent",
            status="running",
            verification_state="pending",
            restart_state="not_requested",
            diagnostic_snapshot_json="{}",
            codex_thread_id="thread-1",
        )

        service = RepairService(
            structured_store=store,
            session_memory=memory,
            codex_bridge=bridge,
            repo_root="/home/heyx/Hypo-Agent",
        )
        await service._on_repair_complete("repair-1", "completed", "修复完成，但没有结构化 JSON。")
        row = await store.get_repair_run("repair-1")
        events = await store.list_repair_run_events("repair-1")

        assert row is not None
        assert row["status"] == "needs_review"
        assert row["verification_state"] == "unknown"
        assert row["restart_state"] == "not_requested"
        assert events
        assert "修复完成，但没有结构化 JSON" in events[0]["summary"] or "summary" in events[0]["payload_json"]

    asyncio.run(_run())


def test_repair_service_render_logs_includes_raw_output_payload(tmp_path: Path) -> None:
    async def _run() -> None:
        from hypo_agent.core.repair_service import RepairService

        store = StructuredStore(db_path=tmp_path / "hypo.db")
        await store.init()
        memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
        bridge = FakeCodexBridge()
        await store.create_repair_run(
            run_id="repair-1",
            session_id="s1",
            issue_text="repair issue",
            working_directory="/home/heyx/Hypo-Agent",
            status="needs_review",
            verification_state="unknown",
            restart_state="not_requested",
            diagnostic_snapshot_json="{}",
        )
        await store.append_repair_run_event(
            run_id="repair-1",
            event_type="task.raw_output",
            source="codex_bridge",
            summary="",
            payload_json=json.dumps({"result": "raw output body"}, ensure_ascii=False),
        )

        service = RepairService(
            structured_store=store,
            session_memory=memory,
            codex_bridge=bridge,
            repo_root="/home/heyx/Hypo-Agent",
        )

        text = await service.render_logs(session_id="s1", run_id="repair-1", line_count=10)

        assert "raw output body" in text

    asyncio.run(_run())


def test_repair_service_stream_events_are_persisted_and_pushed(tmp_path: Path) -> None:
    async def _run() -> None:
        from hypo_agent.core.repair_service import RepairService

        pushed: list[str] = []

        async def push(message) -> None:
            pushed.append(str(message.text))

        store = StructuredStore(db_path=tmp_path / "hypo.db")
        await store.init()
        memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
        bridge = FakeCodexBridge()
        service = RepairService(
            structured_store=store,
            session_memory=memory,
            codex_bridge=bridge,
            repo_root="/home/heyx/Hypo-Agent",
            proactive_callback=push,
        )

        payload = await service.start_run(session_id="s1", issue="stream issue")
        run_id = str(payload["run_id"])
        await bridge.emit_event(run_id, "agent_message_delta", {"delta": "正在查看 pipeline.py"})
        await bridge.emit_event(run_id, "thread_status", {"status": "running"})
        await bridge.emit_event(
            run_id,
            "item_completed",
            {"type": "agentMessage", "text": "正在查看 pipeline.py"},
        )
        text = await service.render_logs(session_id="s1", run_id=run_id, line_count=20)
        events = await store.list_repair_run_events(run_id)

        assert "正在查看 pipeline.py" in text
        assert any("正在查看 pipeline.py" in item for item in pushed)
        assert any(event["event_type"] == "agent_message_delta" for event in events)

    asyncio.run(_run())


def test_repair_service_batches_agent_message_deltas_into_single_push(tmp_path: Path) -> None:
    async def _run() -> None:
        from hypo_agent.core.repair_service import RepairService

        pushed: list[str] = []

        async def push(message) -> None:
            pushed.append(str(message.text))

        store = StructuredStore(db_path=tmp_path / "hypo.db")
        await store.init()
        memory = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
        bridge = FakeCodexBridge()
        service = RepairService(
            structured_store=store,
            session_memory=memory,
            codex_bridge=bridge,
            repo_root="/home/heyx/Hypo-Agent",
            proactive_callback=push,
        )

        payload = await service.start_run(session_id="s1", issue="stream issue")
        run_id = str(payload["run_id"])
        for chunk in ["正", "在", "查", "看", " ", "p", "i", "p", "e", "l", "i", "n", "e", ".", "p", "y"]:
            await bridge.emit_event(run_id, "agent_message_delta", {"delta": chunk})
        await bridge.emit_event(
            run_id,
            "item_completed",
            {"type": "agentMessage", "text": "正在查看 pipeline.py"},
        )

        assert pushed == [f"[Repair | {run_id}]\n正在查看 pipeline.py"]

    asyncio.run(_run())
