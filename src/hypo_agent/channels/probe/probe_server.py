from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError
import structlog

from hypo_agent.core.time_utils import normalize_utc_datetime, utc_isoformat, utc_now
from hypo_agent.models import ProbeConfig

logger = structlog.get_logger("hypo_agent.channels.probe")
router = APIRouter()


class ProbeRPCError(RuntimeError):
    pass


class ProbeHelloMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    token: str
    device_id: str
    platform: str
    version: str
    capabilities: list[str] = Field(default_factory=list)


class ProbeResponseMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    request_id: str
    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class ProbeScreenshotPushMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    timestamp: str
    idle: bool = False
    image_base64: str = ""


@dataclass(slots=True)
class DeviceInfo:
    device_id: str
    platform: str
    version: str
    capabilities: list[str]
    websocket: WebSocket | None
    connected_at: datetime
    last_seen: datetime
    last_screenshot_at: datetime | None = None
    idle: bool = False
    online: bool = True


class ProbeServer:
    def __init__(
        self,
        *,
        token: str | None = None,
        hello_timeout_seconds: float = 5.0,
        stale_check_interval_seconds: float = 60.0,
        offline_timeout_seconds: float = 90.0,
        now_fn: Any = utc_now,
    ) -> None:
        self.devices: dict[str, DeviceInfo] = {}
        self._token = str(token).strip() or None
        self._screenshot_dir = Path("memory/probe_screenshots").expanduser().resolve(strict=False)
        self._hello_timeout_seconds = max(0.01, float(hello_timeout_seconds))
        self._stale_check_interval_seconds = max(0.01, float(stale_check_interval_seconds))
        self._offline_timeout_seconds = max(1.0, float(offline_timeout_seconds))
        self._now_fn = now_fn
        self._lock = asyncio.Lock()
        self._watchdog_task: asyncio.Task[None] | None = None
        self._pending_offline_alerts: set[str] = set()
        self._pending_rpcs: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._rpc_device_ids: dict[str, str] = {}
        self._warned_missing_config = False

    def has_token(self) -> bool:
        return self._token is not None

    def configure(self, config: ProbeConfig | None) -> None:
        if config is None or not str(config.token).strip():
            self._token = None
            self._screenshot_dir = Path("memory/probe_screenshots").expanduser().resolve(strict=False)
            if not self._warned_missing_config:
                logger.warning(
                    "probe.config.missing",
                    message="services.probe is missing or token is empty; rejecting all probe connections",
                )
                self._warned_missing_config = True
            return
        self._token = str(config.token).strip()
        self._screenshot_dir = Path(
            str(config.screenshot_dir).strip() or "memory/probe_screenshots"
        ).expanduser().resolve(strict=False)
        self._warned_missing_config = False

    async def start(self) -> None:
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def stop(self) -> None:
        task = self._watchdog_task
        self._watchdog_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        sockets: list[tuple[str, WebSocket]] = []
        async with self._lock:
            for device_id, info in self.devices.items():
                if info.websocket is not None and info.online:
                    sockets.append((device_id, info.websocket))
                    info.online = False
                    info.websocket = None
                info.idle = False
            pending = list(self._pending_rpcs.items())
            self._pending_rpcs.clear()
            self._rpc_device_ids.clear()
        for _, future in pending:
            if not future.done():
                future.set_exception(ProbeRPCError("device offline"))
        for device_id, websocket in sockets:
            try:
                await websocket.close(code=1012, reason="server shutdown")
            except Exception:
                logger.debug("probe.websocket.close_failed", device_id=device_id, exc_info=True)

    async def handle_connection(self, websocket: WebSocket) -> None:
        await websocket.accept()
        device_id = ""
        try:
            hello_payload = await self._receive_hello(websocket)
            device_id = hello_payload.device_id
            record = await self._register_device(websocket, hello_payload)
            await websocket.send_json(
                {
                    "type": "welcome",
                    "device_id": record.device_id,
                    "server_time": utc_isoformat(self._now()),
                }
            )
            while True:
                payload = await websocket.receive_json()
                await self._touch_device(device_id=device_id, websocket=websocket)
                if isinstance(payload, dict):
                    await self._handle_client_message(
                        device_id=device_id,
                        websocket=websocket,
                        payload=payload,
                    )
        except WebSocketDisconnect:
            return
        except asyncio.TimeoutError:
            await self._close_socket(websocket, code=4408, reason="hello timeout")
        except (ValidationError, ValueError):
            await self._close_socket(websocket, code=4400, reason="invalid hello")
        except Exception:
            logger.exception("probe.websocket.failed", device_id=device_id or None)
            await self._close_socket(websocket, code=1011, reason="internal error")
        finally:
            if device_id:
                await self._mark_disconnected(device_id=device_id, websocket=websocket)

    def list_devices(self) -> list[dict[str, Any]]:
        items = [
            {
                "device_id": info.device_id,
                "platform": info.platform,
                "online": info.online,
                "capabilities": list(info.capabilities),
                "connected_at": utc_isoformat(info.connected_at),
                "last_seen": utc_isoformat(info.last_seen),
            }
            for _, info in sorted(self.devices.items(), key=lambda item: item[0])
        ]
        return items

    async def send_rpc(
        self,
        device_id: str,
        action: str,
        params: dict | None = None,
        timeout: float = 30.0,
    ) -> dict:
        request_id = uuid4().hex
        payload = {
            "type": "request",
            "request_id": request_id,
            "action": str(action or "").strip(),
            "params": dict(params or {}),
        }
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        async with self._lock:
            info = self.devices.get(device_id)
            if info is None or not info.online or info.websocket is None:
                raise ProbeRPCError("device offline")
            websocket = info.websocket
            self._pending_rpcs[request_id] = future
            self._rpc_device_ids[request_id] = device_id
        try:
            await websocket.send_json(payload)
            response = await asyncio.wait_for(future, timeout=max(0.01, float(timeout)))
        except asyncio.TimeoutError as exc:
            raise ProbeRPCError("timeout") from exc
        except Exception:
            if not future.done():
                future.cancel()
            raise
        finally:
            async with self._lock:
                self._pending_rpcs.pop(request_id, None)
                self._rpc_device_ids.pop(request_id, None)
        success = bool(response.get("success"))
        if not success:
            raise ProbeRPCError(str(response.get("error") or "rpc failed").strip() or "rpc failed")
        data = response.get("data")
        return dict(data) if isinstance(data, dict) else {}

    def list_screenshots(self, device_id: str, *, date: str | None = None) -> list[dict[str, Any]]:
        target_date = str(date or self._now().date().isoformat()).strip()
        index_path = self._index_path(device_id=device_id, date_text=target_date)
        if not index_path.exists():
            return []
        items: list[dict[str, Any]] = []
        for line in index_path.read_text(encoding="utf-8").splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            payload = json.loads(cleaned)
            if isinstance(payload, dict):
                items.append(payload)
        return items

    def store_screenshot(
        self,
        *,
        device_id: str,
        timestamp: str | datetime,
        idle: bool,
        image_base64: str = "",
    ) -> dict[str, Any]:
        ts_dt = self._parse_timestamp(timestamp)
        day_dir = self._day_dir(device_id=device_id, timestamp=ts_dt)
        day_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{ts_dt.strftime('%H-%M-%S')}.jpg"
        image_path: Path | None = None
        if not idle:
            if not str(image_base64 or "").strip():
                raise ValueError("image_base64 is required when idle is false")
            try:
                payload = base64.b64decode(image_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("invalid image_base64") from exc
            image_path = day_dir / filename
            image_path.write_bytes(payload)
        self._append_index_record(
            device_id=device_id,
            timestamp=ts_dt,
            idle=idle,
            relative_path=filename if image_path is not None else None,
        )
        info = self.devices.get(device_id)
        if info is not None:
            info.last_screenshot_at = ts_dt
            info.idle = bool(idle)
        return {
            "timestamp": utc_isoformat(ts_dt),
            "idle": bool(idle),
            "path": str(image_path.resolve(strict=False)) if image_path is not None else None,
        }

    async def probe_heartbeat_callback(self) -> str | None:
        await self.check_timeouts()
        async with self._lock:
            if not self._pending_offline_alerts:
                return None
            device_ids = sorted(self._pending_offline_alerts)
            self._pending_offline_alerts.clear()
        return "；".join(f"设备 {device_id} 已离线" for device_id in device_ids)

    async def check_timeouts(self) -> None:
        now = self._now()
        stale_sockets: list[tuple[str, WebSocket]] = []
        async with self._lock:
            for device_id, info in self.devices.items():
                if not info.online:
                    continue
                if now - info.last_seen <= timedelta(seconds=self._offline_timeout_seconds):
                    continue
                if self._transition_offline_locked(device_id=device_id, info=info):
                    websocket = info.websocket
                    if websocket is not None:
                        stale_sockets.append((device_id, websocket))
        for device_id, websocket in stale_sockets:
            await self._close_socket(websocket, code=4408, reason="stale connection")
            logger.info("probe.device.marked_offline", device_id=device_id, reason="stale")

    async def _receive_hello(self, websocket: WebSocket) -> ProbeHelloMessage:
        payload = await asyncio.wait_for(
            websocket.receive_json(),
            timeout=self._hello_timeout_seconds,
        )
        hello = ProbeHelloMessage.model_validate(payload)
        if str(hello.type).strip().lower() != "hello":
            raise ValueError("invalid hello type")
        if self._token is None or hello.token != self._token:
            await self._close_socket(websocket, code=4401, reason="unauthorized")
            raise WebSocketDisconnect(code=4401)
        return hello

    async def _register_device(
        self,
        websocket: WebSocket,
        hello: ProbeHelloMessage,
    ) -> DeviceInfo:
        now = self._now()
        previous_socket: WebSocket | None = None
        async with self._lock:
            existing = self.devices.get(hello.device_id)
            if existing is not None and existing.websocket is not websocket:
                previous_socket = existing.websocket
            info = DeviceInfo(
                device_id=hello.device_id,
                platform=str(hello.platform).strip().lower(),
                version=str(hello.version).strip(),
                capabilities=[str(item) for item in hello.capabilities],
                websocket=websocket,
                connected_at=now,
                last_seen=now,
                last_screenshot_at=getattr(existing, "last_screenshot_at", None) if existing else None,
                idle=getattr(existing, "idle", False) if existing else False,
                online=True,
            )
            self.devices[hello.device_id] = info
            self._pending_offline_alerts.discard(hello.device_id)
        if previous_socket is not None:
            await self._close_socket(previous_socket, code=4409, reason="replaced by newer connection")
        logger.info(
            "probe.device.connected",
            device_id=hello.device_id,
            platform=hello.platform,
            version=hello.version,
        )
        return info

    async def _touch_device(self, *, device_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            info = self.devices.get(device_id)
            if info is None or info.websocket is not websocket:
                return
            info.last_seen = self._now()
            info.online = True

    async def _mark_disconnected(self, *, device_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            info = self.devices.get(device_id)
            if info is None or info.websocket is not websocket:
                return
            changed = self._transition_offline_locked(device_id=device_id, info=info)
            request_ids = [
                request_id
                for request_id, owner_device_id in self._rpc_device_ids.items()
                if owner_device_id == device_id
            ]
            pending = [
                self._pending_rpcs.pop(request_id)
                for request_id in request_ids
                if request_id in self._pending_rpcs
            ]
            for request_id in request_ids:
                self._rpc_device_ids.pop(request_id, None)
        for future in pending:
            if not future.done():
                future.set_exception(ProbeRPCError("device offline"))
        if changed:
            logger.info("probe.device.disconnected", device_id=device_id)

    def _transition_offline_locked(self, *, device_id: str, info: DeviceInfo) -> bool:
        if not info.online:
            info.websocket = None
            return False
        info.online = False
        info.websocket = None
        self._pending_offline_alerts.add(device_id)
        return True

    async def _watchdog_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._stale_check_interval_seconds)
                await self.check_timeouts()
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise

    async def _close_socket(self, websocket: WebSocket, *, code: int, reason: str) -> None:
        try:
            await websocket.close(code=code, reason=reason)
        except RuntimeError:
            return
        except Exception:
            logger.debug("probe.websocket.close_failed", code=code, reason=reason, exc_info=True)

    def _now(self) -> datetime:
        return normalize_utc_datetime(self._now_fn()) or utc_now()

    async def _handle_client_message(
        self,
        *,
        device_id: str,
        websocket: WebSocket,
        payload: dict[str, Any],
    ) -> None:
        payload_type = str(payload.get("type") or "").strip().lower()
        if payload_type == "ping":
            await websocket.send_json({"type": "pong"})
            return
        if payload_type == "response":
            await self._handle_rpc_response(payload)
            return
        if payload_type == "screenshot_push":
            message = ProbeScreenshotPushMessage.model_validate(payload)
            stored = self.store_screenshot(
                device_id=device_id,
                timestamp=message.timestamp,
                idle=message.idle,
                image_base64=message.image_base64,
            )
            await websocket.send_json(
                {
                    "type": "ack",
                    "stored": True,
                    "path": stored["path"],
                }
            )
            return
        logger.warning("probe.message.ignored", device_id=device_id, payload_type=payload_type)

    async def _handle_rpc_response(self, payload: dict[str, Any]) -> None:
        message = ProbeResponseMessage.model_validate(payload)
        if str(message.type).strip().lower() != "response":
            raise ValueError("invalid response type")
        async with self._lock:
            future = self._pending_rpcs.get(message.request_id)
        if future is None or future.done():
            return
        future.set_result(message.model_dump(mode="python"))

    def _day_dir(self, *, device_id: str, timestamp: datetime) -> Path:
        return self._screenshot_dir / device_id / timestamp.date().isoformat()

    def _index_path(self, *, device_id: str, date_text: str) -> Path:
        return self._screenshot_dir / device_id / date_text / "index.jsonl"

    def _append_index_record(
        self,
        *,
        device_id: str,
        timestamp: datetime,
        idle: bool,
        relative_path: str | None,
    ) -> None:
        index_path = self._index_path(device_id=device_id, date_text=timestamp.date().isoformat())
        index_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": utc_isoformat(timestamp),
            "idle": bool(idle),
            "path": relative_path,
        }
        with index_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _parse_timestamp(self, timestamp: str | datetime) -> datetime:
        if isinstance(timestamp, datetime):
            return normalize_utc_datetime(timestamp) or self._now()
        raw = str(timestamp or "").strip()
        if not raw:
            raise ValueError("timestamp is required")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid timestamp") from exc
        normalized = normalize_utc_datetime(parsed)
        if normalized is None:
            raise ValueError("invalid timestamp")
        return normalized


@router.websocket("/ws/probe")
async def probe_websocket_endpoint(websocket: WebSocket) -> None:
    probe_server = getattr(websocket.app.state, "probe_server", None)
    if probe_server is None or not isinstance(probe_server, ProbeServer):
        await websocket.close(code=1011, reason="probe server unavailable")
        return
    await probe_server.handle_connection(websocket)
