from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable

from hypo_agent.channels.codex_bridge import CodexBridge
from hypo_agent.core.recent_logs import get_recent_logs
from hypo_agent.models import Message


_ACCESS_DENIED_PATTERNS = (
    "无法访问",
    "无法读取",
    "没有权限",
    "access denied",
    "cannot access",
    "permission denied",
)

_GENESIS_QWEN_ISSUE_TEXT = "Genesis QWen 工具调用后误报无法访问"


class RepairService:
    def __init__(
        self,
        *,
        structured_store: Any,
        session_memory: Any,
        codex_bridge: CodexBridge | Any | None,
        repo_root: Path | str,
        proactive_callback: Callable[[Message], Any] | None = None,
        restart_handler: Callable[..., Any] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        finding_ttl_seconds: int = 600,
    ) -> None:
        self.structured_store = structured_store
        self.session_memory = session_memory
        self.codex_bridge = codex_bridge
        self.repo_root = Path(repo_root).resolve(strict=False)
        self.proactive_callback = proactive_callback
        self.restart_handler = restart_handler
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.finding_ttl_seconds = max(60, int(finding_ttl_seconds))
        self._finding_cache: dict[str, dict[str, Any]] = {}
        self._delta_buffers: dict[str, str] = {}

    def render_help(self) -> str:
        return "\n".join(
            [
                "# /repair",
                "",
                "支持的子命令：",
                "- `/repair help`",
                "- `/repair report [session] [--hours N]`",
                "- `/repair do <issue>`",
                "- `/repair do --from <finding_id> [--verify \"<cmd>\"]`",
                "- `/repair status`",
                "- `/repair logs [--run <id>] [-n N] [--follow]`",
                "- `/repair abort [--run <id>]`",
                "- `/repair retry [run-id]`",
                "",
                "默认策略：单 repo 单 active repair run；自动重启仅在验证通过且报告明确需要重启时触发。",
                f"已知样例：`{_GENESIS_QWEN_ISSUE_TEXT}`",
            ]
        )

    async def render_report(
        self,
        *,
        session_id: str,
        scope: str = "global",
        hours: int = 24,
    ) -> str:
        active = await self.structured_store.get_active_repair_run()
        findings = await self._build_findings(session_id=session_id, scope=scope, hours=hours)
        self._cache_findings(session_id=session_id, findings=findings)
        history = await self.structured_store.list_repair_runs(limit=5)

        lines = ["## 当前状态"]
        if active is None:
            lines.append("- 当前没有 active repair run")
        else:
            lines.append(
                "- "
                f"run_id={active.get('run_id')} "
                f"status={active.get('status')} "
                f"verification={active.get('verification_state')} "
                f"restart={active.get('restart_state')}"
            )

        lines.extend(["", "## 错误摘要"])
        if findings:
            for finding in findings:
                lines.append(
                    f"- [{finding['finding_id']}] {finding['title']} | "
                    f"{finding['issue_text']} | session={finding.get('session_id') or '-'}"
                )
        else:
            lines.append("- 最近没有明显问题。")

        lines.extend(["", "## repair 历史"])
        if history:
            for row in history[:5]:
                lines.append(
                    "- "
                    f"{row.get('run_id')} | {row.get('status')} | "
                    f"verification={row.get('verification_state')} | "
                    f"restart={row.get('restart_state')} | "
                    f"{row.get('issue_text')}"
                )
        else:
            lines.append("- 暂无 repair 历史。")

        lines.extend(
            [
                "",
                "## 已知模式",
                f"- `{_GENESIS_QWEN_ISSUE_TEXT}` 会在工具成功但 assistant 仍误报无法访问时命中。",
                "",
                "提示：finding ID 为临时编号，默认 10 分钟内有效。",
            ]
        )
        return "\n".join(lines)

    async def start_run(
        self,
        *,
        session_id: str,
        issue: str,
        finding_id: str | None = None,
        verify_commands: list[str] | None = None,
    ) -> dict[str, Any]:
        active = await self.structured_store.get_active_repair_run()
        if active is not None:
            return {"status": "blocked", "run_id": active.get("run_id")}
        if self.codex_bridge is None:
            return {"status": "error", "message": "Codex 不可用。"}

        finding = self._resolve_finding(session_id=session_id, finding_id=finding_id)
        issue_text = str(issue or "").strip() or str((finding or {}).get("issue_text") or "").strip()
        if not issue_text:
            return {"status": "error", "message": "issue is required"}

        verify_list = [str(item).strip() for item in (verify_commands or []) if str(item).strip()]
        diagnostic_snapshot = await self._build_diagnostic_snapshot(
            session_id=session_id,
            scope="global",
            hours=24,
        )
        run_id = self._new_run_id()
        git_status_before = self._git_status()
        working_directory = str(self.repo_root)
        await self.structured_store.create_repair_run(
            run_id=run_id,
            session_id=session_id,
            issue_text=issue_text,
            finding_id=str(finding_id or "").strip() or None,
            working_directory=working_directory,
            status="queued",
            verification_state="pending",
            restart_state="not_requested",
            diagnostic_snapshot_json=json.dumps(diagnostic_snapshot, ensure_ascii=False, sort_keys=True),
            verify_commands_json=json.dumps(verify_list, ensure_ascii=False),
            git_status_before=git_status_before,
        )
        prompt = self._build_repair_prompt(
            issue_text=issue_text,
            diagnostic_snapshot=diagnostic_snapshot,
            verify_commands=verify_list,
            finding=finding,
            previous_run=None,
        )
        thread = await self.codex_bridge.submit(
            run_id=run_id,
            prompt=prompt,
            working_dir=working_directory,
            on_complete=self._on_repair_complete,
            on_event=self._on_repair_event,
        )
        if str(thread.status or "").strip().lower() == "failed":
            row = await self.structured_store.get_repair_run(run_id)
            error = str((row or {}).get("last_error") or thread.result or "Codex submit failed").strip()
            return {
                "status": "failed",
                "run_id": run_id,
                "issue_text": issue_text,
                "working_directory": working_directory,
                "error": error,
            }
        await self.structured_store.update_repair_run(
            run_id,
            codex_thread_id=thread.thread_id,
            status=str(thread.status or "running").strip().lower() or "running",
        )
        await self.structured_store.append_repair_run_event(
            run_id=run_id,
            event_type="task.submitted",
            source="repair_service",
            summary=f"repair task submitted: {thread.thread_id or 'unknown'}",
            payload_json=json.dumps(
                {
                    "run_id": run_id,
                    "thread_id": thread.thread_id,
                    "status": thread.status,
                    "working_dir": working_directory,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return {
            "status": str(thread.status or "running").strip().lower() or "running",
            "run_id": run_id,
            "issue_text": issue_text,
            "working_directory": working_directory,
        }

    async def retry_run(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        active = await self.structured_store.get_active_repair_run()
        if active is not None:
            return {"status": "blocked", "run_id": active.get("run_id")}
        if self.codex_bridge is None:
            return {"status": "error", "message": "Codex 不可用。"}

        base_run = await self._resolve_run(session_id=session_id, run_id=run_id)
        if base_run is None:
            return {"status": "error", "message": "repair run not found"}

        verify_commands = self._load_json_list(base_run.get("verify_commands_json"))
        diagnostic_snapshot = self._load_json_dict(base_run.get("diagnostic_snapshot_json"))
        new_run_id = self._new_run_id()
        working_directory = str(base_run.get("working_directory") or self.repo_root)
        prompt = self._build_repair_prompt(
            issue_text=str(base_run.get("issue_text") or ""),
            diagnostic_snapshot=diagnostic_snapshot,
            verify_commands=verify_commands,
            previous_run=base_run,
        )
        await self.structured_store.create_repair_run(
            run_id=new_run_id,
            session_id=session_id,
            issue_text=str(base_run.get("issue_text") or ""),
            working_directory=working_directory,
            status="queued",
            verification_state="pending",
            restart_state="not_requested",
            diagnostic_snapshot_json=json.dumps(diagnostic_snapshot, ensure_ascii=False, sort_keys=True),
            verify_commands_json=json.dumps(verify_commands, ensure_ascii=False),
            retry_of_run_id=str(base_run.get("run_id") or "").strip() or None,
            git_status_before=self._git_status(),
        )

        base_thread_id = str(base_run.get("codex_thread_id") or "").strip()
        thread = None
        if base_thread_id:
            thread = await self.codex_bridge.continue_thread(
                run_id=new_run_id,
                thread_id=base_thread_id,
                prompt=prompt,
                working_dir=working_directory,
                on_complete=self._on_repair_complete,
                on_event=self._on_repair_event,
            )
        if thread is None or str(thread.status or "").strip().lower() == "failed":
            thread = await self.codex_bridge.submit(
                run_id=new_run_id,
                prompt=prompt,
                working_dir=working_directory,
                on_complete=self._on_repair_complete,
                on_event=self._on_repair_event,
            )
        if str(thread.status or "").strip().lower() == "failed":
            row = await self.structured_store.get_repair_run(new_run_id)
            error = str((row or {}).get("last_error") or thread.result or "Codex retry failed").strip()
            return {"status": "failed", "run_id": new_run_id, "error": error}
        await self.structured_store.update_repair_run(
            new_run_id,
            codex_thread_id=thread.thread_id,
            status=str(thread.status or "running").strip().lower() or "running",
        )
        await self.structured_store.append_repair_run_event(
            run_id=new_run_id,
            event_type="task.retry_submitted",
            source="repair_service",
            summary=f"retry submitted from {base_run.get('run_id')}",
            payload_json=json.dumps(
                {
                    "run_id": new_run_id,
                    "thread_id": thread.thread_id,
                    "retry_of_run_id": base_run.get("run_id"),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return {
            "status": str(thread.status or "running").strip().lower() or "running",
            "run_id": new_run_id,
            "retry_of_run_id": base_run.get("run_id"),
        }

    async def render_status(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
    ) -> str:
        row = await self._resolve_run(session_id=session_id, run_id=run_id)
        if row is None:
            return "当前没有 repair run。"
        return (
            f"repair {row.get('run_id')} | "
            f"status={row.get('status')} "
            f"verification={row.get('verification_state')} "
            f"restart={row.get('restart_state')}"
        )

    async def render_logs(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
        line_count: int = 30,
        follow: bool = False,
    ) -> str:
        row = await self._resolve_run(session_id=session_id, run_id=run_id)
        if row is None:
            return "当前没有 repair run。"
        events = await self.structured_store.list_repair_run_events(
            str(row.get("run_id")),
            limit=max(1, int(line_count)),
        )
        if not events:
            text = f"repair {row.get('run_id')} 暂无日志。"
        else:
            lines = [f"repair {row.get('run_id')} 日志："]
            for item in reversed(events):
                summary = str(item.get("summary") or "").strip()
                if not summary:
                    payload = self._load_json_dict(item.get("payload_json"))
                    summary = str(payload.get("result") or payload.get("summary") or "").strip()
                lines.append(
                    f"- {item.get('created_at')} | {item.get('source')} | "
                    f"{item.get('event_type')} | {summary}"
                )
            text = "\n".join(lines)
        if follow:
            text += "\n(follow 模式当前返回已缓存日志快照)"
        return text

    async def abort_run(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        row = await self._resolve_run(session_id=session_id, run_id=run_id)
        if row is None:
            return {"status": "error", "message": "repair run not found"}

        if self.codex_bridge is not None:
            await self.codex_bridge.abort(str(row.get("run_id")))
        await self.structured_store.update_repair_run(str(row.get("run_id")), status="aborted")
        await self.structured_store.append_repair_run_event(
            run_id=str(row.get("run_id")),
            event_type="repair.aborted",
            source="repair_service",
            summary="repair aborted by user",
        )
        return {"status": "aborted", "run_id": row.get("run_id")}

    async def recover_running_runs(self) -> None:
        if self.codex_bridge is None:
            return
        rows = await self.structured_store.list_repair_runs(limit=100)
        for row in rows:
            status = str(row.get("status") or "").strip().lower()
            if status not in {"queued", "running"}:
                continue
            run_id = str(row.get("run_id") or "").strip()
            thread_id = str(row.get("codex_thread_id") or "").strip()
            if not run_id:
                continue
            if not thread_id:
                await self.structured_store.update_repair_run(
                    run_id,
                    status="failed",
                    verification_state="unknown",
                    last_error="task.lost_on_restart",
                )
                await self.structured_store.append_repair_run_event(
                    run_id=run_id,
                    event_type="task.lost_on_restart",
                    source="repair_service",
                    summary="running repair had no codex_thread_id on restart",
                )
                continue
            inspected = await self.codex_bridge.inspect_thread(
                thread_id=thread_id,
                working_dir=str(row.get("working_directory") or self.repo_root),
            )
            recovered_status = str(inspected.get("status") or "").strip().lower()
            recovered_result = str(inspected.get("result") or "").strip() or None
            if recovered_status == "completed":
                await self._on_repair_complete(run_id, "completed", recovered_result)
                continue
            if recovered_status == "failed" and recovered_result != "task.lost_on_restart":
                await self._on_repair_complete(run_id, "failed", recovered_result)
                continue
            await self.structured_store.update_repair_run(
                run_id,
                status="failed",
                verification_state="unknown",
                last_error=recovered_result or "task.lost_on_restart",
            )
            await self.structured_store.append_repair_run_event(
                run_id=run_id,
                event_type="task.lost_on_restart",
                source="repair_service",
                summary=recovered_result or "task.lost_on_restart",
                payload_json=json.dumps(inspected, ensure_ascii=False, sort_keys=True),
            )

    async def _on_repair_complete(self, run_id: str, status: str, result: str | None) -> None:
        row = await self.structured_store.get_repair_run(run_id)
        if row is None:
            return
        self._delta_buffers.pop(run_id, None)
        normalized_status = str(status or "").strip().lower() or "failed"
        raw_result = str(result or "").strip()
        await self.structured_store.append_repair_run_event(
            run_id=run_id,
            event_type=f"task.{normalized_status}",
            source="codex_bridge",
            summary=(raw_result[:500] if raw_result else normalized_status),
            payload_json=json.dumps({"status": normalized_status, "result": raw_result}, ensure_ascii=False),
        )
        await self.structured_store.append_repair_run_event(
            run_id=run_id,
            event_type="task.raw_output",
            source="codex_bridge",
            summary=(raw_result[:500] if raw_result else ""),
            payload_json=json.dumps({"result": raw_result}, ensure_ascii=False),
        )

        if normalized_status == "aborted":
            await self.structured_store.update_repair_run(
                run_id,
                status="aborted",
                verification_state="unknown",
                report_markdown=raw_result,
                last_error="",
            )
            await self._push_terminal_report(
                session_id=str(row.get("session_id")),
                run_id=run_id,
                text=f"Repair Report\n\nrun_id={run_id}\nstatus=aborted",
            )
            return

        if normalized_status == "failed":
            await self.structured_store.update_repair_run(
                run_id,
                status="failed",
                verification_state="unknown",
                report_markdown=raw_result,
                last_error=raw_result,
            )
            await self._push_terminal_report(
                session_id=str(row.get("session_id")),
                run_id=run_id,
                text=f"Repair Report\n\nrun_id={run_id}\nstatus=failed\nerror={raw_result or 'unknown'}",
            )
            return

        parsed_report = self._extract_report_json(raw_result)
        if parsed_report is None:
            await self.structured_store.update_repair_run(
                run_id,
                status="needs_review",
                verification_state="unknown",
                report_markdown=raw_result,
                report_json="{}",
                git_status_after=self._git_status(),
            )
            await self._push_terminal_report(
                session_id=str(row.get("session_id")),
                run_id=run_id,
                text=f"Repair Report\n\nrun_id={run_id}\nstatus=needs_review\nsummary={raw_result}",
            )
            return

        verification_state = self._verification_state(parsed_report)
        run_status = str(parsed_report.get("status") or "completed").strip().lower() or "completed"
        if run_status not in {"completed", "needs_review", "failed"}:
            run_status = "completed"
        restart_state = "not_requested"
        needs_restart = bool(parsed_report.get("needs_restart"))
        if run_status == "completed" and verification_state == "passed":
            if not needs_restart:
                restart_state = "skipped"
            else:
                restart_state = await self._trigger_restart_if_allowed(run_id=run_id)
        await self.structured_store.update_repair_run(
            run_id,
            status=run_status,
            verification_state=verification_state,
            restart_state=restart_state,
            report_markdown=raw_result,
            report_json=json.dumps(parsed_report, ensure_ascii=False, sort_keys=True),
            git_status_after=self._git_status(),
            last_error="",
        )
        report_text = "\n".join(
            [
                "Repair Report",
                "",
                f"run_id={run_id}",
                f"status={run_status}",
                f"verification={verification_state}",
                f"restart={restart_state}",
                f"root_cause={parsed_report.get('root_cause') or '-'}",
            ]
        )
        await self._push_terminal_report(
            session_id=str(row.get("session_id")),
            run_id=run_id,
            text=report_text,
        )

    async def _on_repair_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        row = await self.structured_store.get_repair_run(run_id)
        if row is None:
            return
        summary = self._event_summary(event_type=event_type, payload=payload)
        await self.structured_store.append_repair_run_event(
            run_id=run_id,
            event_type=event_type,
            source="codex_bridge",
            summary=summary,
            payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )
        if event_type == "agent_message_delta" and summary:
            self._delta_buffers[run_id] = self._delta_buffers.get(run_id, "") + summary
            return
        if event_type == "item_completed":
            item_type = str(payload.get("type") or "").strip()
            if item_type == "agentMessage":
                text = str(payload.get("text") or "").strip() or self._delta_buffers.pop(run_id, "").strip()
                self._delta_buffers.pop(run_id, None)
                if text:
                    await self._push_terminal_report(
                        session_id=str(row.get("session_id")),
                        run_id=run_id,
                        text=f"[Repair | {run_id}]\n{text}",
                    )
                return
        if event_type == "thread_status" and str(payload.get("status") or "").strip().lower() in {"idle", "system_error"}:
            self._delta_buffers.pop(run_id, None)
            return

    async def _trigger_restart_if_allowed(self, *, run_id: str) -> str:
        if self.restart_handler is None:
            return "failed"
        if await self._restart_budget_exhausted():
            return "blocked_budget"
        result = self.restart_handler(reason=f"repair {run_id} completed", force=False)
        if hasattr(result, "__await__"):
            result = await result
        text = str(result or "")
        if "冷却" in text:
            return "blocked_cooldown"
        return "executed"

    async def _restart_budget_exhausted(self) -> bool:
        cutoff = self.now_fn() - timedelta(minutes=30)
        rows = await self.structured_store.list_repair_runs(limit=50)
        count = 0
        for row in rows:
            state = str(row.get("restart_state") or "").strip().lower()
            if state not in {"executed", "requested"}:
                continue
            updated_at = self._parse_datetime(row.get("updated_at"))
            if updated_at is None or updated_at < cutoff:
                continue
            count += 1
        return count >= 2

    async def _push_terminal_report(self, *, session_id: str, run_id: str, text: str) -> None:
        if self.proactive_callback is None:
            return
        message = Message(
            text=text,
            sender="system",
            session_id=session_id,
            channel="system",
            message_tag="tool_status",
            metadata={"source": "repair_service", "run_id": run_id},
        )
        result = self.proactive_callback(message)
        if hasattr(result, "__await__"):
            await result

    async def _resolve_run(self, *, session_id: str, run_id: str | None) -> dict[str, Any] | None:
        if run_id:
            return await self.structured_store.get_repair_run(run_id)
        row = await self.structured_store.get_latest_repair_run_for_session(session_id)
        if row is not None:
            return row
        return await self.structured_store.get_active_repair_run()

    async def _build_findings(
        self,
        *,
        session_id: str,
        scope: str,
        hours: int,
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        findings.extend(await self._detect_known_patterns(session_id=session_id, scope=scope, hours=hours))

        for entry in get_recent_logs(level="all", limit=10):
            if not self._within_hours(entry.get("timestamp"), hours=hours):
                continue
            findings.append(
                {
                    "title": str(entry.get("message") or "recent_log"),
                    "issue_text": str(entry.get("message") or "recent log issue"),
                    "session_id": "",
                    "kind": "recent_log",
                    "detail": str(entry.get("detail") or ""),
                }
            )

        since_iso = (self.now_fn() - timedelta(hours=hours)).isoformat()
        tool_rows = await self.structured_store.list_tool_invocations(since_iso=since_iso, limit=10)
        for row in tool_rows:
            status = str(row.get("status") or "").strip().lower()
            if status == "success":
                continue
            findings.append(
                {
                    "title": f"{row.get('skill_name') or 'tool'}.{row.get('tool_name') or 'unknown'}",
                    "issue_text": str(row.get("error_info") or row.get("result_summary") or "tool failure"),
                    "session_id": str(row.get("session_id") or ""),
                    "kind": "tool_failure",
                    "detail": str(row.get("params_json") or ""),
                }
            )

        deduped: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for item in findings:
            key = (
                str(item.get("kind") or ""),
                str(item.get("title") or ""),
                str(item.get("session_id") or ""),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(item)

        for idx, item in enumerate(deduped, start=1):
            item["finding_id"] = f"F{idx}"
        return deduped

    async def _build_diagnostic_snapshot(
        self,
        *,
        session_id: str,
        scope: str,
        hours: int,
    ) -> dict[str, Any]:
        findings = await self._build_findings(session_id=session_id, scope=scope, hours=hours)
        return {
            "scope": scope,
            "hours": hours,
            "findings": findings,
        }

    async def _detect_known_patterns(
        self,
        *,
        session_id: str,
        scope: str,
        hours: int,
    ) -> list[dict[str, Any]]:
        session_ids = [session_id]
        if scope == "global":
            session_ids = [
                str(item.get("session_id") or "").strip()
                for item in self.session_memory.list_sessions()
                if str(item.get("session_id") or "").strip()
            ]

        findings: list[dict[str, Any]] = []
        since_iso = (self.now_fn() - timedelta(hours=hours)).isoformat()
        tool_rows = await self.structured_store.list_tool_invocations(since_iso=since_iso, limit=200)
        successful_sessions = {
            str(row.get("session_id") or "").strip()
            for row in tool_rows
            if str(row.get("status") or "").strip().lower() == "success"
        }

        for candidate_session_id in session_ids:
            if candidate_session_id not in successful_sessions:
                continue
            messages = self.session_memory.get_messages(candidate_session_id)
            if not messages:
                continue
            for message in messages:
                if str(message.sender or "").strip().lower() != "assistant":
                    continue
                if not self._message_within_hours(message, hours=hours):
                    continue
                lowered = str(message.text or "").casefold()
                if not any(pattern in lowered for pattern in _ACCESS_DENIED_PATTERNS):
                    continue
                provider = str(message.metadata.get("provider") or "").casefold()
                model = str(message.metadata.get("model") or "").casefold()
                if "genesis" in provider or "qwen" in model or "qwen" in provider:
                    findings.append(
                        {
                            "title": "genesis_qwen_tool_access_false_negative",
                            "issue_text": _GENESIS_QWEN_ISSUE_TEXT,
                            "session_id": candidate_session_id,
                            "kind": "known_pattern",
                            "detail": str(message.text or ""),
                        }
                    )
                    break
        return findings

    def _cache_findings(self, *, session_id: str, findings: list[dict[str, Any]]) -> None:
        self._finding_cache[session_id] = {
            "expires_at": self.now_fn() + timedelta(seconds=self.finding_ttl_seconds),
            "findings": {str(item["finding_id"]): item for item in findings if item.get("finding_id")},
        }

    def _resolve_finding(self, *, session_id: str, finding_id: str | None) -> dict[str, Any] | None:
        normalized = str(finding_id or "").strip()
        if not normalized:
            return None
        cached = self._finding_cache.get(session_id)
        if not isinstance(cached, dict):
            return None
        expires_at = cached.get("expires_at")
        if not isinstance(expires_at, datetime) or expires_at < self.now_fn():
            self._finding_cache.pop(session_id, None)
            return None
        findings = cached.get("findings")
        if not isinstance(findings, dict):
            return None
        value = findings.get(normalized)
        return value if isinstance(value, dict) else None

    def _build_repair_prompt(
        self,
        *,
        issue_text: str,
        diagnostic_snapshot: dict[str, Any],
        verify_commands: list[str],
        finding: dict[str, Any] | None = None,
        previous_run: dict[str, Any] | None = None,
    ) -> str:
        lines = [
            "You are the self-repair agent for Hypo-Agent.",
            f"Working directory: {self.repo_root}",
            "",
            "Issue:",
            issue_text,
            "",
            "Diagnostic snapshot:",
            json.dumps(diagnostic_snapshot, ensure_ascii=False, sort_keys=True),
            "",
            "Workspace safety rules:",
            "- only modify files under /home/heyx/Hypo-Agent",
            "- run git status before making changes",
            "- do not revert or overwrite unrelated user changes",
            "- if the worktree is unsafe or ambiguous, stop and return needs_review",
        ]
        if finding is not None:
            lines.extend(
                [
                    "",
                    "Selected finding:",
                    json.dumps(finding, ensure_ascii=False, sort_keys=True),
                ]
            )
        if previous_run is not None:
            lines.extend(
                [
                    "",
                    "上次 repair 失败信息：",
                    str(previous_run.get("report_markdown") or previous_run.get("last_error") or "无"),
                ]
            )
        lines.extend(["", "Verification requirements:"])
        if verify_commands:
            for command in verify_commands:
                lines.append(f"- run `{command}` and include the result")
        else:
            lines.append("- run targeted verification for the modified area and include the result")
        lines.extend(
            [
                "",
                "Final output format:",
                "- first write a brief human summary",
                "- then output one fenced json block",
                "- json must include: status, root_cause, changed_files, verification, needs_restart, confidence, followups",
            ]
        )
        return "\n".join(lines)

    def _extract_report_json(self, text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        fenced = re.search(r"```json\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
        if fenced:
            try:
                parsed = json.loads(fenced.group(1))
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    def _verification_state(self, parsed_report: dict[str, Any]) -> str:
        verification = parsed_report.get("verification")
        if isinstance(verification, dict) and isinstance(verification.get("passed"), bool):
            return "passed" if bool(verification.get("passed")) else "failed"
        return "unknown"

    def _load_json_list(self, raw: Any) -> list[str]:
        text = str(raw or "").strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item).strip() for item in parsed if str(item).strip()]

    def _load_json_dict(self, raw: Any) -> dict[str, Any]:
        text = str(raw or "").strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _git_status(self) -> str:
        try:
            completed = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repo_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return str(completed.stdout or "").strip()

    def _new_run_id(self) -> str:
        return "repair-" + self.now_fn().strftime("%Y%m%d%H%M%S%f")

    def _parse_datetime(self, value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _within_hours(self, value: Any, *, hours: int) -> bool:
        parsed = self._parse_datetime(value)
        if parsed is None:
            return True
        return parsed >= self.now_fn() - timedelta(hours=hours)

    def _message_within_hours(self, message: Message, *, hours: int) -> bool:
        timestamp = message.timestamp
        if timestamp is None:
            return True
        normalized = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)
        return normalized >= self.now_fn() - timedelta(hours=hours)

    def _event_summary(self, *, event_type: str, payload: dict[str, Any]) -> str:
        if event_type == "agent_message_delta":
            return str(payload.get("delta") or "").strip()
        if event_type == "thread_status":
            status = str(payload.get("status") or "unknown").strip()
            flags = payload.get("active_flags")
            if isinstance(flags, list) and flags:
                return f"thread_status={status} flags={','.join(str(item) for item in flags)}"
            return f"thread_status={status}"
        if event_type == "turn_completed":
            return f"turn_completed status={str(payload.get('status') or 'completed').strip()}"
        if event_type == "item_completed":
            item_type = str(payload.get("type") or "item").strip()
            if item_type == "agentMessage":
                return str(payload.get("text") or "").strip()
            if item_type == "commandExecution":
                command = str(payload.get("command") or "").strip()
                output = str(payload.get("aggregatedOutput") or "").strip()
                return f"{command} {output}".strip()
            return item_type
        return str(payload or "")
