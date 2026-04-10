from __future__ import annotations

import asyncio

from hypo_agent.channels.coder.coder_client import CoderUnavailableError
from hypo_agent.skills.coder_skill import CoderSkill


class FakeCoderClient:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []
        self.task_calls: list[str] = []
        self.list_calls: list[str | None] = []
        self.abort_calls: list[str] = []
        self.health_calls = 0
        self.create_payload: dict | Exception = {
            "taskId": "task-123",
            "sessionId": "session-1",
            "status": "running",
            "createdAt": "2026-03-27T10:00:00Z",
        }
        self.task_payload: dict | Exception = {}
        self.list_payload: list[dict] | Exception = []
        self.abort_payload: dict | Exception = {}
        self.health_payload: dict | Exception = {"status": "ok"}

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
        if isinstance(self.create_payload, Exception):
            raise self.create_payload
        return dict(self.create_payload)

    async def get_task(self, task_id: str) -> dict:
        self.task_calls.append(task_id)
        if isinstance(self.task_payload, Exception):
            raise self.task_payload
        return dict(self.task_payload)

    async def abort_task(self, task_id: str) -> dict:
        self.abort_calls.append(task_id)
        if isinstance(self.abort_payload, Exception):
            raise self.abort_payload
        return dict(self.abort_payload)

    async def list_tasks(self, status: str | None = None) -> list[dict]:
        self.list_calls.append(status)
        if isinstance(self.list_payload, Exception):
            raise self.list_payload
        return list(self.list_payload)

    async def health(self) -> dict:
        self.health_calls += 1
        if isinstance(self.health_payload, Exception):
            raise self.health_payload
        return dict(self.health_payload)


def test_submit_task() -> None:
    async def _run() -> None:
        client = FakeCoderClient()
        skill = CoderSkill(coder_client=client, webhook_url="http://localhost:8765/api/coder/webhook")

        result = await skill.execute(
            "coder_submit_task",
            {
                "prompt": "修复 hello.py",
                "working_directory": "/tmp/demo",
            },
        )

        assert result.status == "success"
        assert result.result == "任务已提交，task_id=task-123，正在执行中。完成后会通知你。"
        assert client.create_calls == [
            {
                "prompt": "修复 hello.py",
                "working_directory": "/tmp/demo",
                "model": None,
                "approval_policy": "full-auto",
                "webhook": "http://localhost:8765/api/coder/webhook",
            }
        ]

    asyncio.run(_run())


def test_task_status_running() -> None:
    async def _run() -> None:
        client = FakeCoderClient()
        client.task_payload = {
            "taskId": "task-123",
            "status": "running",
            "createdAt": "2026-03-27T10:00:00Z",
            "startedAt": "2026-03-27T10:00:00Z",
            "updatedAt": "2026-03-27T10:12:00Z",
        }
        skill = CoderSkill(coder_client=client)

        result = await skill.execute("coder_task_status", {"task_id": "task-123"})

        assert result.status == "success"
        assert result.result == "任务 task-123 正在执行中（已运行 12 分钟）"
        assert client.task_calls == ["task-123"]

    asyncio.run(_run())


def test_task_status_completed() -> None:
    async def _run() -> None:
        client = FakeCoderClient()
        client.task_payload = {
            "taskId": "task-123",
            "status": "completed",
            "result": {
                "summary": "已修复 health 检查并补充测试。",
                "fileChanges": [
                    {"path": "app.py", "changeType": "modified"},
                    {"path": "tests/test_health.py", "changeType": "added"},
                ],
                "testsPassed": True,
            },
        }
        skill = CoderSkill(coder_client=client)

        result = await skill.execute("coder_task_status", {"task_id": "task-123"})

        assert result.status == "success"
        assert result.result == "\n".join(
            [
                "任务 task-123 已完成",
                "摘要：已修复 health 检查并补充测试。",
                "文件变更：app.py（修改）, tests/test_health.py（新增）",
                "测试：通过",
            ]
        )

    asyncio.run(_run())


def test_task_status_failed() -> None:
    async def _run() -> None:
        client = FakeCoderClient()
        client.task_payload = {
            "taskId": "task-123",
            "status": "failed",
            "error": "pytest failed",
        }
        skill = CoderSkill(coder_client=client)

        result = await skill.execute("coder_task_status", {"task_id": "task-123"})

        assert result.status == "success"
        assert result.result == "任务 task-123 失败：pytest failed"

    asyncio.run(_run())


def test_coder_unavailable() -> None:
    async def _run() -> None:
        client = FakeCoderClient()
        client.health_payload = CoderUnavailableError("Hypo-Coder 当前不可用，请确认服务是否启动")
        skill = CoderSkill(coder_client=client)

        result = await skill.execute("coder_health", {})

        assert result.status == "error"
        assert result.error_info == "Hypo-Coder 当前不可用，请确认服务是否启动"

    asyncio.run(_run())
