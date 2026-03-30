from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from hypo_agent.core.markdown_splitter import renderable_markdown_block, split_markdown_blocks
from hypo_agent.models import Attachment, Message

_FENCE_OPEN_RE = re.compile(r"^```(?P<lang>[a-zA-Z0-9_+-]*)[^\n]*$")


class MessageProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_channel: str
    source_user: str | None = None
    source_message_id: str | None = None
    origin_webui_client_id: str | None = None


class TextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = "text"
    text: str


class CodeBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["code"] = "code"
    text: str
    language: str | None = None


class TableBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["table"] = "table"
    markdown: str


class MathBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["math"] = "math"
    text: str


class DiagramBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["diagram"] = "diagram"
    syntax: Literal["mermaid"] = "mermaid"
    text: str


class ImageAttachmentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["image_attachment"] = "image_attachment"
    url: str
    filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None


class FileAttachmentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["file_attachment"] = "file_attachment"
    attachment_type: Literal["file", "audio", "video"]
    url: str
    filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None


ContentBlock = (
    TextBlock
    | CodeBlock
    | TableBlock
    | MathBlock
    | DiagramBlock
    | ImageAttachmentBlock
    | FileAttachmentBlock
)


class UnifiedMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_type: Literal["user_message", "ai_reply"]
    blocks: list[ContentBlock] = Field(default_factory=list)
    provenance: MessageProvenance
    session_id: str
    channel: str
    sender: str
    sender_id: str | None = None
    message_tag: str | None = None
    raw_text: str | None = None
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def plain_text(self) -> str:
        parts: list[str] = []
        for block in self.blocks:
            if isinstance(block, TextBlock):
                parts.append(block.text)
                continue
            if isinstance(block, CodeBlock):
                parts.append(block.text)
                continue
            if isinstance(block, TableBlock):
                parts.append(block.markdown)
                continue
            if isinstance(block, MathBlock):
                parts.append(block.text)
                continue
            if isinstance(block, DiagramBlock):
                parts.append(block.text)
                continue
            if isinstance(block, ImageAttachmentBlock):
                label = block.filename or "image"
                parts.append(f"[图片] {label}".strip())
                continue
            if isinstance(block, FileAttachmentBlock):
                label = block.filename or block.attachment_type
                parts.append(f"[文件] {label}".strip())
        return "\n".join(part for part in parts if part).strip()


def unified_message_from_message(
    message: Message,
    *,
    message_type: Literal["user_message", "ai_reply"],
) -> UnifiedMessage:
    return UnifiedMessage(
        message_type=message_type,
        blocks=_blocks_from_message(message),
        provenance=MessageProvenance(
            source_channel=_normalized_channel_name(message.channel),
            source_user=str(message.sender_id or "").strip() or str(message.sender or "").strip() or None,
            source_message_id=_message_id_from_metadata(message.metadata),
            origin_webui_client_id=str(message.metadata.get("webui_client_id") or "").strip() or None,
        ),
        session_id=str(message.session_id or "").strip() or "main",
        channel=_normalized_channel_name(message.channel),
        sender=str(message.sender or "").strip() or "assistant",
        sender_id=str(message.sender_id or "").strip() or None,
        message_tag=str(message.message_tag or "").strip() or None,
        raw_text=message.text,
        timestamp=message.timestamp,
        metadata=dict(message.metadata),
    )


def prepend_text_prefix(message: UnifiedMessage, prefix: str) -> UnifiedMessage:
    normalized_prefix = str(prefix or "")
    if not normalized_prefix:
        return message

    blocks = list(message.blocks)
    if blocks and isinstance(blocks[0], TextBlock):
        first = blocks[0]
        if first.text.startswith(normalized_prefix):
            return message
        blocks[0] = first.model_copy(update={"text": f"{normalized_prefix}{first.text}"})
    else:
        blocks.insert(0, TextBlock(text=f"{normalized_prefix}\n"))
    raw_text = message.raw_text
    if raw_text and not raw_text.startswith(normalized_prefix):
        raw_text = f"{normalized_prefix}{raw_text}"
    elif not raw_text:
        raw_text = f"{normalized_prefix}\n"
    return message.model_copy(update={"blocks": blocks, "raw_text": raw_text})


def message_from_unified(message: UnifiedMessage) -> Message:
    attachments: list[Attachment] = []
    legacy_image: str | None = None
    legacy_file: str | None = None
    legacy_audio: str | None = None
    for block in message.blocks:
        if isinstance(block, ImageAttachmentBlock):
            attachment = Attachment(
                type="image",
                url=block.url,
                filename=block.filename,
                mime_type=block.mime_type,
                size_bytes=block.size_bytes,
            )
            attachments.append(attachment)
            if legacy_image is None:
                legacy_image = block.url
            continue
        if not isinstance(block, FileAttachmentBlock):
            continue
        attachment = Attachment(
            type=block.attachment_type,
            url=block.url,
            filename=block.filename,
            mime_type=block.mime_type,
            size_bytes=block.size_bytes,
        )
        attachments.append(attachment)
        if block.attachment_type == "file" and legacy_file is None:
            legacy_file = block.url
        if block.attachment_type == "audio" and legacy_audio is None:
            legacy_audio = block.url

    return Message(
        text=message.raw_text if message.raw_text is not None else message.plain_text() or None,
        attachments=attachments,
        image=legacy_image,
        file=legacy_file,
        audio=legacy_audio,
        sender=message.sender,
        message_tag=message.message_tag,  # type: ignore[arg-type]
        metadata=dict(message.metadata),
        timestamp=message.timestamp,
        session_id=message.session_id,
        channel=message.channel,
        sender_id=message.sender_id,
    )


def _blocks_from_message(message: Message) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    text = str(message.text or "")
    if text:
        blocks.extend(_blocks_from_text(text))

    blocks.extend(_blocks_from_attachments(message.attachments))

    legacy_image = str(message.image or "").strip()
    if legacy_image:
        blocks.append(ImageAttachmentBlock(url=legacy_image))

    for attachment_type, raw_url in (
        ("file", str(message.file or "").strip()),
        ("audio", str(message.audio or "").strip()),
    ):
        if raw_url:
            blocks.append(
                FileAttachmentBlock(
                    attachment_type=attachment_type,  # type: ignore[arg-type]
                    url=raw_url,
                )
            )
    return blocks


def _blocks_from_text(text: str) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    for block in split_markdown_blocks(text):
        block_type = str(block.get("type") or "").strip().lower()
        content = str(block.get("content") or "")
        if not content:
            continue
        if block_type == "text":
            blocks.append(TextBlock(text=content))
            continue
        if block_type == "code":
            blocks.append(
                CodeBlock(
                    text=renderable_markdown_block(block),
                    language=_code_language_from_fence(content),
                )
            )
            continue
        if block_type == "table":
            blocks.append(TableBlock(markdown=content.strip("\n")))
            continue
        if block_type == "math":
            blocks.append(MathBlock(text=renderable_markdown_block(block)))
            continue
        if block_type == "mermaid":
            blocks.append(DiagramBlock(text=renderable_markdown_block(block)))
            continue
        blocks.append(TextBlock(text=content))
    return blocks


def _blocks_from_attachments(attachments: list[Attachment]) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    for attachment in attachments:
        copied = attachment.model_copy()
        if copied.type == "image":
            blocks.append(
                ImageAttachmentBlock(
                    url=copied.url,
                    filename=copied.filename,
                    mime_type=copied.mime_type,
                    size_bytes=copied.size_bytes,
                )
            )
            continue
        blocks.append(
            FileAttachmentBlock(
                attachment_type=copied.type,  # type: ignore[arg-type]
                url=copied.url,
                filename=copied.filename,
                mime_type=copied.mime_type,
                size_bytes=copied.size_bytes,
            )
        )
    return blocks


def _code_language_from_fence(content: str) -> str | None:
    first_line = content.splitlines()[0] if content.splitlines() else ""
    match = _FENCE_OPEN_RE.match(first_line.strip())
    if not match:
        return None
    language = str(match.group("lang") or "").strip().lower()
    return language or None


def _message_id_from_metadata(metadata: dict[str, Any]) -> str | None:
    qq_meta = metadata.get("qq")
    if isinstance(qq_meta, dict):
        msg_id = str(qq_meta.get("msg_id") or "").strip()
        if msg_id:
            return msg_id

    for key in ("message_id", "msg_id", "source_message_id"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return None


def _normalized_channel_name(channel: str | None) -> str:
    normalized = str(channel or "").strip().lower()
    return normalized or "webui"
