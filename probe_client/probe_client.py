#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any

import websockets
import yaml

if __package__ in {None, ""}:
    ROOT_DIR = Path(__file__).resolve().parent
    if str(ROOT_DIR.parent) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR.parent))
    from probe_client.platform import macos, windows  # type: ignore
else:  # pragma: no cover
    from .platform import macos, windows


def utc_now_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_config(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid config payload: {path}")
    return payload


class ProbeClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.server = dict(config.get("server") or {})
        self.device = dict(config.get("device") or {})
        self.screenshot = dict(config.get("screenshot") or {})
        self._send_lock = asyncio.Lock()
        self._backend = self._select_backend()

    async def run_forever(self) -> None:
        while True:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"probe_client reconnecting after error: {exc}", file=sys.stderr)
                await asyncio.sleep(5)

    async def _run_once(self) -> None:
        async with websockets.connect(self.server["url"]) as websocket:
            await self._send_json(websocket, self._hello_payload())
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(websocket))
            screenshot_task = asyncio.create_task(self._screenshot_loop(websocket))
            try:
                async for raw in websocket:
                    print(raw)
                    await self._handle_message(websocket, raw)
            finally:
                heartbeat_task.cancel()
                screenshot_task.cancel()
                await asyncio.gather(heartbeat_task, screenshot_task, return_exceptions=True)

    async def _handle_message(self, websocket: websockets.ClientConnection, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        if str(payload.get("type") or "").strip().lower() != "request":
            return
        request_id = str(payload.get("request_id") or "").strip()
        action = str(payload.get("action") or "").strip()
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        try:
            data = await self.handle_request({"action": action, "params": params})
            response = {
                "type": "response",
                "request_id": request_id,
                "success": True,
                "data": data,
            }
        except Exception as exc:
            response = {
                "type": "response",
                "request_id": request_id,
                "success": False,
                "error": str(exc),
            }
        await self._send_json(websocket, response)

    async def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        action = str(request.get("action") or "").strip()
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        if action == "screenshot":
            return await self.do_screenshot()
        if action == "process_list":
            top_n = max(1, int(params.get("top_n", 20)))
            return {"items": await self.do_process_list(top_n)}
        raise ValueError(f"Unknown action: {action}")

    async def do_screenshot(self) -> dict[str, Any]:
        timestamp = utc_now_text()
        idle = await asyncio.to_thread(self._backend.is_idle)
        if idle:
            return {"timestamp": timestamp, "idle": True, "image_base64": ""}
        screenshot = await asyncio.to_thread(
            self._backend.take_screenshot,
            quality=int(self.screenshot.get("quality", 85)),
        )
        if bool(screenshot.get("black_frame")):
            return {"timestamp": timestamp, "idle": True, "image_base64": ""}
        return {
            "timestamp": timestamp,
            "idle": False,
            "image_base64": str(screenshot.get("image_base64") or ""),
            "width": int(screenshot.get("width") or 0),
            "height": int(screenshot.get("height") or 0),
        }

    async def do_process_list(self, top_n: int) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._backend.get_process_list, top_n)

    async def _heartbeat_loop(self, websocket: websockets.ClientConnection) -> None:
        while True:
            await asyncio.sleep(30)
            await self._send_json(websocket, {"type": "ping"})

    async def _screenshot_loop(self, websocket: websockets.ClientConnection) -> None:
        if not bool(self.screenshot.get("enabled", True)):
            return
        interval_minutes = max(1, int(self.screenshot.get("interval_minutes", 5)))
        while True:
            await asyncio.sleep(interval_minutes * 60)
            if not self._within_active_hours():
                continue
            payload = await self.do_screenshot()
            push_payload = {
                "type": "screenshot_push",
                "timestamp": payload["timestamp"],
                "idle": bool(payload.get("idle")),
                "image_base64": "" if payload.get("idle") else str(payload.get("image_base64") or ""),
            }
            await self._send_json(websocket, push_payload)

    def _within_active_hours(self) -> bool:
        raw_hours = self.screenshot.get("active_hours", [0, 24])
        if not isinstance(raw_hours, list) or len(raw_hours) != 2:
            return True
        try:
            start_hour = int(raw_hours[0])
            end_hour = int(raw_hours[1])
        except (TypeError, ValueError):
            return True
        current_hour = datetime.now().hour
        if start_hour <= end_hour:
            return start_hour <= current_hour < end_hour
        return current_hour >= start_hour or current_hour < end_hour

    def _hello_payload(self) -> dict[str, Any]:
        return {
            "type": "hello",
            "token": str(self.server.get("token") or ""),
            "device_id": str(self.device.get("device_id") or ""),
            "platform": str(self.device.get("platform") or ""),
            "version": str(self.device.get("version") or "1.0.0"),
            "capabilities": list(self.device.get("capabilities") or ["screenshot", "process_list"]),
        }

    async def _send_json(self, websocket: websockets.ClientConnection, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            await websocket.send(json.dumps(payload, ensure_ascii=False))

    def _select_backend(self) -> Any:
        platform_name = str(self.device.get("platform") or sys.platform).strip().lower()
        if platform_name.startswith("win"):
            return windows
        if platform_name in {"mac", "macos", "darwin"} or sys.platform == "darwin":
            return macos
        raise RuntimeError(f"Unsupported platform for probe_client: {platform_name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone Probe client.")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to probe client config YAML.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config_path = Path(args.config).expanduser().resolve(strict=False)
    client = ProbeClient(load_config(config_path))
    try:
        asyncio.run(client.run_forever())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
