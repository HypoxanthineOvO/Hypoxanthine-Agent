from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from hypo_agent.core.markdown_splitter import renderable_markdown_block, split_markdown_blocks
from hypo_agent.core.qq_text_renderer import render_qq_plaintext
from hypo_agent.core.rich_response import RichResponse
from hypo_agent.models import Attachment, Message


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

    async def format(self, response: RichResponse | Message) -> list[dict[str, Any]]:
        if isinstance(response, Message):
            text = str(response.text or "")
            emoji = self._tag_emoji(response.message_tag)
            if emoji and text.strip():
                text = f"{emoji} {text}"
            attachments = [attachment.model_copy() for attachment in response.attachments]
        else:
            text = str(response.text or "")
            attachments = [
                attachment.model_copy()
                if isinstance(attachment, Attachment)
                else Attachment.model_validate(attachment)
                for attachment in response.attachments
            ]

        segments: list[dict[str, Any]] = []
        for block in split_markdown_blocks(text):
            block_type = block["type"]
            block_content = block["content"]
            if block_type == "text":
                self._append_text_segment(segments, render_qq_plaintext(block_content))
                continue

            if self._renderer_available():
                rendered_path = await self.image_renderer.render_to_image(
                    renderable_markdown_block(block),
                    block_type=block_type,
                )
                segments.append(self._image_segment(rendered_path))
                continue

            self._append_text_segment(segments, self._fallback_block_text(block))

        for attachment in attachments:
            segments.append(self._attachment_segment(attachment))

        merged = self._merge_adjacent_text_segments(segments)
        return merged

    def render_message_text(self, message: Message) -> str:
        text = self.downgrade_markdown((message.text or "").strip())
        if not text:
            return ""
        emoji = self._tag_emoji(message.message_tag)
        if emoji and not text.startswith(f"{emoji} "):
            return f"{emoji} {text}"
        return text

    def downgrade_markdown(self, text: str) -> str:
        if not text:
            return ""

        parts: list[str] = []
        for block in split_markdown_blocks(text):
            if block["type"] == "text":
                downgraded = render_qq_plaintext(block["content"]).replace("`", "")
                if downgraded:
                    parts.append(downgraded)
                continue
            if block["type"] == "table":
                table_text = self._downgrade_table_block(block["content"])
                if table_text:
                    parts.append(table_text)
                continue
            parts.append(renderable_markdown_block(block) or block["content"].strip("\n"))
        return "\n".join(part for part in parts if part).strip()

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

    async def send_message(self, *, user_id: str, message: Message) -> bool:
        segments = await self.format(message)
        if not segments:
            return True
        return await self.send_private_segments(user_id=user_id, segments=segments)

    async def send_private_segments(self, *, user_id: str, segments: list[dict[str, Any]]) -> bool:
        payload: dict[str, Any] = {"message": segments}
        try:
            payload["user_id"] = int(user_id)
        except (TypeError, ValueError):
            payload["user_id"] = str(user_id)

        result = await asyncio.to_thread(self._post_json, "/send_private_msg", payload)
        if not isinstance(result, dict):
            return False
        status = str(result.get("status") or "").strip().lower()
        return status == "ok"

    async def send_private_text(self, *, user_id: str, text: str) -> bool:
        return await self.send_private_segments(
            user_id=user_id,
            segments=[{"type": "text", "data": {"text": text}}],
        )

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
        except Exception:
            return None
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
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

    def _tag_emoji(self, message_tag: str | None) -> str:
        mapping = {
            "reminder": "🔔",
            "heartbeat": "💓",
            "email_scan": "📧",
            "tool_status": "ℹ️",
        }
        return mapping.get(str(message_tag or "").strip(), "")

    def _renderer_available(self) -> bool:
        return bool(self.image_renderer is not None and getattr(self.image_renderer, "available", False))

    def _fallback_block_text(self, block: dict[str, str]) -> str:
        content = str(block.get("content") or "")
        return content.strip("\n")

    def _downgrade_table_block(self, content: str) -> str:
        rows: list[str] = []
        for raw_line in content.splitlines():
            stripped = raw_line.strip()
            if not (stripped.startswith("|") and stripped.endswith("|")):
                rows.append(raw_line)
                continue
            if set(stripped.replace("|", "").replace(":", "").replace("-", "").replace(" ", "")) == set():
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            rows.append(" | ".join(cells))
        return "\n".join(row for row in rows if row).strip()

    def _append_text_segment(self, segments: list[dict[str, Any]], text: str) -> None:
        normalized = str(text or "")
        if not normalized:
            return
        segments.append({"type": "text", "data": {"text": normalized}})

    def _merge_adjacent_text_segments(
        self,
        segments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        for segment in segments:
            if segment.get("type") != "text":
                merged.append(segment)
                continue
            text = str(segment.get("data", {}).get("text") or "")
            if not text:
                continue
            if merged and merged[-1].get("type") == "text":
                previous_data = dict(merged[-1].get("data") or {})
                previous_data["text"] = str(previous_data.get("text") or "") + text
                merged[-1] = {"type": "text", "data": previous_data}
                continue
            merged.append({"type": "text", "data": {"text": text}})
        return merged

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

    def _attachment_segment(self, attachment: Attachment) -> dict[str, Any]:
        if attachment.type == "image":
            return self._image_segment(attachment.url)

        path = self._segment_file_value(attachment.url)
        data: dict[str, Any] = {"file": path}
        if attachment.filename:
            data["name"] = attachment.filename
        return {"type": "file", "data": data}

    def _image_segment(self, path_or_url: str) -> dict[str, Any]:
        return {
            "type": "image",
            "data": {"file": self._segment_file_value(path_or_url)},
        }

    def _segment_file_value(self, path_or_url: str) -> str:
        raw = str(path_or_url or "").strip()
        if raw.startswith(("http://", "https://", "file://")):
            return raw
        return Path(raw).expanduser().resolve(strict=False).as_uri()
