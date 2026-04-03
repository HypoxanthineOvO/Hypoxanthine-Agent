from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx
import structlog
import websockets

from hypo_agent.channels.qq_bot_channel import QQBotChannelService
from hypo_agent.core.time_utils import utc_isoformat, utc_now

logger = structlog.get_logger("hypo_agent.gateway.qqbot_ws_client")

QQBOT_C2C_INTENT = 1 << 25
QQBOT_GROUP_AT_INTENT = 1 << 26
QQBOT_DIRECT_MESSAGE_INTENT = 1 << 12
DEFAULT_QQBOT_INTENTS = QQBOT_C2C_INTENT | QQBOT_GROUP_AT_INTENT | QQBOT_DIRECT_MESSAGE_INTENT
_QQBOT_WS_ERRORS = (
    httpx.HTTPError,
    websockets.WebSocketException,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    json.JSONDecodeError,
)


class QQBotWebSocketClient:
    def __init__(
        self,
        *,
        service_getter: Callable[[], QQBotChannelService | None],
        pipeline_getter: Callable[[], Any | None],
        reconnect_delay_seconds: float = 5.0,
        connect_timeout_seconds: float = 10.0,
        intents: int = DEFAULT_QQBOT_INTENTS,
        max_reconnect_retries: int | None = 10,
    ) -> None:
        self._service_getter = service_getter
        self._pipeline_getter = pipeline_getter
        self.reconnect_delay_seconds = max(0.1, float(reconnect_delay_seconds))
        self.connect_timeout_seconds = max(1.0, float(connect_timeout_seconds))
        self.intents = int(intents)
        self.max_reconnect_retries = (
            None if max_reconnect_retries is None else max(1, int(max_reconnect_retries))
        )

        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

        self.status = "disconnected"
        self.connected_at: str | None = None
        self.session_id: str | None = None
        self.seq: int | None = None
        self.user: dict[str, Any] | None = None
        self.ws_connected = False

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        self._task = None
        await self._cancel_heartbeat()
        self._set_connected(False)
        self.status = "disconnected"
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return

    async def run_once(self) -> None:
        service = self._service_getter()
        pipeline = self._pipeline_getter()
        if service is None or pipeline is None:
            logger.warning("qq_bot.ws_client.skipped", reason="service_or_pipeline_unavailable")
            return

        access_token = await service.get_access_token()
        gateway_url = await service.get_gateway_url(access_token=access_token)
        if not gateway_url:
            raise RuntimeError("qq bot gateway url is empty")

        self.status = "connecting"
        logger.info("qq_bot.ws_client.connecting", gateway_url=gateway_url)

        try:
            async with websockets.connect(
                gateway_url,
                open_timeout=self.connect_timeout_seconds,
                close_timeout=1,
            ) as ws:
                self._set_connected(True)
                async for raw in ws:
                    payload = self._decode_payload(raw)
                    if payload is None:
                        continue
                    self._update_seq(payload.get("s"))
                    should_break = await self._handle_payload(
                        ws=ws,
                        payload=payload,
                        access_token=access_token,
                        service=service,
                        pipeline=pipeline,
                    )
                    if should_break:
                        return
        finally:
            await self._cancel_heartbeat()
            self._set_connected(False)
            if not self._stop_event.is_set():
                self.status = "disconnected"

    async def _run_forever(self) -> None:
        retry_count = 0
        while not self._stop_event.is_set():
            try:
                await self.run_once()
                retry_count = 0
            except asyncio.CancelledError:
                raise
            except _QQBOT_WS_ERRORS as exc:
                self.status = "disconnected"
                logger.warning("qq_bot.ws_client.disconnected", error=str(exc))

            if self._stop_event.is_set():
                break

            if self.max_reconnect_retries is not None and retry_count >= self.max_reconnect_retries:
                logger.error(
                    "qq_bot.ws_client.reconnect_exhausted",
                    retries=retry_count,
                )
                break

            delay = min(30.0, self.reconnect_delay_seconds * (2**retry_count))
            retry_count += 1

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=delay,
                )
            except asyncio.TimeoutError:
                continue

    async def _handle_payload(
        self,
        *,
        ws: Any,
        payload: dict[str, Any],
        access_token: str,
        service: QQBotChannelService,
        pipeline: Any,
    ) -> bool:
        op = int(payload.get("op") or 0)
        event_type = str(payload.get("t") or "").strip()

        if op == 10:
            await self._handle_hello(ws=ws, payload=payload, access_token=access_token)
            return False
        if op == 11:
            return False
        if op == 7:
            logger.info("qq_bot.ws_client.server_reconnect")
            self.status = "connecting"
            return True
        if op == 9:
            can_resume = bool(payload.get("d"))
            logger.warning("qq_bot.ws_client.invalid_session", can_resume=can_resume)
            if not can_resume:
                self.session_id = None
                self.seq = None
            self.status = "connecting"
            return True
        if op != 0:
            return False

        if event_type == "READY":
            data = payload.get("d")
            if isinstance(data, dict):
                self.session_id = str(data.get("session_id") or "").strip() or None
                user = data.get("user")
                self.user = user if isinstance(user, dict) else None
            logger.info("qq_bot.ws_client.ready", session_id=self.session_id)
            return False
        if event_type == "RESUMED":
            logger.info("qq_bot.ws_client.resumed", session_id=self.session_id)
            return False

        await service.handle_event(payload, pipeline=pipeline)
        return False

    async def _handle_hello(self, *, ws: Any, payload: dict[str, Any], access_token: str) -> None:
        data = payload.get("d")
        heartbeat_interval_ms = 0
        if isinstance(data, dict):
            heartbeat_interval_ms = int(data.get("heartbeat_interval") or 0)
        await self._cancel_heartbeat()
        if heartbeat_interval_ms > 0:
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(ws, heartbeat_interval_ms / 1000.0)
            )

        if self.session_id and self.seq is not None:
            await ws.send(
                json.dumps(
                    {
                        "op": 6,
                        "d": {
                            "token": f"QQBot {access_token}",
                            "session_id": self.session_id,
                            "seq": self.seq,
                        },
                    }
                )
            )
            return

        await ws.send(
            json.dumps(
                {
                    "op": 2,
                    "d": {
                        "token": f"QQBot {access_token}",
                        "intents": self.intents,
                        "shard": [0, 1],
                    },
                }
            )
        )

    async def _heartbeat_loop(self, ws: Any, interval_seconds: float) -> None:
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(interval_seconds)
                await ws.send(json.dumps({"op": 1, "d": self.seq}))
        except asyncio.CancelledError:
            raise
        except _QQBOT_WS_ERRORS as exc:
            logger.warning("qq_bot.ws_client.heartbeat_failed", error=str(exc))

    async def _cancel_heartbeat(self) -> None:
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return

    def _set_connected(self, connected: bool) -> None:
        self.ws_connected = bool(connected)
        self.status = "connected" if connected else "disconnected"
        connected_at = utc_now() if connected else None
        self.connected_at = utc_isoformat(connected_at) if connected_at else None
        service = self._service_getter()
        if service is not None:
            service.set_ws_connection_state(connected=connected, connected_at=connected_at)

    def _update_seq(self, value: Any) -> None:
        if value is None:
            return
        try:
            self.seq = int(value)
        except (TypeError, ValueError):
            return

    def _decode_payload(self, raw: Any) -> dict[str, Any] | None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not isinstance(raw, str):
            logger.warning("qq_bot.ws_client.payload.invalid_type", payload_type=type(raw).__name__)
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("qq_bot.ws_client.payload.invalid_json")
            return None
        if not isinstance(parsed, dict):
            logger.warning(
                "qq_bot.ws_client.payload.invalid_shape",
                payload_type=type(parsed).__name__,
            )
            return None
        return parsed

    def get_status(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ws_connected": self.ws_connected,
            "connected_at": self.connected_at,
            "session_id": self.session_id,
            "seq": self.seq,
        }
