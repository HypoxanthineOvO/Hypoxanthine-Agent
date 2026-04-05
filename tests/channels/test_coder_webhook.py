from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from pathlib import Path

from fastapi.testclient import TestClient

from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import Message


class DummyPipeline:
    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


def _build_client(tmp_path: Path) -> TestClient:
    deps = AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
    )
    app = create_app(auth_token="test-token", pipeline=DummyPipeline(), deps=deps)
    return TestClient(app)


def _sign(secret: str, payload: dict) -> tuple[bytes, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, f"sha256={digest}"


def test_webhook_completed(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    pushed: list[Message] = []

    async def capture(message: Message) -> None:
        pushed.append(message)

    payload = {
        "event": "task.completed",
        "taskId": "task-abc123",
        "timestamp": "2026-03-27T11:00:00Z",
        "result": {
            "summary": "已修复 hello.py 并通过校验。",
            "fileChanges": [{"path": "hello.py", "changeType": "modified"}],
            "testsPassed": True,
        },
    }
    body, signature = _sign("coder-secret", payload)

    with client:
        asyncio.run(
            client.app.state.structured_store.create_coder_task(
                task_id="task-abc123",
                session_id="s-coder",
                working_directory="/repo/demo",
                prompt_summary="fix hello",
                model="o4-mini",
                status="running",
                attached=True,
            )
        )
        client.app.state.coder_webhook_secret = "coder-secret"
        client.app.state.pipeline.on_proactive_message = capture
        response = client.post(
            "/api/coder/webhook",
            content=body,
            headers={
                "content-type": "application/json",
                "X-HypoCoder-Event": "task.completed",
                "X-HypoCoder-Signature": signature,
            },
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(pushed) == 1
    assert pushed[0].message_tag == "tool_status"
    assert pushed[0].session_id == "s-coder"
    assert "编码任务完成！" in str(pushed[0].text)
    assert "文件变更：1 个文件" in str(pushed[0].text)
    assert "测试：通过" in str(pushed[0].text)
    task = asyncio.run(client.app.state.structured_store.get_coder_task("task-abc123"))
    assert task is not None
    assert task["status"] == "completed"


def test_webhook_failed(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    pushed: list[Message] = []

    async def capture(message: Message) -> None:
        pushed.append(message)

    payload = {
        "event": "task.failed",
        "taskId": "task-abc123",
        "timestamp": "2026-03-27T11:00:00Z",
        "error": "pytest failed",
    }
    body, signature = _sign("coder-secret", payload)

    with client:
        asyncio.run(
            client.app.state.structured_store.create_coder_task(
                task_id="task-abc123",
                session_id="s-coder",
                working_directory="/repo/demo",
                prompt_summary="fix hello",
                model="o4-mini",
                status="running",
                attached=True,
            )
        )
        client.app.state.coder_webhook_secret = "coder-secret"
        client.app.state.pipeline.on_proactive_message = capture
        response = client.post(
            "/api/coder/webhook",
            content=body,
            headers={
                "content-type": "application/json",
                "X-HypoCoder-Event": "task.failed",
                "X-HypoCoder-Signature": signature,
            },
        )

    assert response.status_code == 200
    assert len(pushed) == 1
    assert "编码任务失败：pytest failed" == pushed[0].text
    assert pushed[0].session_id == "s-coder"
    task = asyncio.run(client.app.state.structured_store.get_coder_task("task-abc123"))
    assert task is not None
    assert task["status"] == "failed"
    assert task["last_error"] == "pytest failed"


def test_webhook_detached_task_updates_db_without_push(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    pushed: list[Message] = []

    async def capture(message: Message) -> None:
        pushed.append(message)

    payload = {
        "event": "task.completed",
        "taskId": "task-abc123",
        "timestamp": "2026-03-27T11:00:00Z",
        "result": {
            "summary": "已修复 hello.py 并通过校验。",
            "fileChanges": [{"path": "hello.py", "changeType": "modified"}],
            "testsPassed": True,
        },
    }
    body, signature = _sign("coder-secret", payload)

    with client:
        asyncio.run(
            client.app.state.structured_store.create_coder_task(
                task_id="task-abc123",
                session_id="s-coder",
                working_directory="/repo/demo",
                prompt_summary="fix hello",
                model="o4-mini",
                status="running",
                attached=False,
            )
        )
        client.app.state.coder_webhook_secret = "coder-secret"
        client.app.state.pipeline.on_proactive_message = capture
        response = client.post(
            "/api/coder/webhook",
            content=body,
            headers={
                "content-type": "application/json",
                "X-HypoCoder-Event": "task.completed",
                "X-HypoCoder-Signature": signature,
            },
        )

    assert response.status_code == 200
    assert pushed == []
    task = asyncio.run(client.app.state.structured_store.get_coder_task("task-abc123"))
    assert task is not None
    assert task["status"] == "completed"


def test_webhook_invalid_secret(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    payload = {
        "event": "task.completed",
        "taskId": "task-abc123",
        "timestamp": "2026-03-27T11:00:00Z",
        "result": {"summary": "done"},
    }
    body, _ = _sign("wrong-secret", payload)

    with client:
        client.app.state.coder_webhook_secret = "coder-secret"
        response = client.post(
            "/api/coder/webhook",
            content=body,
            headers={
                "content-type": "application/json",
                "X-HypoCoder-Event": "task.completed",
                "X-HypoCoder-Signature": "sha256=deadbeef",
            },
        )

    assert response.status_code == 403
