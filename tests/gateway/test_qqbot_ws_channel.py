from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from hypo_agent.channels.qq_bot_channel import QQBotChannelService, clear_qqbot_token_cache
from hypo_agent.gateway.qqbot_ws_client import QQBotWebSocketClient
from hypo_agent.models import Message


class QueuePipelineStub:
    def __init__(self) -> None:
        self.inbounds: list[Message] = []

    async def enqueue_user_message(self, inbound: Message, *, emit):
        self.inbounds.append(inbound)
        await emit(
            {
                "type": "assistant_done",
                "sender": "assistant",
                "session_id": inbound.session_id,
            }
        )

    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


class ProgressQueuePipelineStub(QueuePipelineStub):
    async def enqueue_user_message(self, inbound: Message, *, emit):
        self.inbounds.append(inbound)
        await emit(
            {
                "type": "pipeline_stage",
                "stage": "preprocessing",
                "detail": "正在分析你的消息...",
                "session_id": inbound.session_id,
            }
        )
        await emit(
            {
                "type": "tool_call_start",
                "tool_name": "search_web",
                "tool_call_id": "call-1",
                "session_id": inbound.session_id,
            }
        )
        await emit(
            {
                "type": "assistant_done",
                "sender": "assistant",
                "session_id": inbound.session_id,
            }
        )


class FakeConnection:
    def __init__(self, messages: list[dict[str, Any]], *, pause_after: float = 0.0) -> None:
        self._messages = [json.dumps(item) for item in messages]
        self.pause_after = pause_after
        self.sent: list[dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        return None

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._messages:
            return self._messages.pop(0)
        if self.pause_after > 0:
            await asyncio.sleep(self.pause_after)
        raise StopAsyncIteration

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


def _service() -> QQBotChannelService:
    return QQBotChannelService(app_id="1029384756", app_secret="bot-secret-xyz")


def test_qqbot_ws_client_fetches_gateway_and_identifies(monkeypatch) -> None:
    clear_qqbot_token_cache()
    calls: list[tuple[str, str]] = []
    connection = FakeConnection(
        [
            {"op": 10, "d": {"heartbeat_interval": 60000}},
            {"op": 0, "t": "READY", "s": 1, "d": {"session_id": "session-1", "user": {"id": "bot"}}},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.url.path.endswith("/getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        if request.url.path.endswith("/gateway"):
            assert request.headers["Authorization"] == "QQBot token-1"
            return httpx.Response(200, json={"url": "wss://gateway.qq.test"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    def fake_connect(url: str, *args, **kwargs):
        del args, kwargs
        assert url == "wss://gateway.qq.test"
        return connection

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)
    monkeypatch.setattr("hypo_agent.gateway.qqbot_ws_client.websockets.connect", fake_connect)

    client = QQBotWebSocketClient(
        service_getter=_service,
        pipeline_getter=lambda: object(),
    )

    asyncio.run(client.run_once())

    assert calls == [
        ("POST", "https://bots.qq.com/app/getAppAccessToken"),
        ("GET", "https://api.sgroup.qq.com/gateway"),
    ]
    assert connection.sent[0] == {
        "op": 2,
        "d": {
            "token": "QQBot token-1",
            "intents": client.intents,
            "shard": [0, 1],
        },
    }
    assert client.session_id == "session-1"
    assert client.seq == 1


def test_qqbot_ws_client_sends_heartbeat(monkeypatch) -> None:
    clear_qqbot_token_cache()
    connection = FakeConnection(
        [
            {"op": 10, "d": {"heartbeat_interval": 5}},
            {"op": 0, "t": "READY", "s": 7, "d": {"session_id": "session-1"}},
        ],
        pause_after=0.02,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        if request.url.path.endswith("/gateway"):
            return httpx.Response(200, json={"url": "wss://gateway.qq.test"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)
    monkeypatch.setattr(
        "hypo_agent.gateway.qqbot_ws_client.websockets.connect",
        lambda url, *args, **kwargs: connection,
    )

    client = QQBotWebSocketClient(
        service_getter=_service,
        pipeline_getter=lambda: object(),
    )

    asyncio.run(client.run_once())

    assert any(frame.get("op") == 1 and frame.get("d") == 7 for frame in connection.sent)


def test_qqbot_ws_client_dispatches_c2c_messages_to_pipeline(monkeypatch) -> None:
    clear_qqbot_token_cache()
    service = _service()
    pipeline = QueuePipelineStub()
    connection = FakeConnection(
        [
            {"op": 10, "d": {"heartbeat_interval": 60000}},
            {"op": 0, "t": "READY", "s": 1, "d": {"session_id": "session-1"}},
            {
                "op": 0,
                "t": "C2C_MESSAGE_CREATE",
                "s": 2,
                "d": {
                    "id": "msg-001",
                    "content": "<@!1029384756> 你好",
                    "timestamp": "2026-03-26T10:00:00+08:00",
                    "author": {
                        "user_openid": "OPENID-C2C-001",
                    },
                },
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        if request.url.path.endswith("/gateway"):
            return httpx.Response(200, json={"url": "wss://gateway.qq.test"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)
    monkeypatch.setattr(
        "hypo_agent.gateway.qqbot_ws_client.websockets.connect",
        lambda url, *args, **kwargs: connection,
    )

    client = QQBotWebSocketClient(
        service_getter=lambda: service,
        pipeline_getter=lambda: pipeline,
    )

    asyncio.run(client.run_once())

    assert len(pipeline.inbounds) == 1
    inbound = pipeline.inbounds[0]
    assert inbound.text == "你好"
    assert inbound.sender_id == "OPENID-C2C-001"
    assert inbound.metadata["qq"]["msg_id"] == "msg-001"


def test_qqbot_ws_client_resumes_existing_session(monkeypatch) -> None:
    clear_qqbot_token_cache()
    connection = FakeConnection([{"op": 10, "d": {"heartbeat_interval": 60000}}])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        if request.url.path.endswith("/gateway"):
            return httpx.Response(200, json={"url": "wss://gateway.qq.test"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)
    monkeypatch.setattr(
        "hypo_agent.gateway.qqbot_ws_client.websockets.connect",
        lambda url, *args, **kwargs: connection,
    )

    client = QQBotWebSocketClient(
        service_getter=_service,
        pipeline_getter=lambda: object(),
    )
    client.session_id = "session-1"
    client.seq = 42

    asyncio.run(client.run_once())

    assert connection.sent[0] == {
        "op": 6,
        "d": {
            "token": "QQBot token-1",
            "session_id": "session-1",
            "seq": 42,
        },
    }


def test_qqbot_ws_client_invalid_session_clears_resume_state(monkeypatch) -> None:
    clear_qqbot_token_cache()
    connection = FakeConnection(
        [
            {"op": 10, "d": {"heartbeat_interval": 60000}},
            {"op": 9, "d": False},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        if request.url.path.endswith("/gateway"):
            return httpx.Response(200, json={"url": "wss://gateway.qq.test"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)
    monkeypatch.setattr(
        "hypo_agent.gateway.qqbot_ws_client.websockets.connect",
        lambda url, *args, **kwargs: connection,
    )

    client = QQBotWebSocketClient(
        service_getter=_service,
        pipeline_getter=lambda: object(),
    )
    client.session_id = "session-1"
    client.seq = 42

    asyncio.run(client.run_once())

    assert client.session_id is None
    assert client.seq is None




def test_qqbot_ws_client_suppresses_mechanical_progress_events(monkeypatch) -> None:
    clear_qqbot_token_cache()
    service = _service()
    pipeline = ProgressQueuePipelineStub()
    pushed: list[Message] = []

    async def fake_send_message(message: Message):
        pushed.append(message)
        return None

    service.send_message = fake_send_message  # type: ignore[method-assign]
    connection = FakeConnection(
        [
            {"op": 10, "d": {"heartbeat_interval": 60000}},
            {"op": 0, "t": "READY", "s": 1, "d": {"session_id": "session-1"}},
            {
                "op": 0,
                "t": "C2C_MESSAGE_CREATE",
                "s": 2,
                "d": {
                    "id": "msg-001",
                    "content": "<@!1029384756> 你好",
                    "timestamp": "2026-03-26T10:00:00+08:00",
                    "author": {"user_openid": "OPENID-C2C-001"},
                },
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        if request.url.path.endswith("/gateway"):
            return httpx.Response(200, json={"url": "wss://gateway.qq.test"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("hypo_agent.channels.qq_bot_channel.httpx.AsyncClient", MockAsyncClient)
    monkeypatch.setattr(
        "hypo_agent.gateway.qqbot_ws_client.websockets.connect",
        lambda url, *args, **kwargs: connection,
    )

    client = QQBotWebSocketClient(
        service_getter=lambda: service,
        pipeline_getter=lambda: pipeline,
    )

    asyncio.run(client.run_once())

    assert pushed == []
