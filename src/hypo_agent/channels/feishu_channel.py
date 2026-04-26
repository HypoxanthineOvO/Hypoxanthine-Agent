from __future__ import annotations

import asyncio
import base64
import io
import inspect
import json
from pathlib import Path
import threading
from typing import Any, Callable
from urllib import request as urllib_request
from urllib.parse import urlparse

import structlog

from hypo_agent.core.channel_progress import summarize_channel_progress_event
from hypo_agent.core.delivery import DeliveryResult, combine_delivery_results
from hypo_agent.core.feishu_adapter import FeishuAdapter
from hypo_agent.core.rich_response import RichResponse
from hypo_agent.core.time_utils import utc_isoformat, utc_now
from hypo_agent.core.unified_message import UnifiedMessage, message_from_unified
from hypo_agent.exceptions import ChannelError
from hypo_agent.models import Attachment, Message

logger = structlog.get_logger("hypo_agent.channels.feishu")
_FEISHU_RUNTIME_ERRORS = (OSError, RuntimeError, TypeError, ValueError)

try:  # pragma: no cover - optional dependency during tests
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateFileRequest,
        CreateFileRequestBody,
        CreateImageRequest,
        CreateImageRequestBody,
        CreateMessageRequest,
        CreateMessageRequestBody,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised via lazy startup path
    lark = None
    CreateImageRequest = None
    CreateImageRequestBody = None
    CreateFileRequest = None
    CreateFileRequestBody = None
    CreateMessageRequest = None
    CreateMessageRequestBody = None


class FeishuAPIError(ChannelError):
    def __init__(self, message: str, *, code: str | int | None = None) -> None:
        super().__init__(message, operation="feishu_api", code=str(code or "").strip() or None)
        self.code = str(code or "").strip() or None


class _LarkMessageClient:
    def __init__(self, *, app_id: str, app_secret: str) -> None:
        if lark is None or CreateMessageRequest is None or CreateImageRequest is None:
            raise RuntimeError("lark-oapi is required for Feishu channel support")
        self.client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )

    def create(self, payload: dict[str, str]) -> None:
        req = (
            CreateMessageRequest.builder()
            .receive_id_type(str(payload.get("receive_id_type") or "chat_id"))
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(str(payload.get("receive_id") or ""))
                .msg_type(str(payload.get("msg_type") or "interactive"))
                .content(str(payload.get("content") or ""))
                .build()
            )
            .build()
        )
        resp = self.client.im.v1.message.create(req)
        if not resp.success():
            raise FeishuAPIError(
                str(getattr(resp, "msg", "") or "feishu create failed"),
                code=getattr(resp, "code", None),
            )

    def upload_image(self, payload: bytes, filename: str = "image.png") -> str:
        if CreateImageRequest is None or CreateImageRequestBody is None:
            raise RuntimeError("lark-oapi image upload support is unavailable")

        image_file = io.BytesIO(payload)
        image_file.name = filename
        req = (
            CreateImageRequest.builder()
            .request_body(
                CreateImageRequestBody.builder()
                .image_type("message")
                .image(image_file)
                .build()
            )
            .build()
        )
        resp = self.client.im.v1.image.create(req)
        if not resp.success():
            raise FeishuAPIError(
                str(getattr(resp, "msg", "") or "feishu image upload failed"),
                code=getattr(resp, "code", None),
            )
        image_key = str(getattr(getattr(resp, "data", None), "image_key", "") or "").strip()
        if not image_key:
            raise FeishuAPIError("feishu image upload missing image_key")
        return image_key

    def upload_file(self, payload: bytes, filename: str, file_type: str = "stream") -> str:
        if CreateFileRequest is None or CreateFileRequestBody is None:
            raise RuntimeError("lark-oapi file upload support is unavailable")

        file_obj = io.BytesIO(payload)
        file_obj.name = filename
        req = (
            CreateFileRequest.builder()
            .request_body(
                CreateFileRequestBody.builder()
                .file_type(file_type)
                .file_name(filename)
                .file(file_obj)
                .build()
            )
            .build()
        )
        resp = self.client.im.v1.file.create(req)
        if not resp.success():
            raise FeishuAPIError(
                str(getattr(resp, "msg", "") or "feishu file upload failed"),
                code=getattr(resp, "code", None),
            )
        file_key = str(getattr(getattr(resp, "data", None), "file_key", "") or "").strip()
        if not file_key:
            raise FeishuAPIError("feishu file upload missing file_key")
        return file_key


class FeishuChannel:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        message_queue: Any,
        build_message: Callable[..., Message] = Message,
        inbound_callback_getter: Callable[[], Any | None] | None = None,
        api_client: Any | None = None,
        ws_client_factory: Callable[[], Any] | None = None,
        reconnect_delay_seconds: float = 3.0,
        max_reconnect_retries: int | None = 10,
    ) -> None:
        self.app_id = str(app_id or "").strip()
        self.app_secret = str(app_secret or "").strip()
        self.queue = message_queue
        self.build_message = build_message
        self._get_inbound_callback = inbound_callback_getter or (lambda: None)
        self._api_client = api_client
        self._ws_client_factory = ws_client_factory
        self._reconnect_delay_seconds = max(0.5, float(reconnect_delay_seconds))
        self._max_reconnect_retries = (
            None if max_reconnect_retries is None else max(1, int(max_reconnect_retries))
        )
        self._adapter = FeishuAdapter()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ws_client: Any | None = None
        self._session_to_chat: dict[str, str] = {}
        self._chat_to_session: dict[str, str] = {}
        self._messages_received = 0
        self._messages_sent = 0
        self._last_message_at: str | None = None

    async def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._loop = asyncio.get_running_loop()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._ws_worker,
            name="feishu-ws",
            daemon=True,
        )
        self._thread.start()

    async def stop(self) -> None:
        self._stop_event.set()
        client = self._ws_client
        if client is not None:
            stopper = getattr(client, "stop", None)
            if callable(stopper):
                try:
                    await asyncio.to_thread(stopper)
                except _FEISHU_RUNTIME_ERRORS:
                    logger.warning("feishu.channel.stop_failed", exc_info=True)
        thread = self._thread
        self._thread = None
        if thread is not None:
            await asyncio.to_thread(thread.join, 5.0)
        logger.info("feishu.channel.stopped")

    async def send(self, chat_id: str, rich_response: RichResponse) -> DeliveryResult:
        if not str(chat_id or "").strip():
            return DeliveryResult.failed("feishu", error="missing_chat_id")
        if not str(rich_response.text or "").strip() and not rich_response.attachments:
            return DeliveryResult.ok("feishu", segment_count=0)
        payloads = await self._build_delivery_payloads(
            text=str(rich_response.text or ""),
            attachments=list(rich_response.attachments),
        )
        results: list[DeliveryResult] = []
        for payload in payloads:
            delivery_payload = {
                "receive_id_type": "chat_id",
                "receive_id": str(chat_id or "").strip(),
                **payload,
            }
            results.append(await self._send_create_payload(delivery_payload))
        return combine_delivery_results("feishu", results)

    async def push_proactive(self, message: Message | UnifiedMessage) -> DeliveryResult:
        outbound = message_from_unified(message) if isinstance(message, UnifiedMessage) else message
        if (
            outbound.message_tag == "tool_status"
            and bool(outbound.metadata.get("ephemeral"))
        ):
            return DeliveryResult.ok("feishu", segment_count=0)

        chat_id = self._resolve_chat_id_for_message(outbound)
        if not chat_id:
            logger.warning(
                "feishu.message.failed",
                error_code="missing_chat_id",
                session_id=outbound.session_id,
            )
            return DeliveryResult.failed("feishu", error="missing_chat_id")

        payloads = await self._build_delivery_payloads(
            text=str(outbound.text or ""),
            attachments=list(outbound.attachments),
        )
        results: list[DeliveryResult] = []
        for payload in payloads:
            delivery_payload = {
                "receive_id_type": "chat_id",
                "receive_id": chat_id,
                **payload,
            }
            results.append(await self._send_create_payload(delivery_payload))
        return combine_delivery_results("feishu", results)

    def bind_chat_session(self, *, chat_id: str, session_id: str | None = None) -> str:
        normalized_chat_id = str(chat_id or "").strip()
        if not normalized_chat_id:
            raise ValueError("chat_id is required")
        resolved_session_id = str(session_id or self.resolve_session_id(normalized_chat_id)).strip()
        self._chat_to_session[normalized_chat_id] = resolved_session_id
        self._session_to_chat[resolved_session_id] = normalized_chat_id
        return resolved_session_id

    def resolve_session_id(self, chat_id: str) -> str:
        normalized_chat_id = str(chat_id or "").strip()
        if not normalized_chat_id:
            raise ValueError("chat_id is required")
        # Hypo-Agent is single-user and channels are mutually synced.
        # Keep the same session_id across channels (aligned with QQ/Weixin defaults).
        return "main"

    def resolve_chat_id(self, session_id: str) -> str | None:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return None
        if normalized_session_id in self._session_to_chat:
            return self._session_to_chat[normalized_session_id]
        return None

    def get_status(self) -> dict[str, Any]:
        status = "disabled"
        if self.app_id and self.app_secret:
            thread = self._thread
            status = "connected" if thread is not None and thread.is_alive() else "enabled"
        return {
            "status": status,
            "app_id": f"••••{self.app_id[-4:]}" if len(self.app_id) >= 4 else ("••••" if self.app_id else ""),
            "chat_count": len(self._chat_to_session),
            "last_message_at": self._last_message_at,
            "messages_received": self._messages_received,
            "messages_sent": self._messages_sent,
        }

    async def _handle_message_receive(self, data: Any) -> None:
        message = getattr(getattr(data, "event", None), "message", None)
        if message is None:
            return

        chat_id = str(getattr(message, "chat_id", "") or "").strip()
        message_type = str(getattr(message, "message_type", "") or "").strip().lower()
        session_id = self.bind_chat_session(chat_id=chat_id)
        sender_id = self._extract_sender_id(data)
        self._messages_received += 1
        self._last_message_at = utc_isoformat(utc_now())

        logger.info(
            "feishu.message.received",
            chat_id=chat_id,
            message_type=message_type,
            session_id=session_id,
        )

        if message_type != "text":
            await self.send(chat_id, RichResponse(text="暂不支持该消息类型"))
            return

        text = self._extract_text_content(getattr(message, "content", ""))
        inbound = self.build_message(
            text=text or None,
            sender="user",
            session_id=session_id,
            channel="feishu",
            sender_id=sender_id,
            metadata={
                "feishu": {
                    "chat_id": chat_id,
                    "message_type": message_type,
                }
            },
        )

        callback = self._get_inbound_callback()
        if callable(callback):
            try:
                result = callback(inbound, message_type="user_message")
            except TypeError:
                result = callback(inbound)
            if inspect.isawaitable(result):
                await result

        await self.queue.put(
            {
                "event_type": "user_message",
                "message": inbound,
                "emit": self._make_emit_callback(chat_id),
            }
        )

    def _make_emit_callback(self, chat_id: str):
        prelude_sent = False

        async def emit(event: dict[str, Any]) -> None:
            nonlocal prelude_sent
            event_type = str(event.get("type") or "").strip().lower()
            text, prelude_sent = summarize_channel_progress_event(event, prelude_sent=prelude_sent)
            if text:
                await self.send(chat_id, RichResponse(text=text))
                return
            if event_type == "error":
                await self.send(
                    chat_id,
                    RichResponse(text=str(event.get("message") or "处理失败，请稍后重试")),
                )

        return emit

    def _extract_sender_id(self, data: Any) -> str | None:
        sender = getattr(getattr(data, "event", None), "sender", None)
        sender_id = getattr(sender, "sender_id", None)
        for field_name in ("open_id", "user_id", "union_id"):
            value = str(getattr(sender_id, field_name, "") or "").strip()
            if value:
                return value
        return None

    def _extract_text_content(self, raw_content: Any) -> str:
        if isinstance(raw_content, dict):
            return str(raw_content.get("text") or "").strip()
        raw_text = str(raw_content or "").strip()
        if not raw_text:
            return ""
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            return raw_text
        if isinstance(payload, dict):
            return str(payload.get("text") or "").strip()
        return raw_text

    def _resolve_chat_id_for_message(self, message: Message) -> str | None:
        feishu_meta = message.metadata.get("feishu")
        if isinstance(feishu_meta, dict):
            chat_id = str(feishu_meta.get("chat_id") or "").strip()
            if chat_id:
                return chat_id
        return self.resolve_chat_id(message.session_id)

    async def _build_delivery_payloads(
        self,
        *,
        text: str,
        attachments: list[Any],
    ) -> list[dict[str, str]]:
        payloads: list[dict[str, str]] = []
        normalized_text = str(text or "").strip()
        if normalized_text:
            payloads.extend(await self._adapter.format(RichResponse(text=normalized_text)))

        for raw_attachment in attachments:
            attachment = self._normalize_attachment(raw_attachment)
            if attachment is None:
                continue
            if attachment.type != "image":
                payloads.append(await self._build_file_payload(attachment))
                continue
            payloads.append(await self._build_image_payload(attachment))

        return payloads

    def _normalize_attachment(self, raw_attachment: Any) -> Attachment | None:
        if isinstance(raw_attachment, Attachment):
            return raw_attachment
        if isinstance(raw_attachment, dict):
            try:
                return Attachment.model_validate(raw_attachment)
            except Exception:
                logger.warning("feishu.attachment.invalid", exc_info=True)
                return None
        return None

    async def _build_image_payload(self, attachment: Attachment) -> dict[str, str]:
        try:
            payload = self._load_attachment_bytes(str(getattr(attachment, "url", "") or "").strip())
            image_key = await self._upload_image_bytes(
                payload,
                filename=self._attachment_filename(attachment),
            )
        except FeishuAPIError:
            raise
        except _FEISHU_RUNTIME_ERRORS as exc:
            logger.warning(
                "feishu.image.upload_failed",
                attachment_url=str(getattr(attachment, "url", "") or ""),
                error=str(exc),
            )
            fallback = self._attachment_fallback_text(attachment)
            cards = await self._adapter.format(RichResponse(text=fallback))
            return cards[0]

        return {
            "msg_type": "image",
            "content": json.dumps({"image_key": image_key}, ensure_ascii=False, separators=(",", ":")),
        }

    async def _build_file_payload(self, attachment: Attachment) -> dict[str, str]:
        try:
            payload = self._load_attachment_bytes(str(getattr(attachment, "url", "") or "").strip())
            file_key = await self._upload_file_bytes(
                payload,
                filename=self._attachment_filename(attachment),
                file_type=self._feishu_file_type(attachment),
            )
        except FeishuAPIError:
            raise
        except _FEISHU_RUNTIME_ERRORS as exc:
            logger.warning(
                "feishu.file.upload_failed",
                attachment_url=str(getattr(attachment, "url", "") or ""),
                error=str(exc),
            )
            label = self._attachment_filename(attachment)
            cards = await self._adapter.format(RichResponse(text=f"[文件] {label}"))
            return cards[0]

        return {
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key}, ensure_ascii=False, separators=(",", ":")),
        }

    async def _upload_image_bytes(self, payload: bytes, *, filename: str) -> str:
        uploader = self._ensure_api_client().upload_image
        try:
            return await asyncio.to_thread(uploader, payload, filename)
        except TypeError as exc:
            message = str(exc)
            if "positional argument" not in message and "keyword argument" not in message:
                raise
            return await asyncio.to_thread(uploader, payload)

    async def _upload_file_bytes(self, payload: bytes, *, filename: str, file_type: str) -> str:
        uploader = getattr(self._ensure_api_client(), "upload_file", None)
        if not callable(uploader):
            raise RuntimeError("feishu file upload support is unavailable")
        try:
            return await asyncio.to_thread(uploader, payload, filename, file_type)
        except TypeError as exc:
            message = str(exc)
            if "positional argument" not in message and "keyword argument" not in message:
                raise
            try:
                return await asyncio.to_thread(uploader, payload, filename)
            except TypeError as fallback_exc:
                fallback_message = str(fallback_exc)
                if "positional argument" not in fallback_message and "keyword argument" not in fallback_message:
                    raise
                return await asyncio.to_thread(uploader, payload)

    def _feishu_file_type(self, attachment: Attachment) -> str:
        if attachment.type == "audio":
            return "audio"
        if attachment.type == "video":
            return "mp4"
        return "stream"

    def _attachment_filename(self, attachment: Attachment) -> str:
        filename = str(attachment.filename or "").strip()
        if filename:
            return filename
        raw_url = str(attachment.url or "").strip()
        if raw_url.startswith("file://"):
            return Path(urlparse(raw_url).path).name or "image.png"
        if raw_url.startswith(("http://", "https://")):
            return Path(urlparse(raw_url).path).name or "image.png"
        return Path(raw_url).name or "image.png"

    def _attachment_fallback_text(self, attachment: Attachment) -> str:
        label = self._attachment_filename(attachment)
        return f"[图片] {label}" if label else "[图片]"

    def _load_attachment_bytes(self, image_ref: str) -> bytes:
        raw_ref = str(image_ref or "").strip()
        if not raw_ref:
            raise ValueError("missing image attachment url")
        if raw_ref.startswith("data:image/"):
            _, _, payload = raw_ref.partition(",")
            return base64.b64decode(payload.encode("utf-8"), validate=False)
        if raw_ref.startswith(("http://", "https://")):
            request = urllib_request.Request(url=raw_ref, method="GET")
            with urllib_request.urlopen(request, timeout=10.0) as response:
                return response.read()
        if raw_ref.startswith("file://"):
            return Path(urlparse(raw_ref).path).expanduser().resolve(strict=False).read_bytes()
        return Path(raw_ref).expanduser().resolve(strict=False).read_bytes()

    async def _send_create_payload(self, payload: dict[str, str]) -> DeliveryResult:
        try:
            await asyncio.to_thread(self._ensure_api_client().create, payload)
        except FeishuAPIError as exc:
            logger.warning(
                "feishu.message.failed",
                error_code=exc.code,
                receive_id=payload.get("receive_id"),
                exc_info=True,
            )
            return DeliveryResult.failed("feishu", segment_count=1, error=str(exc))
        except _FEISHU_RUNTIME_ERRORS as exc:
            logger.warning(
                "feishu.message.failed",
                error_code="exception",
                receive_id=payload.get("receive_id"),
                exc_info=True,
            )
            return DeliveryResult.failed("feishu", segment_count=1, error=str(exc))

        self._messages_sent += 1
        self._last_message_at = utc_isoformat(utc_now())
        logger.info("feishu.message.sent", receive_id=payload.get("receive_id"))
        return DeliveryResult.ok("feishu", segment_count=1)

    def _ensure_api_client(self) -> Any:
        if self._api_client is None:
            self._api_client = _LarkMessageClient(
                app_id=self.app_id,
                app_secret=self.app_secret,
            )
        return self._api_client

    def _build_ws_client(self) -> Any:
        if lark is None:
            raise RuntimeError("lark-oapi is required for Feishu channel support")

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_message_receive_sync)
            .build()
        )
        return lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
        )

    def _handle_message_receive_sync(self, data: Any) -> None:
        loop = self._loop
        if loop is None:
            logger.warning("feishu.message.failed", error_code="missing_loop")
            return
        future = asyncio.run_coroutine_threadsafe(self._handle_message_receive(data), loop)
        future.add_done_callback(self._log_future_error)

    def _log_future_error(self, future) -> None:
        if future.cancelled():
            return
        exc = future.exception()
        if exc is not None:
            logger.warning(
                "feishu.message.failed",
                error_code="handler_exception",
                error=str(exc),
                exc_info=True,
            )

    def _ws_worker(self) -> None:
        attempt = 0
        while not self._stop_event.is_set():
            try:
                self._ws_client = (
                    self._ws_client_factory() if self._ws_client_factory is not None else self._build_ws_client()
                )
                logger.info(
                    "feishu.channel.started",
                    app_id_suffix=self.app_id[-4:],
                    attempt=attempt + 1,
                )
                self._ws_client.start()
                if self._stop_event.is_set():
                    break
                attempt += 1
                logger.warning(
                    "feishu.channel.reconnecting",
                    reason="ws_client_exited",
                    attempt=attempt,
                )
            except _FEISHU_RUNTIME_ERRORS as exc:
                if self._stop_event.is_set():
                    break
                attempt += 1
                logger.warning(
                    "feishu.channel.reconnecting",
                    error=str(exc),
                    attempt=attempt,
                )

            if self._max_reconnect_retries is not None and attempt >= self._max_reconnect_retries:
                logger.error(
                    "feishu.channel.reconnect_exhausted",
                    retries=attempt,
                )
                break

            delay = min(30.0, self._reconnect_delay_seconds * (2 ** max(attempt - 1, 0)))
            if self._stop_event.wait(delay):
                break
