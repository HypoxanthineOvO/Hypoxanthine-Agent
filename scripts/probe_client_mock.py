#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
from datetime import UTC, datetime
import json

import websockets

JPEG_BASE64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAkGBxAQEBUQEBAVFhUVFRUVFRUVFRUVFRUVFRUWFhUV"
    "FRUYHSggGBolHRUVITEhJSkrLi4uFx8zODMsNygtLisBCgoKDg0OGxAQGi0fICUtLS0tLS0tLS0t"
    "LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLf/AABEIAAEAAgMBIgACEQEDEQH/"
    "xAAbAAABBQEBAAAAAAAAAAAAAAAEAAIDBQYBB//EADYQAAEDAgQDBgQEBwAAAAAAAAECAxEABAUS"
    "ITFBEyJRYQYUMnGBkaGxwdHwI0JS4fEVFiQzQ1NygpL/xAAZAQADAQEBAAAAAAAAAAAAAAABAgME"
    "AAX/xAAkEQACAgICAgIDAQAAAAAAAAAAAQIRAxIhMQQTQVEiMmFxgf/aAAwDAQACEQMRAD8A9xREQ"
    "EREBERAREQEREBERAREQEREBERB//2Q=="
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mock Probe client for local testing.")
    parser.add_argument(
        "--url",
        default="ws://localhost:8090/ws/probe",
        help="Probe websocket URL.",
    )
    parser.add_argument(
        "--token",
        default="your-probe-token-here",
        help="Probe auth token.",
    )
    parser.add_argument(
        "--device-id",
        default="mock-device",
        help="Probe device identifier.",
    )
    parser.add_argument(
        "--platform",
        default="linux",
        help="Probe platform value.",
    )
    parser.add_argument(
        "--version",
        default="1.0.0",
        help="Probe client version.",
    )
    parser.add_argument(
        "--capability",
        dest="capabilities",
        action="append",
        default=None,
        help="Capability to advertise. Repeat for multiple values.",
    )
    parser.add_argument(
        "--idle",
        action="store_true",
        help="Always report screenshots as idle/screen-off.",
    )
    parser.add_argument(
        "--push-interval-seconds",
        type=int,
        default=300,
        help="Interval for proactive screenshot pushes.",
    )
    return parser


async def send_heartbeats(websocket: websockets.ClientConnection) -> None:
    while True:
        await asyncio.sleep(30)
        await websocket.send(json.dumps({"type": "ping"}, ensure_ascii=False))


def _utc_now_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def send_screenshot_pushes(
    websocket: websockets.ClientConnection,
    *,
    idle: bool,
    interval_seconds: int,
) -> None:
    while True:
        await asyncio.sleep(max(1, int(interval_seconds)))
        payload = {
            "type": "screenshot_push",
            "timestamp": _utc_now_text(),
            "idle": bool(idle),
            "image_base64": "" if idle else JPEG_BASE64,
        }
        await websocket.send(json.dumps(payload, ensure_ascii=False))
        print(json.dumps(payload, ensure_ascii=False))


async def _handle_request(
    websocket: websockets.ClientConnection,
    payload: dict,
    *,
    idle: bool,
) -> None:
    request_id = str(payload.get("request_id") or "").strip()
    action = str(payload.get("action") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    if not request_id:
        return
    if action == "screenshot":
        data = {
            "timestamp": _utc_now_text(),
            "idle": bool(idle),
            "image_base64": "" if idle else JPEG_BASE64,
            "width": 100,
            "height": 100,
        }
        response = {
            "type": "response",
            "request_id": request_id,
            "success": True,
            "data": data,
        }
    elif action == "process_list":
        top_n = max(1, int(params.get("top_n", 20)))
        items = [
            {"pid": 1234, "name": "chrome.exe", "cpu_percent": 15.2, "ram_mb": 512},
            {"pid": 5678, "name": "code.exe", "cpu_percent": 8.1, "ram_mb": 256},
            {"pid": 9012, "name": "wechat.exe", "cpu_percent": 2.3, "ram_mb": 128},
        ][:top_n]
        response = {
            "type": "response",
            "request_id": request_id,
            "success": True,
            "data": {"items": items},
        }
    else:
        response = {
            "type": "response",
            "request_id": request_id,
            "success": False,
            "error": f"Unknown action: {action}",
        }
    await websocket.send(json.dumps(response, ensure_ascii=False))
    print(json.dumps(response, ensure_ascii=False))


async def receive_messages(
    websocket: websockets.ClientConnection,
    *,
    idle: bool,
) -> None:
    async for message in websocket:
        print(message)
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and str(payload.get("type") or "").strip().lower() == "request":
            await _handle_request(websocket, payload, idle=idle)


async def run_client(args: argparse.Namespace) -> None:
    capabilities = args.capabilities if args.capabilities is not None else ["screenshot", "process_list"]
    hello_payload = {
        "type": "hello",
        "token": args.token,
        "device_id": args.device_id,
        "platform": args.platform,
        "version": args.version,
        "capabilities": capabilities,
    }
    async with websockets.connect(args.url) as websocket:
        await websocket.send(json.dumps(hello_payload, ensure_ascii=False))
        heartbeat_task = asyncio.create_task(send_heartbeats(websocket))
        screenshot_task = asyncio.create_task(
            send_screenshot_pushes(
                websocket,
                idle=bool(args.idle),
                interval_seconds=args.push_interval_seconds,
            )
        )
        receive_task = asyncio.create_task(receive_messages(websocket, idle=bool(args.idle)))
        try:
            await receive_task
        finally:
            heartbeat_task.cancel()
            screenshot_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            with contextlib.suppress(asyncio.CancelledError):
                await screenshot_task


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(run_client(args))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
