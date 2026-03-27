from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
import json

from hypo_agent.channels.probe.probe_server import DeviceInfo, ProbeServer
from hypo_agent.skills.probe_skill import ProbeSkill

JPEG_BASE64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAkGBxAQEBUQEBAVFhUVFRUVFRUVFRUVFRUVFRUWFhUV"
    "FRUYHSggGBolHRUVITEhJSkrLi4uFx8zODMsNygtLisBCgoKDg0OGxAQGi0fICUtLS0tLS0tLS0t"
    "LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLf/AABEIAAEAAgMBIgACEQEDEQH/"
    "xAAbAAABBQEBAAAAAAAAAAAAAAAEAAIDBQYBB//EADYQAAEDAgQDBgQEBwAAAAAAAAECAxEABAUS"
    "ITFBEyJRYQYUMnGBkaGxwdHwI0JS4fEVFiQzQ1NygpL/xAAZAQADAQEBAAAAAAAAAAAAAAABAgME"
    "AAX/xAAkEQACAgICAgIDAQAAAAAAAAAAAQIRAxIhMQQTQVEiMmFxgf/aAAwDAQACEQMRAD8A9xREQ"
    "EREBERAREQEREBERAREQEREBERB//2Q=="
)


class StubProbeServer(ProbeServer):
    def __init__(self) -> None:
        super().__init__(token="probe-secret")
        self.rpc_calls: list[tuple[str, str, dict, float]] = []
        self.rpc_response: dict | Exception = {}

    async def send_rpc(
        self,
        device_id: str,
        action: str,
        params: dict | None = None,
        timeout: float = 30.0,
    ) -> dict:
        self.rpc_calls.append((device_id, action, dict(params or {}), timeout))
        if isinstance(self.rpc_response, Exception):
            raise self.rpc_response
        return dict(self.rpc_response)


def test_list_devices_empty() -> None:
    async def _run() -> None:
        skill = ProbeSkill(probe_server=ProbeServer(token="probe-secret"))
        output = await skill.execute("probe_list_devices", {})

        assert output.status == "success"
        assert output.result == []
        assert output.metadata["message"] == "目前没有在线的探针设备"

    asyncio.run(_run())


def test_rpc_screenshot(tmp_path) -> None:
    async def _run() -> None:
        probe_server = StubProbeServer()
        probe_server.configure(type("Cfg", (), {"token": "probe-secret", "screenshot_dir": str(tmp_path)})())
        probe_server.rpc_response = {
            "image_base64": JPEG_BASE64,
            "width": 100,
            "height": 100,
            "timestamp": "2026-03-26T13:05:00Z",
            "idle": False,
        }
        skill = ProbeSkill(probe_server=probe_server)

        output = await skill.execute("probe_screenshot", {"device_id": "mock-device"})

        assert output.status == "success"
        assert output.attachments and output.attachments[0].type == "image"
        assert output.attachments[0].url.endswith("13-05-00.jpg")
        assert base64.b64decode(JPEG_BASE64) == open(output.attachments[0].url, "rb").read()
        assert probe_server.rpc_calls == [("mock-device", "screenshot", {}, 30.0)]

    asyncio.run(_run())


def test_rpc_screenshot_idle() -> None:
    async def _run() -> None:
        probe_server = StubProbeServer()
        probe_server.rpc_response = {
            "timestamp": "2026-03-26T13:05:00Z",
            "idle": True,
            "image_base64": "",
        }
        skill = ProbeSkill(probe_server=probe_server)

        output = await skill.execute("probe_screenshot", {"device_id": "mock-device"})

        assert output.status == "success"
        assert output.attachments == []
        assert output.result == "设备当前处于息屏/空闲状态"

    asyncio.run(_run())


def test_rpc_process_list() -> None:
    async def _run() -> None:
        probe_server = StubProbeServer()
        probe_server.rpc_response = {
            "items": [
                {"pid": 1234, "name": "chrome.exe", "cpu_percent": 15.2, "ram_mb": 512},
                {"pid": 5678, "name": "code.exe", "cpu_percent": 8.1, "ram_mb": 256},
            ]
        }
        skill = ProbeSkill(probe_server=probe_server)

        output = await skill.execute("probe_process_list", {"device_id": "mock-device", "top_n": 2})

        assert output.status == "success"
        assert "chrome.exe" in output.result
        assert "15.2" in output.result
        assert "code.exe" in output.result
        assert probe_server.rpc_calls == [("mock-device", "process_list", {"top_n": 2}, 30.0)]

    asyncio.run(_run())


def test_list_screenshots(tmp_path) -> None:
    async def _run() -> None:
        probe_server = ProbeServer(token="probe-secret")
        probe_server.configure(type("Cfg", (), {"token": "probe-secret", "screenshot_dir": str(tmp_path)})())
        day_dir = tmp_path / "mock-device" / "2026-03-26"
        day_dir.mkdir(parents=True, exist_ok=True)
        (day_dir / "index.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {"timestamp": "2026-03-26T13:05:00Z", "idle": False, "path": "13-05-00.jpg"},
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {"timestamp": "2026-03-26T13:10:00Z", "idle": True, "path": None},
                        ensure_ascii=False,
                    ),
                ]
            ),
            encoding="utf-8",
        )
        skill = ProbeSkill(probe_server=probe_server)

        output = await skill.execute(
            "probe_list_screenshots",
            {"device_id": "mock-device", "date": "2026-03-26"},
        )

        assert output.status == "success"
        assert output.result == [
            {"timestamp": "2026-03-26T13:05:00Z", "idle": False, "path": "13-05-00.jpg"},
            {"timestamp": "2026-03-26T13:10:00Z", "idle": True, "path": None},
        ]

    asyncio.run(_run())


def test_list_devices_with_mock() -> None:
    async def _run() -> None:
        now = datetime(2026, 3, 26, 10, 30, 0, tzinfo=UTC)
        probe_server = ProbeServer(token="probe-secret", now_fn=lambda: now)
        probe_server.devices["windows-office"] = DeviceInfo(
            device_id="windows-office",
            platform="windows",
            version="1.0.0",
            capabilities=["screenshot", "process_list"],
            websocket=None,
            connected_at=datetime(2026, 3, 26, 10, 0, 0, tzinfo=UTC),
            last_seen=now,
            online=True,
        )
        skill = ProbeSkill(probe_server=probe_server)

        output = await skill.execute("probe_list_devices", {})

        assert output.status == "success"
        assert output.result == [
            {
                "device_id": "windows-office",
                "platform": "windows",
                "online": True,
                "capabilities": ["screenshot", "process_list"],
                "connected_at": "2026-03-26T10:00:00Z",
                "last_seen": "2026-03-26T10:30:00Z",
            }
        ]

    asyncio.run(_run())
