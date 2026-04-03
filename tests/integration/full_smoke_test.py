from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi.testclient import TestClient


def _load_agent_cli_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "agent_cli.py"
    spec = importlib.util.spec_from_file_location("agent_cli_full_smoke_module", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class EchoPipeline:
    def __init__(self) -> None:
        self.inbounds = []

    async def stream_reply(self, inbound):
        self.inbounds.append(inbound)
        yield {
            "type": "assistant_chunk",
            "text": f"echo:{inbound.text}",
            "sender": "assistant",
            "session_id": inbound.session_id,
        }
        yield {
            "type": "assistant_done",
            "sender": "assistant",
            "session_id": inbound.session_id,
        }


class _AsyncClientWebSocket:
    def __init__(self, ws) -> None:
        self._ws = ws

    async def send(self, payload: str) -> None:
        await asyncio.to_thread(self._ws.send_text, payload)

    async def recv(self) -> str:
        return await asyncio.to_thread(self._ws.receive_text)


class _AsyncWebSocketConnect:
    def __init__(self, client: TestClient, uri: str) -> None:
        split = urlsplit(uri)
        self._client = client
        self._path = split.path if not split.query else f"{split.path}?{split.query}"
        self._context = None
        self._ws = None

    async def __aenter__(self):
        self._context = self._client.websocket_connect(self._path)
        self._ws = self._context.__enter__()
        return _AsyncClientWebSocket(self._ws)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        assert self._context is not None
        self._context.__exit__(exc_type, exc, tb)


def test_cli_send_roundtrip_uses_isolated_session(app_factory, monkeypatch, capsys) -> None:
    pipeline = EchoPipeline()
    app = app_factory(pipeline=pipeline)
    module = _load_agent_cli_module()

    with TestClient(app) as client:
        monkeypatch.setattr(module, "_load_token", lambda: "test-token")
        monkeypatch.setattr(
            module,
            "websockets",
            SimpleNamespace(connect=lambda uri: _AsyncWebSocketConnect(client, uri)),
        )

        result = asyncio.run(module.cmd_send("hello smoke", port=8766, session_id="smoke-cli", wait=1))

    captured = capsys.readouterr()
    assert result == 0
    assert "echo:hello smoke" in captured.out
    assert [item.session_id for item in pipeline.inbounds] == ["smoke-cli"]


def test_smoke_session_isolation_does_not_touch_main(app_factory, monkeypatch) -> None:
    pipeline = EchoPipeline()
    app = app_factory(pipeline=pipeline)
    module = _load_agent_cli_module()

    with TestClient(app) as client:
        monkeypatch.setattr(module, "_load_token", lambda: "test-token")
        monkeypatch.setattr(
            module,
            "websockets",
            SimpleNamespace(connect=lambda uri: _AsyncWebSocketConnect(client, uri)),
        )

        main_result = asyncio.run(module.cmd_send("main-only", port=8766, session_id="main", wait=1))
        result = asyncio.run(module.cmd_send("isolated", port=8766, session_id="smoke-iso", wait=1))

    assert main_result == 0
    assert result == 0
    assert [item.session_id for item in pipeline.inbounds] == ["main", "smoke-iso"]
    assert [item.text for item in pipeline.inbounds] == ["main-only", "isolated"]


def test_test_app_factory_has_no_external_sinks(app_factory) -> None:
    app = app_factory(pipeline=EchoPipeline())

    external_channels = [
        registration.name
        for registration in app.state.channel_dispatcher.registrations()
        if registration.is_external
    ]

    assert external_channels == []
