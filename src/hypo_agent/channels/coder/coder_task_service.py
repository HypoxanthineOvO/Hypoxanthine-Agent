from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable


class CoderTaskService:
    def __init__(
        self,
        *,
        coder_client: Any,
        structured_store: Any | None,
        webhook_url: str | None = None,
        default_working_directory: str = "/home/heyx/Hypo-Agent",
        now_fn: Callable[[], datetime] | None = None,
        watcher: Any | None = None,
    ) -> None:
        self.coder_client = coder_client
        self.structured_store = structured_store
        self.webhook_url = str(webhook_url or "").strip() or None
        self.default_working_directory = (
            str(default_working_directory or "").strip() or "/home/heyx/Hypo-Agent"
        )
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.watcher = watcher

    def supports_streaming(self) -> bool:
        checker = getattr(self.coder_client, "supports_streaming", None)
        return bool(checker()) if callable(checker) else False

    def supports_continuation(self) -> bool:
        checker = getattr(self.coder_client, "supports_continuation", None)
        return bool(checker()) if callable(checker) else False

    def supports_incremental_output(self) -> bool:
        checker = getattr(self.coder_client, "supports_incremental_output", None)
        return bool(checker()) if callable(checker) else False

    async def submit_task(
        self,
        *,
        session_id: str,
        prompt: str,
        working_directory: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        resolved_directory = await self.resolve_working_directory(
            session_id=session_id,
            explicit_directory=working_directory,
        )
        normalized_model = str(model or "").strip() or None
        payload = await self.coder_client.create_task(
            prompt=prompt,
            working_directory=resolved_directory,
            model=normalized_model,
            approval_policy="full-auto",
            webhook=self.webhook_url,
        )
        task_id = str(payload.get("taskId") or "").strip()
        status = str(payload.get("status") or "queued").strip() or "queued"
        if self.structured_store is not None:
            await self.structured_store.create_coder_task(
                task_id=task_id,
                session_id=session_id,
                working_directory=resolved_directory,
                prompt_summary=prompt.strip()[:200],
                model=normalized_model,
                status=status,
                attached=True,
            )
        await self._start_watcher_if_needed(task_id=task_id, session_id=session_id, status=status)
        task = await self.structured_store.get_coder_task(task_id) if self.structured_store is not None else None
        return task or {
            "task_id": task_id,
            "session_id": session_id,
            "working_directory": resolved_directory,
            "prompt_summary": prompt.strip()[:200],
            "model": normalized_model,
            "status": status,
            "attached": 1,
            "done": 0,
            "last_error": "",
        }

    async def resolve_working_directory(
        self,
        *,
        session_id: str,
        explicit_directory: str | None = None,
    ) -> str:
        provided = str(explicit_directory or "").strip()
        if provided:
            return provided
        if self.structured_store is None:
            return self.default_working_directory
        latest = await self.structured_store.get_latest_coder_task_for_session(session_id)
        if latest is not None:
            existing = str(latest.get("working_directory") or "").strip()
            if existing:
                return existing
        return self.default_working_directory

    async def get_task_status(
        self,
        *,
        task_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_task_id = await self.resolve_task_id(task_id=task_id, session_id=session_id)
        payload = await self.coder_client.get_task(resolved_task_id)
        status = str(payload.get("status") or "unknown").strip() or "unknown"
        error = str(payload.get("error") or payload.get("message") or "").strip()
        if self.structured_store is not None:
            await self.structured_store.update_coder_task_status(
                task_id=resolved_task_id,
                status=status,
                last_error=error,
            )
        row = (
            await self.structured_store.get_coder_task(resolved_task_id)
            if self.structured_store is not None
            else None
        )
        if row is None:
            return {"task_id": resolved_task_id, "status": status, "last_error": error, **payload}
        merged = dict(row)
        merged.update(payload)
        return merged

    async def list_tasks(self, *, status: str | None = None) -> list[dict[str, Any]]:
        return list(await self.coder_client.list_tasks(status=status))

    async def abort_task(
        self,
        *,
        task_id: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_task_id = await self.resolve_task_id(task_id=task_id, session_id=session_id)
        payload = await self.coder_client.abort_task(resolved_task_id)
        status = str(payload.get("status") or "aborted").strip() or "aborted"
        if self.structured_store is not None:
            await self.structured_store.update_coder_task_status(
                task_id=resolved_task_id,
                status=status,
                last_error="",
            )
        row = (
            await self.structured_store.get_coder_task(resolved_task_id)
            if self.structured_store is not None
            else None
        )
        if row is None:
            return {"task_id": resolved_task_id, "status": status}
        merged = dict(row)
        merged.update(payload)
        return merged

    async def attach_task(
        self,
        *,
        session_id: str,
        task_id: str,
        initial_cursor: str | None = None,
    ) -> None:
        if self.structured_store is None:
            return
        await self.structured_store.attach_coder_task(session_id=session_id, task_id=task_id)
        task = await self.structured_store.get_coder_task(task_id)
        status = str(task.get("status") or "").strip() if task is not None else ""
        await self._start_watcher_if_needed(
            task_id=task_id,
            session_id=session_id,
            status=status,
            initial_cursor=initial_cursor,
        )

    async def detach_task(self, session_id: str) -> None:
        if self.structured_store is None:
            return
        await self.structured_store.detach_coder_task(session_id=session_id)

    async def mark_done(self, session_id: str) -> None:
        if self.structured_store is None:
            return
        await self.structured_store.mark_coder_task_done(session_id=session_id)

    async def get_attached_task(self, session_id: str) -> dict[str, Any] | None:
        if self.structured_store is None:
            return None
        return await self.structured_store.get_attached_coder_task_for_session(session_id)

    async def health(self) -> dict[str, Any]:
        return dict(await self.coder_client.health())

    async def get_task_output(
        self,
        *,
        task_id: str,
        after: str | None = None,
    ) -> dict[str, Any]:
        getter = getattr(self.coder_client, "get_task_output", None)
        if not callable(getter):
            return {"cursor": str(after or "").strip(), "lines": [], "done": False}
        payload = getter(task_id, after=after)
        if hasattr(payload, "__await__"):
            payload = await payload
        return dict(payload) if isinstance(payload, dict) else {"cursor": str(after or "").strip(), "lines": [], "done": False}

    async def send_to_task(
        self,
        *,
        session_id: str,
        instruction: str,
        task_id: str = "last",
    ) -> str:
        del session_id, instruction, task_id
        if not self.supports_continuation():
            return "Hypo-Coder API 暂不支持 session continuation。"
        raise NotImplementedError("Continuation support is not wired yet")

    async def resolve_task_id(
        self,
        *,
        task_id: str,
        session_id: str | None = None,
    ) -> str:
        normalized = str(task_id or "").strip()
        if normalized and normalized != "last":
            return normalized
        if not session_id:
            raise ValueError("session_id is required when task_id is 'last'")
        if self.structured_store is None:
            raise ValueError("structured_store is required when task_id is 'last'")
        latest = await self.structured_store.get_latest_coder_task_for_session(session_id)
        if latest is None:
            raise ValueError(f"Session {session_id} has no coder task")
        resolved_task_id = str(latest.get("task_id") or "").strip()
        if not resolved_task_id:
            raise ValueError(f"Session {session_id} has no coder task")
        return resolved_task_id

    async def _start_watcher_if_needed(
        self,
        *,
        task_id: str,
        session_id: str,
        status: str,
        initial_cursor: str | None = None,
    ) -> None:
        if self.watcher is None:
            return
        if str(status or "").strip().lower() in {"completed", "failed", "aborted"}:
            return
        starter = getattr(self.watcher, "start", None)
        if not callable(starter):
            return
        result = starter(task_id=task_id, session_id=session_id, initial_cursor=initial_cursor)
        if hasattr(result, "__await__"):
            await result
