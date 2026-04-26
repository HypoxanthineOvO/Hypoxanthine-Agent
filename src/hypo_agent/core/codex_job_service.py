from __future__ import annotations

import json
from typing import Any, Callable
from uuid import uuid4

from hypo_agent.core.time_utils import utc_isoformat, utc_now
from hypo_agent.models import Message

_ALLOWED_OPERATIONS = {
    "inspect_repo",
    "apply_patch_task",
    "run_verification",
    "diagnose_failure",
    "summarize_diff",
}


class CodexJobService:
    def __init__(
        self,
        *,
        structured_store: Any,
        codex_bridge: Any,
        session_memory: Any | None = None,
        proactive_callback: Callable[[Message], Any] | None = None,
        default_working_directory: str = ".",
    ) -> None:
        self.structured_store = structured_store
        self.codex_bridge = codex_bridge
        self.session_memory = session_memory
        self.proactive_callback = proactive_callback
        self.default_working_directory = str(default_working_directory or ".").strip() or "."

    async def submit_job(
        self,
        *,
        session_id: str,
        operation: str,
        prompt: str,
        working_directory: str | None = None,
    ) -> dict[str, Any]:
        normalized_operation = str(operation or "").strip()
        if normalized_operation not in _ALLOWED_OPERATIONS:
            raise ValueError(f"Unsupported Codex job operation: {operation}")
        job_id = f"codex-job-{uuid4().hex[:12]}"
        trace_id = f"trace-{uuid4().hex}"
        resolved_working_dir = str(working_directory or "").strip() or self.default_working_directory
        isolation_mode = str(getattr(self.codex_bridge, "isolation_mode", "") or "unknown")

        await self.structured_store.create_codex_job(
            job_id=job_id,
            session_id=session_id,
            operation=normalized_operation,
            prompt_summary=str(prompt or "").strip()[:500],
            working_directory=resolved_working_dir,
            trace_id=trace_id,
            status="running",
            isolation_mode=isolation_mode,
        )

        async def on_event(run_id: str, event_type: str, payload: dict[str, Any]) -> None:
            await self._on_event(
                job_id=run_id,
                session_id=session_id,
                trace_id=trace_id,
                event_type=event_type,
                payload=payload,
            )

        async def on_complete(run_id: str, status: str, result: str | None) -> None:
            await self._on_complete(job_id=run_id, status=status, result=result)

        thread = await self.codex_bridge.submit(
            run_id=job_id,
            prompt=prompt,
            working_dir=resolved_working_dir,
            on_complete=on_complete,
            on_event=on_event,
        )
        thread_id = str(getattr(thread, "thread_id", "") or "")
        status = str(getattr(thread, "status", "") or "running")
        await self.structured_store.update_codex_job(
            job_id=job_id,
            status=status,
            thread_id=thread_id,
            last_error=str(getattr(thread, "result", "") or "") if status == "failed" else "",
        )
        return {
            "job_id": job_id,
            "session_id": session_id,
            "operation": normalized_operation,
            "working_directory": resolved_working_dir,
            "trace_id": trace_id,
            "thread_id": thread_id,
            "status": status,
            "isolation_mode": isolation_mode,
        }

    async def abort_job(self, job_id: str) -> None:
        await self.codex_bridge.abort(job_id)
        await self.structured_store.update_codex_job(
            job_id=job_id,
            status="aborted",
            last_error="aborted by user",
            completed_at=utc_isoformat(utc_now()),
        )
        await self.structured_store.append_codex_job_event(
            job_id=job_id,
            event_type="aborted",
            summary="aborted by user",
            payload_json=json.dumps({"status": "aborted"}, ensure_ascii=False),
        )

    async def _on_event(
        self,
        *,
        job_id: str,
        session_id: str,
        trace_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        summary = self._event_summary(event_type, payload)
        await self.structured_store.append_codex_job_event(
            job_id=job_id,
            event_type=event_type,
            summary=summary,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
        if summary:
            await self._push_transient_progress(
                session_id=session_id,
                job_id=job_id,
                trace_id=trace_id,
                text=summary,
            )

    async def _on_complete(self, *, job_id: str, status: str, result: str | None) -> None:
        terminal_status = str(status or "failed").strip() or "failed"
        result_summary = str(result or "").strip()
        await self.structured_store.update_codex_job(
            job_id=job_id,
            status=terminal_status,
            result_summary=result_summary,
            last_error=result_summary if terminal_status == "failed" else "",
            completed_at=utc_isoformat(utc_now()),
        )
        await self.structured_store.append_codex_job_event(
            job_id=job_id,
            event_type="completed",
            summary=result_summary or terminal_status,
            payload_json=json.dumps({"status": terminal_status, "result": result}, ensure_ascii=False),
        )

    async def _push_transient_progress(
        self,
        *,
        session_id: str,
        job_id: str,
        trace_id: str,
        text: str,
    ) -> None:
        if self.proactive_callback is None:
            return
        message = Message(
            text=f"[Codex | {job_id}]\n{text}",
            sender="assistant",
            session_id=session_id,
            message_tag="tool_status",
            metadata={
                "codex_job_id": job_id,
                "trace_id": trace_id,
                "transient": True,
                "persist_to_l1": False,
            },
        )
        result = self.proactive_callback(message)
        if hasattr(result, "__await__"):
            await result

    def _event_summary(self, event_type: str, payload: dict[str, Any]) -> str:
        if event_type == "agent_message_delta":
            return str(payload.get("delta") or "").strip()
        if event_type == "thread_status":
            return f"status: {payload.get('status') or 'unknown'}"
        if event_type == "item_completed":
            text = payload.get("text")
            return str(text or "").strip()
        if event_type == "turn_completed":
            return f"turn completed: {payload.get('status') or 'completed'}"
        return ""
