from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from hypo_agent.core.image_renderer import ImageRenderError
from hypo_agent.core.unified_message import (
    CodeBlock,
    DiagramBlock,
    FileAttachmentBlock,
    ImageAttachmentBlock,
    MathBlock,
    TableBlock,
    TextBlock,
    UnifiedMessage,
    unified_message_from_message,
)
from hypo_agent.models import Message

_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_UNORDERED_LIST_RE = re.compile(r"^(\s*)[-*]\s+")
_QUOTE_RE = re.compile(r"^\s*>\s?")


class WeixinRenderer:
    def __init__(self, *, image_renderer: Any | None = None) -> None:
        self.image_renderer = image_renderer

    async def render(self, message: UnifiedMessage | Message) -> list[dict[str, Any]]:
        unified = self._coerce_message(message)
        pending_emoji = self._tag_emoji(unified.message_tag)
        segments: list[dict[str, Any]] = []

        for block in unified.blocks:
            if isinstance(block, TextBlock):
                text = self._render_plaintext(block.text)
                if pending_emoji and text.strip():
                    text = f"{pending_emoji} {text}"
                    pending_emoji = ""
                self._append_text_segment(segments, text)
                continue
            if isinstance(block, CodeBlock):
                consumed = await self._append_rendered_block(
                    segments,
                    content=block.text,
                    block_type="code",
                    pending_emoji=pending_emoji,
                )
                pending_emoji = "" if consumed else pending_emoji
                continue
            if isinstance(block, TableBlock):
                consumed = await self._append_rendered_block(
                    segments,
                    content=block.markdown,
                    block_type="table",
                    pending_emoji=pending_emoji,
                )
                pending_emoji = "" if consumed else pending_emoji
                continue
            if isinstance(block, MathBlock):
                consumed = await self._append_rendered_block(
                    segments,
                    content=block.text,
                    block_type="math",
                    pending_emoji=pending_emoji,
                )
                pending_emoji = "" if consumed else pending_emoji
                continue
            if isinstance(block, DiagramBlock):
                consumed = await self._append_rendered_block(
                    segments,
                    content=block.text,
                    block_type=block.syntax,
                    pending_emoji=pending_emoji,
                )
                pending_emoji = "" if consumed else pending_emoji
                continue
            if isinstance(block, ImageAttachmentBlock):
                segments.append(
                    {
                        "type": "image",
                        "source": block.url,
                        "name": block.filename or Path(str(block.url or "")).name or None,
                    }
                )
                continue
            if isinstance(block, FileAttachmentBlock):
                label = block.filename or Path(str(block.url or "")).name or block.attachment_type
                self._append_text_segment(segments, f"[文件] {label}")

        return self._merge_adjacent_text_segments(segments)

    def render_message_text(self, message: UnifiedMessage | Message) -> str:
        unified = self._coerce_message(message)
        parts: list[str] = []
        for block in unified.blocks:
            if isinstance(block, TextBlock):
                rendered = self._render_plaintext(block.text)
                if rendered:
                    parts.append(rendered)
                continue
            if isinstance(block, TableBlock):
                parts.append(self._fallback_block_text(block.markdown, block_type="table"))
                continue
            if isinstance(block, CodeBlock):
                parts.append(self._fallback_block_text(block.text, block_type="code"))
                continue
            if isinstance(block, MathBlock):
                parts.append(self._fallback_block_text(block.text, block_type="math"))
                continue
            if isinstance(block, DiagramBlock):
                parts.append(self._fallback_block_text(block.text, block_type=block.syntax))
                continue
            if isinstance(block, ImageAttachmentBlock):
                label = block.filename or Path(str(block.url or "")).name or "image"
                parts.append(f"[图片] {label}")
                continue
            if isinstance(block, FileAttachmentBlock):
                label = block.filename or Path(str(block.url or "")).name or block.attachment_type
                parts.append(f"[文件] {label}")

        text = "\n".join(part for part in parts if part).strip()
        emoji = self._tag_emoji(unified.message_tag)
        if emoji and text and not text.startswith(f"{emoji} "):
            return f"{emoji} {text}"
        return text

    async def _append_rendered_block(
        self,
        segments: list[dict[str, Any]],
        *,
        content: str,
        block_type: str,
        pending_emoji: str,
    ) -> bool:
        if self._renderer_available():
            try:
                rendered_path = await self.image_renderer.render_to_image(content, block_type=block_type)
            except ImageRenderError as exc:
                fallback = exc.fallback_text
            except Exception:
                fallback = self._fallback_block_text(content, block_type=block_type)
            else:
                segments.append(
                    {
                        "type": "image",
                        "source": rendered_path,
                        "name": Path(str(rendered_path)).name or None,
                        "fallback_text": self._fallback_block_text(content, block_type=block_type),
                    }
                )
                return True
        else:
            fallback = self._fallback_block_text(content, block_type=block_type)

        text = str(fallback or "")
        if pending_emoji and text.strip():
            text = f"{pending_emoji} {text}"
        self._append_text_segment(segments, text)
        return bool(text.strip())

    def _renderer_available(self) -> bool:
        return bool(self.image_renderer is not None and getattr(self.image_renderer, "available", False))

    def _render_plaintext(self, markdown_text: str) -> str:
        if not markdown_text:
            return ""
        rendered_lines: list[str] = []
        for raw_line in markdown_text.splitlines():
            line = raw_line.rstrip()
            line = _HEADING_RE.sub("", line)
            line = _QUOTE_RE.sub("", line)
            line = _UNORDERED_LIST_RE.sub(lambda match: f"{match.group(1)}- ", line, count=1)
            line = _LINK_RE.sub(r"\1 (\2)", line)
            line = _BOLD_RE.sub(r"\1", line)
            line = _ITALIC_RE.sub(r"\1", line)
            line = _STRIKE_RE.sub(r"\1", line)
            line = _INLINE_CODE_RE.sub(r"\1", line)
            rendered_lines.append(line)
        return "\n".join(rendered_lines).strip()

    def _fallback_block_text(self, content: str, *, block_type: str) -> str:
        if self.image_renderer is not None and hasattr(self.image_renderer, "build_fallback_text"):
            return str(self.image_renderer.build_fallback_text(content, block_type=block_type))
        labels = {
            "table": "表格",
            "code": "代码块",
            "math": "公式",
            "mermaid": "Mermaid 图",
        }
        label = labels.get(block_type, "内容")
        body = str(content or "").strip() or "[空内容]"
        return f"[{label}渲染失败，原始内容如下]\n{body}"

    def _append_text_segment(self, segments: list[dict[str, Any]], text: str) -> None:
        normalized = str(text or "")
        if not normalized:
            return
        segments.append({"type": "text", "text": normalized})

    def _merge_adjacent_text_segments(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        for segment in segments:
            if segment.get("type") != "text":
                merged.append(segment)
                continue
            text = str(segment.get("text") or "")
            if not text:
                continue
            if merged and merged[-1].get("type") == "text":
                merged[-1] = {
                    "type": "text",
                    "text": str(merged[-1].get("text") or "") + text,
                }
                continue
            merged.append({"type": "text", "text": text})
        return merged

    def _tag_emoji(self, message_tag: str | None) -> str:
        mapping = {
            "reminder": "🔔",
            "heartbeat": "💓",
            "email_scan": "📧",
            "tool_status": "ℹ️",
        }
        return mapping.get(str(message_tag or "").strip(), "")

    def _coerce_message(self, message: UnifiedMessage | Message) -> UnifiedMessage:
        if isinstance(message, UnifiedMessage):
            return message
        message_type = "user_message" if str(message.sender or "").strip().lower() == "user" else "ai_reply"
        return unified_message_from_message(message, message_type=message_type)
