from __future__ import annotations

import asyncio

import httpx
import pytest

from hypo_agent.channels.coder.coder_client import CoderClient


def test_coder_client_reports_streaming_unsupported_by_default() -> None:
    client = CoderClient(base_url="http://localhost:11451", agent_token="test-token")

    assert client.supports_streaming() is False


def test_coder_client_reports_continuation_unsupported_by_default() -> None:
    client = CoderClient(base_url="http://localhost:11451", agent_token="test-token")

    assert client.supports_continuation() is False


def test_coder_client_reports_incremental_output_disabled_by_default() -> None:
    client = CoderClient(base_url="http://localhost:11451", agent_token="test-token")

    assert client.supports_incremental_output() is False


def test_coder_client_reports_incremental_output_when_enabled() -> None:
    client = CoderClient(
        base_url="http://localhost:11451",
        agent_token="test-token",
        incremental_output_enabled=True,
    )

    assert client.supports_incremental_output() is True


def test_coder_client_omits_model_when_not_provided(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"taskId": "task-123", "status": "queued"}

    class FakeAsyncClient:
        def __init__(self, *, base_url: str, timeout: float, headers: dict[str, str]) -> None:
            captured["base_url"] = base_url
            captured["timeout"] = timeout
            captured["headers"] = headers

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

        async def request(self, method: str, path: str, *, json=None, params=None):
            captured["method"] = method
            captured["path"] = path
            captured["json"] = json
            captured["params"] = params
            return FakeResponse()

    monkeypatch.setattr("hypo_agent.channels.coder.coder_client.httpx.AsyncClient", FakeAsyncClient)

    async def _run() -> None:
        client = CoderClient(base_url="http://localhost:11451", agent_token="test-token")
        payload = await client.create_task(
            prompt="inspect repo",
            working_directory="/tmp/repo",
            model=None,
        )
        assert payload == {"taskId": "task-123", "status": "queued"}

    asyncio.run(_run())

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/tasks"
    assert captured["json"] == {
        "prompt": "inspect repo",
        "workingDirectory": "/tmp/repo",
        "approvalPolicy": "full-auto",
    }


def test_coder_client_get_task_output_passes_after_cursor(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"cursor": "cursor-2", "lines": ["a", "b"], "done": False}

    class FakeAsyncClient:
        def __init__(self, *, base_url: str, timeout: float, headers: dict[str, str]) -> None:
            captured["base_url"] = base_url
            captured["timeout"] = timeout
            captured["headers"] = headers

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

        async def request(self, method: str, path: str, *, json=None, params=None):
            captured["method"] = method
            captured["path"] = path
            captured["json"] = json
            captured["params"] = params
            return FakeResponse()

    monkeypatch.setattr("hypo_agent.channels.coder.coder_client.httpx.AsyncClient", FakeAsyncClient)

    async def _run() -> None:
        client = CoderClient(
            base_url="http://localhost:11451",
            agent_token="test-token",
            incremental_output_enabled=True,
        )
        payload = await client.get_task_output("task-123", after="cursor-1")
        assert payload == {"cursor": "cursor-2", "lines": ["a", "b"], "done": False}

    asyncio.run(_run())

    assert captured["method"] == "GET"
    assert captured["path"] == "/api/tasks/task-123/output"
    assert captured["params"] == {"after": "cursor-1"}


@pytest.mark.parametrize("status_code", [404, 500])
def test_coder_client_get_task_output_returns_empty_on_unavailable_endpoint(
    monkeypatch,
    status_code: int,
) -> None:
    request = httpx.Request("GET", f"http://localhost:11451/api/tasks/task-123/output")

    class FakeResponse:
        def raise_for_status(self) -> None:
            response = httpx.Response(status_code=status_code, request=request)
            raise httpx.HTTPStatusError("endpoint unavailable", request=request, response=response)

        def json(self) -> dict[str, object]:
            return {}

    class FakeAsyncClient:
        def __init__(self, *, base_url: str, timeout: float, headers: dict[str, str]) -> None:
            del base_url, timeout, headers

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

        async def request(self, method: str, path: str, *, json=None, params=None):
            del method, path, json, params
            return FakeResponse()

    monkeypatch.setattr("hypo_agent.channels.coder.coder_client.httpx.AsyncClient", FakeAsyncClient)

    async def _run() -> None:
        client = CoderClient(
            base_url="http://localhost:11451",
            agent_token="test-token",
            incremental_output_enabled=True,
        )
        payload = await client.get_task_output("task-123")
        assert payload == {"cursor": "", "lines": [], "done": False}

    asyncio.run(_run())
