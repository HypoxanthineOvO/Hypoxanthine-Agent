from __future__ import annotations

import asyncio
import json

from hypo_agent.gateway.qq_ws_client import NapCatWebSocketClient


class FakeConnection:
    def __init__(self, messages: list[str]) -> None:
        self._messages = list(messages)

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
        raise StopAsyncIteration


class RecordingQQService:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, object]] = []

    async def handle_onebot_event(self, payload: dict, *, pipeline: object) -> bool:
        self.calls.append((payload, pipeline))
        return True


def test_napcat_websocket_client_consumes_messages(monkeypatch) -> None:
    service = RecordingQQService()
    pipeline = object()

    def fake_connect(url: str, *args, **kwargs):
        del args, kwargs
        assert url == "ws://127.0.0.1:3009/onebot/v11/ws"
        return FakeConnection(
            [
                json.dumps(
                    {
                        "post_type": "message",
                        "message_type": "private",
                        "user_id": "10001",
                        "message": "hello-from-napcat",
                    }
                )
            ]
        )

    monkeypatch.setattr("hypo_agent.gateway.qq_ws_client.websockets.connect", fake_connect)

    client = NapCatWebSocketClient(
        url="ws://127.0.0.1:3009/onebot/v11/ws",
        service_getter=lambda: service,
        pipeline_getter=lambda: pipeline,
        reconnect_delay_seconds=0.01,
    )

    asyncio.run(client.run_once())

    assert len(service.calls) == 1
    assert service.calls[0][0]["message"] == "hello-from-napcat"
    assert service.calls[0][1] is pipeline


def test_napcat_websocket_client_appends_access_token_to_url(monkeypatch) -> None:
    service = RecordingQQService()
    pipeline = object()
    captured: dict[str, str] = {}

    def fake_connect(url: str, *args, **kwargs):
        del args, kwargs
        captured["url"] = url
        return FakeConnection([])

    monkeypatch.setattr("hypo_agent.gateway.qq_ws_client.websockets.connect", fake_connect)

    client = NapCatWebSocketClient(
        url="ws://127.0.0.1:3009/onebot/v11/ws",
        token="ws-token-123",
        service_getter=lambda: service,
        pipeline_getter=lambda: pipeline,
    )

    asyncio.run(client.run_once())

    assert captured["url"] == "ws://127.0.0.1:3009/onebot/v11/ws?access_token=ws-token-123"


def test_napcat_websocket_client_omits_access_token_when_empty(monkeypatch) -> None:
    service = RecordingQQService()
    pipeline = object()
    captured: dict[str, str] = {}

    def fake_connect(url: str, *args, **kwargs):
        del args, kwargs
        captured["url"] = url
        return FakeConnection([])

    monkeypatch.setattr("hypo_agent.gateway.qq_ws_client.websockets.connect", fake_connect)

    client = NapCatWebSocketClient(
        url="ws://127.0.0.1:3009/onebot/v11/ws",
        token="",
        service_getter=lambda: service,
        pipeline_getter=lambda: pipeline,
    )

    asyncio.run(client.run_once())

    assert captured["url"] == "ws://127.0.0.1:3009/onebot/v11/ws"


def test_napcat_websocket_client_counts_only_handled_messages(monkeypatch) -> None:
    class IgnoringQQService:
        async def handle_onebot_event(self, payload: dict, *, pipeline: object) -> bool:
            del payload, pipeline
            return False

    service = IgnoringQQService()
    pipeline = object()

    def fake_connect(url: str, *args, **kwargs):
        del url, args, kwargs
        return FakeConnection(
            [
                json.dumps(
                    {
                        "post_type": "message",
                        "message_type": "private",
                        "user_id": "10001",
                        "message": "ignored",
                    }
                )
            ]
        )

    monkeypatch.setattr("hypo_agent.gateway.qq_ws_client.websockets.connect", fake_connect)

    client = NapCatWebSocketClient(
        url="ws://127.0.0.1:3009/onebot/v11/ws",
        service_getter=lambda: service,
        pipeline_getter=lambda: pipeline,
    )

    asyncio.run(client.run_once())

    assert client.messages_received == 0
    assert client.last_message_at is None
