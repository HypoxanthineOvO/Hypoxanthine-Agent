from __future__ import annotations

import asyncio

from hypo_agent.channels.coder.coder_task_service import CoderTaskService
from hypo_agent.memory.structured_store import StructuredStore


class FakeCoderClient:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []
        self.task_calls: list[str] = []
        self.abort_calls: list[str] = []
        self.list_calls: list[str | None] = []
        self.health_calls = 0
        self.create_payload: dict = {
            "taskId": "task-123",
            "status": "running",
            "createdAt": "2026-04-05T10:00:00Z",
        }
        self.task_payload: dict = {
            "taskId": "task-123",
            "status": "running",
            "createdAt": "2026-04-05T10:00:00Z",
            "updatedAt": "2026-04-05T10:01:00Z",
        }
        self.abort_payload: dict = {"status": "aborted"}
        self.list_payload: list[dict] = []
        self.health_payload: dict = {"status": "ok"}

    async def create_task(
        self,
        prompt: str,
        working_directory: str,
        model: str | None = None,
        approval_policy: str = "full-auto",
        webhook: str | None = None,
    ) -> dict:
        self.create_calls.append(
            {
                "prompt": prompt,
                "working_directory": working_directory,
                "model": model,
                "approval_policy": approval_policy,
                "webhook": webhook,
            }
        )
        return dict(self.create_payload)

    async def get_task(self, task_id: str) -> dict:
        self.task_calls.append(task_id)
        return dict(self.task_payload)

    async def abort_task(self, task_id: str) -> dict:
        self.abort_calls.append(task_id)
        return dict(self.abort_payload)

    async def list_tasks(self, status: str | None = None) -> list[dict]:
        self.list_calls.append(status)
        return list(self.list_payload)

    async def health(self) -> dict:
        self.health_calls += 1
        return dict(self.health_payload)


def test_submit_task_persists_mapping_and_resolves_default_directory(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        await store.create_coder_task(
            task_id="task-old",
            session_id="s1",
            working_directory="/repo/existing",
            prompt_summary="old task",
            model="o4-mini",
            status="completed",
            attached=False,
        )
        client = FakeCoderClient()
        service = CoderTaskService(
            coder_client=client,
            structured_store=store,
            webhook_url="http://localhost:8765/api/coder/webhook",
            default_working_directory="/fallback/repo",
        )

        created = await service.submit_task(
            session_id="s1",
            prompt="fix login flow",
        )

        assert created["task_id"] == "task-123"
        assert created["working_directory"] == "/repo/existing"
        assert client.create_calls == [
            {
                "prompt": "fix login flow",
                "working_directory": "/repo/existing",
                "model": None,
                "approval_policy": "full-auto",
                "webhook": "http://localhost:8765/api/coder/webhook",
            }
        ]
        attached = await store.get_attached_coder_task_for_session("s1")
        assert attached is not None
        assert attached["task_id"] == "task-123"

    asyncio.run(_run())


def test_submit_task_explicit_directory_attach_detach_done_and_abort_last(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        client = FakeCoderClient()
        client.create_payload = {
            "taskId": "task-999",
            "status": "queued",
            "createdAt": "2026-04-05T10:00:00Z",
        }
        service = CoderTaskService(
            coder_client=client,
            structured_store=store,
            default_working_directory="/fallback/repo",
        )

        created = await service.submit_task(
            session_id="s1",
            prompt="add tests",
            working_directory="/repo/explicit",
            model="gpt-5-codex",
        )
        assert created["working_directory"] == "/repo/explicit"
        assert created["task_id"] == "task-999"

        latest = await service.get_task_status(task_id="last", session_id="s1")
        assert latest["task_id"] == "task-999"

        await service.detach_task("s1")
        assert await store.get_attached_coder_task_for_session("s1") is None

        await service.attach_task(session_id="s1", task_id="task-999")
        attached = await store.get_attached_coder_task_for_session("s1")
        assert attached is not None
        assert attached["task_id"] == "task-999"

        await service.mark_done("s1")
        assert await store.get_attached_coder_task_for_session("s1") is None
        done = await store.get_coder_task("task-999")
        assert done is not None
        assert done["done"] == 1

        await service.abort_task(task_id="task-999")
        assert client.abort_calls == ["task-999"]

    asyncio.run(_run())


def test_service_capabilities_and_send_degrade_cleanly(tmp_path) -> None:
    db_path = tmp_path / "hypo.db"

    async def _run() -> None:
        store = StructuredStore(db_path=db_path)
        await store.init()
        client = FakeCoderClient()
        service = CoderTaskService(
            coder_client=client,
            structured_store=store,
            default_working_directory="/fallback/repo",
        )

        assert service.supports_streaming() is False
        assert service.supports_continuation() is False

        error = await service.send_to_task(session_id="s1", instruction="continue")
        assert error == "Hypo-Coder API 暂不支持 session continuation。"

        created = await service.submit_task(session_id="s1", prompt="fix auth")
        assert created["working_directory"] == "/fallback/repo"

    asyncio.run(_run())
