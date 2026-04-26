from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import shutil
from typing import Any, Callable

from codex_app_server import AppServerConfig, AsyncCodex, TextInput
from codex_app_server.generated.v2_all import ApprovalsReviewer, AskForApprovalValue, SandboxMode


@dataclass(slots=True)
class CodexThread:
    thread_id: str
    run_id: str
    working_dir: str
    status: str = "idle"
    result: str | None = None
    turn_id: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    thread: Any | None = field(default=None, repr=False)
    turn_handle: Any | None = field(default=None, repr=False)


class CodexBridge:
    """Programmatic Codex execution layer inside Hypo-Agent."""

    def __init__(
        self,
        *,
        model: str = "gpt-5.4",
        reasoning_effort: str = "high",
        codex_bin: str | None = None,
        approval_policy: str = "never",
        approvals_reviewer: str | None = "guardian_subagent",
        sandbox_mode: str = "danger-full-access",
        codex_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.model = str(model or "").strip() or "gpt-5.4"
        self.reasoning_effort = str(reasoning_effort or "").strip() or "high"
        self.codex_bin = str(codex_bin or "").strip() or shutil.which("codex") or ""
        self.approval_policy = self._normalize_approval_policy(approval_policy)
        self.approvals_reviewer = self._normalize_approvals_reviewer(approvals_reviewer)
        self.sandbox_mode = self._normalize_sandbox_mode(sandbox_mode)
        self._codex_factory = codex_factory
        self._codex: Any | None = None
        self._threads: dict[str, CodexThread] = {}
        self._start_error: str = ""

    async def start(self) -> bool:
        if self._codex is not None:
            return True
        try:
            codex = self._codex_factory() if callable(self._codex_factory) else AsyncCodex(
                config=AppServerConfig(codex_bin=self.codex_bin or None)
            )
            entered = codex.__aenter__()
            self._codex = await entered if hasattr(entered, "__await__") else codex
            self._start_error = ""
            return True
        except Exception as exc:
            self._codex = None
            self._start_error = str(exc)
            return False

    async def stop(self) -> None:
        for thread in list(self._threads.values()):
            if thread.task is not None and not thread.task.done():
                thread.task.cancel()
                try:
                    await thread.task
                except asyncio.CancelledError:
                    pass
        self._threads.clear()
        if self._codex is None:
            return
        closer = getattr(self._codex, "__aexit__", None)
        try:
            if callable(closer):
                result = closer(None, None, None)
                if hasattr(result, "__await__"):
                    await result
            else:
                close_result = self._codex.close()
                if hasattr(close_result, "__await__"):
                    await close_result
        finally:
            self._codex = None

    async def submit(
        self,
        run_id: str,
        prompt: str,
        working_dir: str,
        on_complete: Callable[[str, str, str | None], Any],
        on_event: Callable[[str, str, dict[str, Any]], Any] | None = None,
    ) -> CodexThread:
        started = await self.start()
        if not started or self._codex is None:
            thread = CodexThread(
                thread_id="",
                run_id=run_id,
                working_dir=working_dir,
                status="failed",
                result=self._start_error or "CodexBridge failed to start",
            )
            self._threads[run_id] = thread
            await self._invoke_callback(on_complete, run_id, "failed", thread.result)
            return thread

        try:
            sdk_thread = await self._codex.thread_start(
                model=self.model,
                cwd=working_dir,
                approval_policy=self._approval_policy_value(),
                approvals_reviewer=self._approvals_reviewer_value(),
                sandbox=self._sandbox_mode_value(),
                config={"model_reasoning_effort": self.reasoning_effort},
            )
        except Exception as exc:
            thread = CodexThread(
                thread_id="",
                run_id=run_id,
                working_dir=working_dir,
                status="failed",
                result=str(exc),
            )
            self._threads[run_id] = thread
            await self._invoke_callback(on_complete, run_id, "failed", thread.result)
            return thread

        thread = CodexThread(
            thread_id=str(getattr(sdk_thread, "id", "") or ""),
            run_id=run_id,
            working_dir=working_dir,
            status="running",
            thread=sdk_thread,
        )
        self._threads[run_id] = thread
        thread.task = asyncio.create_task(self._execute(thread, prompt, on_complete, on_event))
        return thread

    async def continue_thread(
        self,
        run_id: str,
        thread_id: str,
        prompt: str,
        working_dir: str,
        on_complete: Callable[[str, str, str | None], Any],
        on_event: Callable[[str, str, dict[str, Any]], Any] | None = None,
    ) -> CodexThread:
        started = await self.start()
        if not started or self._codex is None:
            thread = CodexThread(
                thread_id=thread_id,
                run_id=run_id,
                working_dir=working_dir,
                status="failed",
                result=self._start_error or "CodexBridge failed to start",
            )
            self._threads[run_id] = thread
            await self._invoke_callback(on_complete, run_id, "failed", thread.result)
            return thread
        try:
            sdk_thread = await self._codex.thread_resume(
                thread_id,
                model=self.model,
                cwd=working_dir,
                approval_policy=self._approval_policy_value(),
                approvals_reviewer=self._approvals_reviewer_value(),
                sandbox=self._sandbox_mode_value(),
                config={"model_reasoning_effort": self.reasoning_effort},
            )
        except Exception as exc:
            thread = CodexThread(
                thread_id=thread_id,
                run_id=run_id,
                working_dir=working_dir,
                status="failed",
                result=str(exc),
            )
            self._threads[run_id] = thread
            return thread

        thread = CodexThread(
            thread_id=str(getattr(sdk_thread, "id", "") or thread_id),
            run_id=run_id,
            working_dir=working_dir,
            status="running",
            thread=sdk_thread,
        )
        self._threads[run_id] = thread
        thread.task = asyncio.create_task(self._execute(thread, prompt, on_complete, on_event))
        return thread

    async def abort(self, run_id: str) -> None:
        thread = self._threads.get(run_id)
        if thread is None:
            return
        thread.status = "aborted"
        interrupter = getattr(thread.turn_handle, "interrupt", None)
        if callable(interrupter):
            result = interrupter()
            if hasattr(result, "__await__"):
                await result
        if thread.turn_handle is None and thread.task is not None and not thread.task.done():
            thread.task.cancel()
        if thread.task is not None and not thread.task.done():
            try:
                await thread.task
            except asyncio.CancelledError:
                pass

    def get_status(self, run_id: str) -> CodexThread | None:
        return self._threads.get(run_id)

    async def inspect_thread(
        self,
        *,
        thread_id: str,
        working_dir: str,
    ) -> dict[str, str | None]:
        started = await self.start()
        if not started or self._codex is None:
            return {"status": "failed", "result": self._start_error or "CodexBridge failed to start"}
        try:
            sdk_thread = await self._codex.thread_resume(
                thread_id,
                model=self.model,
                cwd=working_dir,
                approval_policy=self._approval_policy_value(),
                approvals_reviewer=self._approvals_reviewer_value(),
                sandbox=self._sandbox_mode_value(),
                config={"model_reasoning_effort": self.reasoning_effort},
            )
            payload = await sdk_thread.read(include_turns=True)
        except Exception as exc:
            return {"status": "failed", "result": str(exc)}

        turns = list(getattr(payload.thread, "turns", []) or [])
        if not turns:
            return {"status": "failed", "result": "thread has no turns to recover"}
        latest = turns[-1]
        status = self._status_value(getattr(latest, "status", None))
        if status == "completed":
            return {"status": "completed", "result": self._assistant_text_from_turn(latest)}
        if status == "failed":
            error = getattr(getattr(latest, "error", None), "message", "") or "turn failed"
            return {"status": "failed", "result": str(error)}
        return {"status": "failed", "result": "task.lost_on_restart"}

    async def _execute(
        self,
        thread: CodexThread,
        prompt: str,
        on_complete: Callable[[str, str, str | None], Any],
        on_event: Callable[[str, str, dict[str, Any]], Any] | None,
    ) -> None:
        try:
            turn_handle = await thread.thread.turn(
                TextInput(prompt),
                cwd=thread.working_dir,
                approval_policy=self._approval_policy_value(),
                approvals_reviewer=self._approvals_reviewer_value(),
                sandbox_policy=self._turn_sandbox_policy(thread.working_dir),
            )
            thread.turn_handle = turn_handle
            thread.turn_id = str(getattr(turn_handle, "id", "") or "")
            stream = turn_handle.stream()
            try:
                async for event in stream:
                    method = str(getattr(event, "method", "") or "")
                    payload = getattr(event, "payload", None)
                    if method == "item/agentMessage/delta":
                        delta = str(getattr(payload, "delta", "") or "")
                        if delta:
                            await self._invoke_event_callback(
                                on_event,
                                thread.run_id,
                                "agent_message_delta",
                                {
                                    "delta": delta,
                                    "turn_id": str(getattr(payload, "turn_id", "") or ""),
                                    "thread_id": str(getattr(payload, "thread_id", "") or thread.thread_id),
                                },
                            )
                        continue
                    if method == "item/completed":
                        item = getattr(payload, "item", None)
                        dumped = item.model_dump(mode="json") if hasattr(item, "model_dump") else {}
                        if isinstance(dumped, dict):
                            await self._invoke_event_callback(
                                on_event,
                                thread.run_id,
                                "item_completed",
                                dumped,
                            )
                        continue
                    if method == "thread/status/changed":
                        status = getattr(getattr(payload, "status", None), "root", None)
                        status_type = str(getattr(status, "type", "") or "").strip().lower()
                        active_flags = self._active_flags(status)
                        event_payload: dict[str, Any] = {"status": status_type or "unknown"}
                        if active_flags:
                            event_payload["active_flags"] = active_flags
                            event_payload["waiting_on_approval"] = "waitingOnApproval" in active_flags
                            event_payload["waiting_on_user_input"] = "waitingOnUserInput" in active_flags
                        await self._invoke_event_callback(
                            on_event,
                            thread.run_id,
                            "thread_status",
                            event_payload,
                        )
                        if status_type in {"idle", "system_error"}:
                            break
                        continue
                    if method == "turn/completed":
                        await self._invoke_event_callback(
                            on_event,
                            thread.run_id,
                            "turn_completed",
                            {"turn_id": thread.turn_id or "", "status": "completed"},
                        )
                        break
            finally:
                await stream.aclose()
            payload = await thread.thread.read(include_turns=True)
            turns = list(getattr(payload.thread, "turns", []) or [])
            latest = turns[-1] if turns else None
            status = self._status_value(getattr(latest, "status", None))
            if status == "failed":
                message = getattr(getattr(latest, "error", None), "message", "") or "turn failed"
                thread.status = "failed"
                thread.result = str(message)
                await self._invoke_callback(on_complete, thread.run_id, "failed", thread.result)
                return
            if status == "aborted":
                thread.status = "aborted"
                thread.result = "aborted"
                await self._invoke_callback(on_complete, thread.run_id, "aborted", thread.result)
                return
            if status == "interrupted":
                thread.status = "failed"
                thread.result = "interrupted"
                await self._invoke_callback(on_complete, thread.run_id, "failed", thread.result)
                return
            result_text = self._assistant_text_from_turn(latest)
            thread.status = "completed"
            thread.result = result_text
            await self._invoke_callback(on_complete, thread.run_id, "completed", result_text)
        except asyncio.CancelledError:
            thread.status = "aborted"
            thread.result = "aborted"
            await self._invoke_callback(on_complete, thread.run_id, "aborted", thread.result)
            return
        except Exception as exc:
            thread.status = "failed"
            thread.result = str(exc)
            await self._invoke_callback(on_complete, thread.run_id, "failed", thread.result)

    async def _invoke_callback(
        self,
        callback: Callable[[str, str, str | None], Any],
        run_id: str,
        status: str,
        result: str | None,
    ) -> None:
        resolved = callback(run_id, status, result)
        if hasattr(resolved, "__await__"):
            await resolved

    async def _invoke_event_callback(
        self,
        callback: Callable[[str, str, dict[str, Any]], Any] | None,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if callback is None:
            return
        resolved = callback(run_id, event_type, payload)
        if hasattr(resolved, "__await__"):
            await resolved

    def _assistant_text_from_turn(self, turn: Any) -> str | None:
        if turn is None:
            return None
        chunks: list[str] = []
        for item in list(getattr(turn, "items", []) or []):
            payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            if not isinstance(payload, dict):
                continue
            item_type = payload.get("type")
            if item_type == "agentMessage":
                text = payload.get("text")
                if isinstance(text, str) and text:
                    chunks.append(text)
                    continue
            if item_type == "message" and payload.get("role") == "assistant":
                for content in payload.get("content") or []:
                    if not isinstance(content, dict) or content.get("type") != "output_text":
                        continue
                    text = content.get("text")
                    if isinstance(text, str) and text:
                        chunks.append(text)
        if not chunks:
            return None
        return "".join(chunks)

    def _status_value(self, value: Any) -> str:
        return str(getattr(value, "value", value) or "").strip().lower()

    def _approval_policy_value(self) -> AskForApprovalValue:
        return {
            "untrusted": AskForApprovalValue.untrusted,
            "on-failure": AskForApprovalValue.on_failure,
            "on-request": AskForApprovalValue.on_request,
            "never": AskForApprovalValue.never,
        }.get(self.approval_policy, AskForApprovalValue.never)

    def _approvals_reviewer_value(self) -> ApprovalsReviewer | None:
        if not self.approvals_reviewer:
            return None
        return {
            "user": ApprovalsReviewer.user,
            "guardian_subagent": ApprovalsReviewer.guardian_subagent,
        }.get(self.approvals_reviewer)

    def _sandbox_mode_value(self) -> SandboxMode:
        return {
            "read-only": SandboxMode.read_only,
            "workspace-write": SandboxMode.workspace_write,
            "danger-full-access": SandboxMode.danger_full_access,
        }.get(self.sandbox_mode, SandboxMode.danger_full_access)

    def _turn_sandbox_policy(self, working_dir: str) -> dict[str, Any]:
        if self.sandbox_mode == "read-only":
            return {
                "type": "readOnly",
                "access": {"type": "fullAccess"},
                "networkAccess": False,
            }
        if self.sandbox_mode == "workspace-write":
            return {
                "type": "workspaceWrite",
                "writableRoots": [str(working_dir or "").strip() or "."],
                "readOnlyAccess": {"type": "fullAccess"},
                "networkAccess": False,
                "excludeTmpdirEnvVar": False,
                "excludeSlashTmp": False,
            }
        return {"type": "dangerFullAccess"}

    def _normalize_approval_policy(self, value: str | None) -> str:
        normalized = str(value or "").strip().lower().replace("_", "-")
        aliases = {
            "": "never",
            "auto": "never",
            "auto-approve": "never",
            "full-auto": "never",
            "fullauto": "never",
            "manual": "on-request",
            "onrequest": "on-request",
            "onfailure": "on-failure",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"untrusted", "on-failure", "on-request", "never"}:
            return normalized
        return "never"

    def _normalize_approvals_reviewer(self, value: str | None) -> str:
        normalized = str(value or "").strip().lower().replace("-", "_")
        aliases = {
            "": "",
            "none": "",
            "manual": "user",
            "auto": "guardian_subagent",
            "auto_review": "guardian_subagent",
            "autoreview": "guardian_subagent",
            "guardian": "guardian_subagent",
            "guardian_sub_agent": "guardian_subagent",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"user", "guardian_subagent"}:
            return normalized
        return ""

    def _normalize_sandbox_mode(self, value: str | None) -> str:
        normalized = str(value or "").strip().lower().replace("_", "-")
        aliases = {
            "": "danger-full-access",
            "readonly": "read-only",
            "workspace": "workspace-write",
            "workspacewrite": "workspace-write",
            "full": "danger-full-access",
            "danger": "danger-full-access",
            "dangerfullaccess": "danger-full-access",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"read-only", "workspace-write", "danger-full-access"}:
            return normalized
        return "danger-full-access"

    def _active_flags(self, status: Any) -> list[str]:
        raw_flags = getattr(status, "active_flags", None) or []
        flags: list[str] = []
        for item in raw_flags:
            value = getattr(item, "value", None) or str(item or "")
            text = str(value).strip()
            if text:
                flags.append(text)
        return flags
