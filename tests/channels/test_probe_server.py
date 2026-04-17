from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hypo_agent.core.event_queue import EventQueue
from hypo_agent.gateway.app import AppDeps, create_app
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore

JPEG_BASE64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAkGBxAQEBUQEBAVFhUVFRUVFRUVFRUVFRUVFRUWFhUV"
    "FRUYHSggGBolHRUVITEhJSkrLi4uFx8zODMsNygtLisBCgoKDg0OGxAQGi0fICUtLS0tLS0tLS0t"
    "LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLf/AABEIAAEAAgMBIgACEQEDEQH/"
    "xAAbAAABBQEBAAAAAAAAAAAAAAAEAAIDBQYBB//EADYQAAEDAgQDBgQEBwAAAAAAAAECAxEABAUS"
    "ITFBEyJRYQYUMnGBkaGxwdHwI0JS4fEVFiQzQ1NygpL/xAAZAQADAQEBAAAAAAAAAAAAAAABAgME"
    "AAX/xAAkEQACAgICAgIDAQAAAAAAAAAAAQIRAxIhMQQTQVEiMmFxgf/aAAwDAQACEQMRAD8A9xREQ"
    "EREBERAREQEREBERAREQEREBERB//2Q=="
)


class PassivePipeline:
    async def start_event_consumer(self) -> None:
        return None

    async def stop_event_consumer(self) -> None:
        return None

    async def stream_reply(self, inbound):
        del inbound
        if False:  # pragma: no cover
            yield {}


class NoopScheduler:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class FakeProbeWebSocket:
    def __init__(self) -> None:
        self.sent_payloads: list[dict] = []
        self._recv_queue: asyncio.Queue[object] = asyncio.Queue()
        self.closed: tuple[int, str] | None = None
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self) -> dict:
        item = await self._recv_queue.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def send_json(self, payload: dict) -> None:
        self.sent_payloads.append(payload)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = (code, reason or "")

    async def push(self, payload: dict | BaseException) -> None:
        await self._recv_queue.put(payload)


def _build_probe_client(
    tmp_path,
    *,
    probe_server,
) -> TestClient:
    app = create_app(
        auth_token="test-token",
        pipeline=PassivePipeline(),
        deps=AppDeps(
            session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
            structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
            event_queue=EventQueue(),
            scheduler=NoopScheduler(),
            probe_server=probe_server,
        ),
    )
    return TestClient(app)


async def _drain() -> None:
    await asyncio.sleep(0.05)


def test_hello_valid_token(tmp_path) -> None:
    from hypo_agent.channels.probe.probe_server import ProbeServer

    probe_server = ProbeServer(token="probe-secret")

    with _build_probe_client(tmp_path, probe_server=probe_server) as client:
        with client.websocket_connect("/ws/probe") as ws:
            ws.send_json(
                {
                    "type": "hello",
                    "token": "probe-secret",
                    "device_id": "windows-office",
                    "platform": "windows",
                    "version": "1.0.0",
                    "capabilities": ["screenshot", "process_list"],
                }
            )
            payload = ws.receive_json()

            assert payload["type"] == "welcome"
            assert payload["device_id"] == "windows-office"

            record = probe_server.devices["windows-office"]
            assert record.device_id == "windows-office"
            assert record.platform == "windows"
            assert record.online is True
            assert record.capabilities == ["screenshot", "process_list"]


def test_hello_invalid_token(tmp_path) -> None:
    from hypo_agent.channels.probe.probe_server import ProbeServer

    probe_server = ProbeServer(token="probe-secret")

    with _build_probe_client(tmp_path, probe_server=probe_server) as client:
        with client.websocket_connect("/ws/probe") as ws:
            ws.send_json(
                {
                    "type": "hello",
                    "token": "wrong-token",
                    "device_id": "windows-office",
                    "platform": "windows",
                    "version": "1.0.0",
                    "capabilities": [],
                }
            )
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 4401


def test_hello_timeout(tmp_path) -> None:
    from hypo_agent.channels.probe.probe_server import ProbeServer

    probe_server = ProbeServer(token="probe-secret", hello_timeout_seconds=0.01)

    with _build_probe_client(tmp_path, probe_server=probe_server) as client:
        with client.websocket_connect("/ws/probe") as ws:
            time.sleep(0.05)
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()


def test_device_reregister(tmp_path) -> None:
    from hypo_agent.channels.probe.probe_server import ProbeServer

    probe_server = ProbeServer(token="probe-secret")
    hello_payload = {
        "type": "hello",
        "token": "probe-secret",
        "device_id": "windows-office",
        "platform": "windows",
        "version": "1.0.0",
        "capabilities": ["screenshot"],
    }

    with _build_probe_client(tmp_path, probe_server=probe_server) as client:
        with client.websocket_connect("/ws/probe") as first:
            first.send_json(hello_payload)
            assert first.receive_json()["type"] == "welcome"

            with client.websocket_connect("/ws/probe") as second:
                second.send_json(hello_payload)
                assert second.receive_json()["type"] == "welcome"
                time.sleep(0.05)

                with pytest.raises(WebSocketDisconnect):
                    first.receive_json()

                record = probe_server.devices["windows-office"]
                assert record.online is True
                assert record.device_id == "windows-office"


def test_ping_pong(tmp_path) -> None:
    from hypo_agent.channels.probe.probe_server import ProbeServer

    probe_server = ProbeServer(token="probe-secret")

    with _build_probe_client(tmp_path, probe_server=probe_server) as client:
        with client.websocket_connect("/ws/probe") as ws:
            ws.send_json(
                {
                    "type": "hello",
                    "token": "probe-secret",
                    "device_id": "mock-device",
                    "platform": "linux",
                    "version": "1.0.0",
                    "capabilities": [],
                }
            )
            assert ws.receive_json()["type"] == "welcome"

            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}


def test_device_offline_on_disconnect(tmp_path) -> None:
    from hypo_agent.channels.probe.probe_server import ProbeServer

    probe_server = ProbeServer(token="probe-secret")

    with _build_probe_client(tmp_path, probe_server=probe_server) as client:
        with client.websocket_connect("/ws/probe") as ws:
            ws.send_json(
                {
                    "type": "hello",
                    "token": "probe-secret",
                    "device_id": "mock-device",
                    "platform": "linux",
                    "version": "1.0.0",
                    "capabilities": [],
                }
            )
            assert ws.receive_json()["type"] == "welcome"

        time.sleep(0.05)
        assert probe_server.devices["mock-device"].online is False


def test_heartbeat_offline_alert() -> None:
    from hypo_agent.channels.probe.probe_server import DeviceInfo, ProbeServer

    now = datetime(2026, 3, 26, 10, 30, 0, tzinfo=UTC)
    probe_server = ProbeServer(
        token="probe-secret",
        offline_timeout_seconds=90,
        now_fn=lambda: now,
    )
    probe_server.devices["windows-office"] = DeviceInfo(
        device_id="windows-office",
        platform="windows",
        version="1.0.0",
        capabilities=["screenshot"],
        websocket=None,
        connected_at=now - timedelta(minutes=10),
        last_seen=now - timedelta(minutes=2),
        online=True,
    )

    summary = asyncio.run(probe_server.probe_heartbeat_callback())

    assert summary == "设备 windows-office 已离线"
    assert probe_server.devices["windows-office"].online is False


def test_screenshot_push_stored(tmp_path) -> None:
    from hypo_agent.channels.probe.probe_server import ProbeServer

    async def _run() -> None:
        probe_server = ProbeServer(
            token="probe-secret",
            now_fn=lambda: datetime(2026, 3, 26, 13, 5, 0, tzinfo=UTC),
        )
        probe_server.configure(type("Cfg", (), {"token": "probe-secret", "screenshot_dir": str(tmp_path)})())
        ws = FakeProbeWebSocket()
        task = asyncio.create_task(probe_server.handle_connection(ws))
        await ws.push(
            {
                "type": "hello",
                "token": "probe-secret",
                "device_id": "mock-device",
                "platform": "linux",
                "version": "1.0.0",
                "capabilities": ["screenshot"],
            }
        )
        await _drain()
        await ws.push(
            {
                "type": "screenshot_push",
                "timestamp": "2026-03-26T13:05:00Z",
                "idle": False,
                "image_base64": JPEG_BASE64,
            }
        )
        await _drain()
        await ws.push(WebSocketDisconnect(code=1000))
        await task

        shot_path = tmp_path / "mock-device" / "2026-03-26" / "13-05-00.jpg"
        index_path = tmp_path / "mock-device" / "2026-03-26" / "index.jsonl"
        assert shot_path.exists()
        assert shot_path.read_bytes() == base64.b64decode(JPEG_BASE64)
        assert index_path.exists()
        lines = index_path.read_text(encoding="utf-8").splitlines()
        assert lines == [
            json.dumps(
                {"timestamp": "2026-03-26T21:05:00+08:00", "idle": False, "path": "13-05-00.jpg"},
                ensure_ascii=False,
            )
        ]
        assert ws.sent_payloads[-1] == {
            "type": "ack",
            "stored": True,
            "path": str(shot_path.resolve(strict=False)),
        }
        record = probe_server.devices["mock-device"]
        assert record.last_screenshot_at == datetime(2026, 3, 26, 13, 5, 0, tzinfo=UTC)
        assert record.idle is False

    asyncio.run(_run())


def test_screenshot_push_idle(tmp_path) -> None:
    from hypo_agent.channels.probe.probe_server import ProbeServer

    async def _run() -> None:
        probe_server = ProbeServer(token="probe-secret")
        probe_server.configure(type("Cfg", (), {"token": "probe-secret", "screenshot_dir": str(tmp_path)})())
        ws = FakeProbeWebSocket()
        task = asyncio.create_task(probe_server.handle_connection(ws))
        await ws.push(
            {
                "type": "hello",
                "token": "probe-secret",
                "device_id": "idle-device",
                "platform": "linux",
                "version": "1.0.0",
                "capabilities": ["screenshot"],
            }
        )
        await _drain()
        await ws.push(
            {
                "type": "screenshot_push",
                "timestamp": "2026-03-26T13:10:00Z",
                "idle": True,
                "image_base64": "",
            }
        )
        await _drain()
        await ws.push(WebSocketDisconnect(code=1000))
        await task

        day_dir = tmp_path / "idle-device" / "2026-03-26"
        assert not (day_dir / "13-10-00.jpg").exists()
        index_path = day_dir / "index.jsonl"
        assert index_path.read_text(encoding="utf-8").splitlines() == [
            json.dumps(
                {"timestamp": "2026-03-26T21:10:00+08:00", "idle": True, "path": None},
                ensure_ascii=False,
            )
        ]
        assert ws.sent_payloads[-1] == {
            "type": "ack",
            "stored": True,
            "path": None,
        }

    asyncio.run(_run())


def test_rpc_timeout() -> None:
    from hypo_agent.channels.probe.probe_server import DeviceInfo, ProbeRPCError, ProbeServer

    async def _run() -> None:
        now = datetime(2026, 3, 26, 13, 10, 0, tzinfo=UTC)
        probe_server = ProbeServer(token="probe-secret", now_fn=lambda: now)
        ws = FakeProbeWebSocket()
        probe_server.devices["mock-device"] = DeviceInfo(
            device_id="mock-device",
            platform="linux",
            version="1.0.0",
            capabilities=["screenshot", "process_list"],
            websocket=ws,
            connected_at=now,
            last_seen=now,
            last_screenshot_at=None,
            idle=False,
            online=True,
        )

        with pytest.raises(ProbeRPCError, match="timeout"):
            await probe_server.send_rpc("mock-device", "screenshot", timeout=0.01)

        assert ws.sent_payloads[0]["type"] == "request"
        assert ws.sent_payloads[0]["action"] == "screenshot"

    asyncio.run(_run())
