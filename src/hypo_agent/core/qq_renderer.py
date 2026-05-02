from __future__ import annotations

from pathlib import Path
from typing import Any

from hypo_agent.core.image_renderer import ImageRenderError
from hypo_agent.core.markdown_plaintext import downgrade_markdown_table
from hypo_agent.core.qq_text_renderer import render_qq_plaintext
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


class QQRenderer:
    def __init__(self, *, image_renderer: Any | None = None) -> None:
        self.image_renderer = image_renderer

    async def render(self, message: UnifiedMessage | Message) -> list[dict[str, Any]]:
        unified = self._coerce_message(message)
        pending_emoji = self._tag_emoji(unified.message_tag)
        segments: list[dict[str, Any]] = []

        for block in unified.blocks:
            if isinstance(block, TextBlock):
                text = render_qq_plaintext(block.text)
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
                table_text = block.markdown.strip()
                if table_text:
                    if pending_emoji:
                        table_text = f"{pending_emoji} {table_text}"
                        pending_emoji = ""
                    self._append_text_segment(segments, table_text)
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
                segments.append(
                    {
                        "type": "file",
                        "source": block.url,
                        "name": block.filename or Path(str(block.url or "")).name or block.attachment_type,
                        "mime_type": block.mime_type,
                        "attachment_type": block.attachment_type,
                    }
                )

        return self._merge_adjacent_text_segments(segments)

    def render_message_text(self, message: UnifiedMessage | Message) -> str:
        unified = self._coerce_message(message)
        parts: list[str] = []
        for block in unified.blocks:
            if isinstance(block, TextBlock):
                downgraded = render_qq_plaintext(block.text).replace("`", "")
                if downgraded:
                    parts.append(downgraded)
                continue
            if isinstance(block, TableBlock):
                table_text = self._downgrade_table_block(block.markdown)
                if table_text:
                    parts.append(table_text)
                continue
            if isinstance(block, CodeBlock):
                parts.append(block.text)
                continue
            if isinstance(block, MathBlock):
                parts.append(block.text)
                continue
            if isinstance(block, DiagramBlock):
                parts.append(block.text)
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

    def downgrade_markdown(self, text: str) -> str:
        return self.render_message_text(
            unified_message_from_message(
                Message(text=text, sender="assistant", session_id="main"),
                message_type="ai_reply",
            )
        )

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
                rendered_path = await self.image_renderer.render_to_image(
                    content,
                    block_type=block_type,
                )
            except ImageRenderError as exc:
                fallback = exc.fallback_text
            except (OSError, RuntimeError, TypeError, ValueError):
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

    def _downgrade_table_block(self, content: str) -> str:
        return downgrade_markdown_table(content)

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
