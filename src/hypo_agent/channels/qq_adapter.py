from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import structlog

from hypo_agent.core.delivery import DeliveryResult
from hypo_agent.core.qq_renderer import QQRenderer
from hypo_agent.core.rich_response import RichResponse
from hypo_agent.core.unified_message import UnifiedMessage, message_from_unified
from hypo_agent.models import Attachment, Message

logger = structlog.get_logger("hypo_agent.channels.qq.adapter")


class QQAdapter:
    def __init__(
        self,
        *,
        napcat_http_url: str,
        napcat_http_token: str | None = None,
        image_renderer: Any | None = None,
        send_delay_seconds: float = 0.2,
        request_timeout_seconds: float = 10.0,
        message_limit: int = 4500,
    ) -> None:
        self.napcat_http_url = napcat_http_url.rstrip("/")
        self.napcat_http_token = (napcat_http_token or "").strip() or None
        self.image_renderer = image_renderer
        self.send_delay_seconds = max(0.0, send_delay_seconds)
        self.request_timeout_seconds = max(1.0, request_timeout_seconds)
        self.message_limit = max(100, int(message_limit))
        self.renderer = QQRenderer(image_renderer=image_renderer)
        self._last_delivery: DeliveryResult | None = None

    async def format(self, response: RichResponse | Message | UnifiedMessage) -> list[dict[str, Any]]:
        if isinstance(response, UnifiedMessage):
            response = message_from_unified(response)
        if not isinstance(response, Message):
            text = str(response.text or "")
            attachments = [
                attachment.model_copy()
                if isinstance(attachment, Attachment)
                else Attachment.model_validate(attachment)
                for attachment in response.attachments
            ]
            response = Message(text=text, attachments=attachments, sender="assistant", session_id="main")

        rendered_segments = await self.renderer.render(response)
        return [self._transport_segment(segment) for segment in rendered_segments]

    def render_message_text(self, message: Message) -> str:
        return self.renderer.render_message_text(message)

    def downgrade_markdown(self, text: str) -> str:
        return self.renderer.downgrade_markdown(text)

    def split_message(self, text: str, *, limit: int | None = None) -> list[str]:
        effective_limit = self.message_limit if limit is None else max(1, int(limit))
        if len(text) <= effective_limit:
            return [text]

        chunks: list[str] = []
        current = ""
        for line in text.splitlines(keepends=True):
            if len(line) > effective_limit:
                if current:
                    chunks.append(current)
                    current = ""
                for idx in range(0, len(line), effective_limit):
                    chunks.append(line[idx : idx + effective_limit])
                continue

            if len(current) + len(line) <= effective_limit:
                current += line
                continue
            if current:
                chunks.append(current)
            current = line

        if current:
            chunks.append(current)
        return chunks

    async def send_message(self, *, user_id: str, message: Message | UnifiedMessage) -> DeliveryResult:
        segments = await self.format(message)
        if not segments:
            result = DeliveryResult.ok("qq_napcat", segment_count=0)
            self._last_delivery = result
            return result
        return await self.send_private_segments(user_id=user_id, segments=segments)

    async def send_private_segments(self, *, user_id: str, segments: list[dict[str, Any]]) -> DeliveryResult:
        prepared_segments = self._split_text_segments(segments)
        payload: dict[str, Any] = {"message": prepared_segments}
        try:
            payload["user_id"] = int(user_id)
        except (TypeError, ValueError):
            payload["user_id"] = str(user_id)

        result = await asyncio.to_thread(self._post_json, "/send_private_msg", payload)
        delivery = self._delivery_result_from_response(
            result,
            segment_count=len(prepared_segments),
        )
        self._last_delivery = delivery
        return delivery

    async def send_private_text(self, *, user_id: str, text: str) -> DeliveryResult:
        return await self.send_private_segments(
            user_id=user_id,
            segments=[{"type": "text", "data": {"text": text}}],
        )

    def get_status(self) -> dict[str, Any] | None:
        return self._post_json("/get_status", {})

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = self._build_request_url(path)
        headers = {"Content-Type": "application/json"}
        if self.napcat_http_token is not None:
            headers["Authorization"] = f"Bearer {self.napcat_http_token}"
        req = urllib_request.Request(
            url=url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=self.request_timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("qq.adapter.request_failed", path=path, error=str(exc))
            return None
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError:
            logger.warning("qq.adapter.response_invalid_json", path=path)
            return None
        if isinstance(parsed, dict):
            return parsed
        logger.warning(
            "qq.adapter.response_invalid_shape",
            path=path,
            payload_type=type(parsed).__name__,
        )
        return None

    def download_remote_file(self, *, url: str, target_path: str) -> dict[str, Any]:
        request = urllib_request.Request(url=url, method="GET")
        with urllib_request.urlopen(request, timeout=self.request_timeout_seconds) as resp:
            payload = resp.read()
            mime_type = resp.info().get_content_type()

        path = Path(target_path).expanduser().resolve(strict=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return {
            "mime_type": mime_type,
            "size_bytes": len(payload),
        }

    def _build_request_url(self, path: str) -> str:
        base_url = f"{self.napcat_http_url}{path}"
        if self.napcat_http_token is None:
            return base_url

        parsed = urllib_parse.urlsplit(base_url)
        pairs = urllib_parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not any(key == "access_token" for key, _ in pairs):
            pairs.append(("access_token", self.napcat_http_token))
        query = urllib_parse.urlencode(pairs)
        return urllib_parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment)
        )

    def _split_text_segments(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        expanded: list[dict[str, Any]] = []
        for segment in segments:
            if segment.get("type") != "text":
                expanded.append(segment)
                continue
            text = str(segment.get("data", {}).get("text") or "")
            for chunk in self.split_message(text):
                if chunk:
                    expanded.append({"type": "text", "data": {"text": chunk}})
        return expanded

    def _transport_segment(self, segment: dict[str, Any]) -> dict[str, Any]:
        segment_type = str(segment.get("type") or "").strip().lower()
        if segment_type == "text":
            return {"type": "text", "data": {"text": str(segment.get("text") or "")}}
        if segment_type == "image":
            return {
                "type": "image",
                "data": {"file": self._segment_file_value(str(segment.get("source") or ""))},
            }
        if segment_type == "file":
            data: dict[str, Any] = {"file": self._segment_file_value(str(segment.get("source") or ""))}
            name = str(segment.get("name") or "").strip()
            if name:
                data["name"] = name
            return {"type": "file", "data": data}
        return {"type": "text", "data": {"text": str(segment)}}

    def _segment_file_value(self, path_or_url: str) -> str:
        raw = str(path_or_url or "").strip()
        if raw.startswith(("http://", "https://", "file://")):
            return raw
        return Path(raw).expanduser().resolve(strict=False).as_uri()

    def _delivery_result_from_response(
        self,
        response: dict[str, Any] | None,
        *,
        segment_count: int,
    ) -> DeliveryResult:
        if not isinstance(response, dict):
            return DeliveryResult.failed(
                "qq_napcat",
                segment_count=segment_count,
                error="NapCat request failed",
            )

        status = str(response.get("status") or "").strip().lower()
        if status == "ok":
            return DeliveryResult.ok("qq_napcat", segment_count=segment_count)

        error = (
            str(response.get("wording") or "").strip()
            or str(response.get("message") or "").strip()
            or str(response.get("msg") or "").strip()
            or "NapCat returned non-ok status"
        )
        return DeliveryResult.failed(
            "qq_napcat",
            segment_count=segment_count,
            error=error,
        )
