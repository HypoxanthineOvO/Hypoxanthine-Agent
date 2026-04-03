from __future__ import annotations

import asyncio
import json
from typing import Any, Callable
from urllib import parse as urllib_parse

import structlog
import websockets

from hypo_agent.core.time_utils import unix_seconds_to_utc_datetime, utc_isoformat, utc_now

logger = structlog.get_logger("hypo_agent.gateway.qq_ws_client")


class NapCatWebSocketClient:
    def __init__(
        self,
        *,
        url: str,
        bot_qq: str = "",
        token: str = "",
        service_getter: Callable[[], Any | None],
        pipeline_getter: Callable[[], Any | None],
        reconnect_delay_seconds: float = 5.0,
        connect_timeout_seconds: float = 5.0,
        max_reconnect_retries: int | None = 10,
    ) -> None:
        self.url = str(url).strip()
        self.bot_qq = str(bot_qq).strip()
        self.token = str(token).strip()
        self._service_getter = service_getter
        self._pipeline_getter = pipeline_getter
        self.reconnect_delay_seconds = max(0.1, float(reconnect_delay_seconds))
        self.connect_timeout_seconds = max(1.0, float(connect_timeout_seconds))
        self.max_reconnect_retries = (
            None if max_reconnect_retries is None else max(1, int(max_reconnect_retries))
        )
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.status = "disconnected"
        self.connected_at: str | None = None
        self.last_message_at: str | None = None
        self.messages_received = 0
        self.messages_sent = 0

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        self._stop_event.set()
        self.status = "disconnected"
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return

    async def run_once(self) -> None:
        self.status = "connecting"
        logger.info("qq.ws.client.connecting", url=self.url)
        async with websockets.connect(
            self._build_connect_url(),
            open_timeout=self.connect_timeout_seconds,
            close_timeout=1,
        ) as ws:
            self.status = "connected"
            self.connected_at = utc_isoformat(utc_now())
            logger.info("qq.ws.client.connected", url=self.url)
            async for raw in ws:
                payload = self._decode_payload(raw)
                if payload is None:
                    continue
                service = self._service_getter()
                pipeline = self._pipeline_getter()
                if service is None or pipeline is None:
                    logger.warning("qq.ws.client.message.skipped", reason="service_or_pipeline_unavailable")
                    continue

                payload = self._normalize_event_timestamp(payload)
                handled = await service.handle_onebot_event(payload, pipeline=pipeline)
                if handled:
                    self.messages_received += 1
                    self.last_message_at = str(payload.get("timestamp") or utc_isoformat(utc_now()))

    def _build_connect_url(self) -> str:
        if not self.token:
            return self.url

        parsed = urllib_parse.urlsplit(self.url)
        pairs = urllib_parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not any(key == "access_token" for key, _ in pairs):
            pairs.append(("access_token", self.token))
        query = urllib_parse.urlencode(pairs)
        return urllib_parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment)
        )

    async def _run_forever(self) -> None:
        retry_count = 0
        while not self._stop_event.is_set():
            try:
                await self.run_once()
                retry_count = 0
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self.status = "disconnected"
                logger.warning("qq.ws.client.disconnected", url=self.url, error=str(exc))

            if self._stop_event.is_set():
                break

            if self.max_reconnect_retries is not None and retry_count >= self.max_reconnect_retries:
                logger.error(
                    "qq.ws.client.reconnect_exhausted",
                    url=self.url,
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

    def _decode_payload(self, raw: Any) -> dict[str, Any] | None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not isinstance(raw, str):
            logger.warning("qq.ws.client.payload.invalid_type", payload_type=type(raw).__name__)
            return None

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("qq.ws.client.payload.invalid_json")
            return None

        if not isinstance(parsed, dict):
            logger.warning("qq.ws.client.payload.invalid_shape", payload_type=type(parsed).__name__)
            return None
        return parsed

    def _normalize_event_timestamp(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("timestamp"):
            return payload
        normalized = unix_seconds_to_utc_datetime(payload.get("time")) or utc_now()
        return {
            **payload,
            "timestamp": utc_isoformat(normalized),
        }

    def record_message_sent(self) -> None:
        self.messages_sent += 1
        self.last_message_at = utc_isoformat(utc_now())

    def get_status(self) -> dict[str, object]:
        return {
            "status": self.status,
            "bot_qq": self.bot_qq,
            "napcat_ws_url": self.url,
            "connected_at": self.connected_at,
            "last_message_at": self.last_message_at,
            "messages_received": self.messages_received,
            "messages_sent": self.messages_sent,
        }
