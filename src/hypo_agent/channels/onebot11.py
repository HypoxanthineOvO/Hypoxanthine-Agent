from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


_CQ_AT_PATTERN = re.compile(r"\[CQ:at,qq=(\d+)\]")
_CQ_IMAGE_PATTERN = re.compile(r"\[CQ:image,[^\]]*\]")
_CQ_FILE_PATTERN = re.compile(r"\[CQ:file,[^\]]*\]")
_CQ_FACE_PATTERN = re.compile(r"\[CQ:face,[^\]]*\]")
_CQ_REPLY_PATTERN = re.compile(r"\[CQ:reply,[^\]]*\]")


@dataclass(slots=True)
class ParsedPrivateMessage:
    user_id: str
    text: str
    message_type: str
    raw_event: dict[str, Any]


def parse_onebot_private_message(
    payload: dict[str, Any],
    *,
    bot_qq: str | None = None,
) -> ParsedPrivateMessage | None:
    post_type = str(payload.get("post_type") or "").strip().lower()
    if post_type != "message":
        return None

    message_type = str(payload.get("message_type") or "").strip().lower()
    if message_type != "private":
        return None

    user_id = str(payload.get("user_id") or "").strip()
    if not user_id:
        return None
    if bot_qq and user_id == str(bot_qq).strip():
        return None

    message_value = payload.get("message")
    if message_value in (None, ""):
        message_value = payload.get("raw_message")
    text = _extract_text(message_value).strip()
    if not text:
        return None

    return ParsedPrivateMessage(
        user_id=user_id,
        text=text,
        message_type="private",
        raw_event=dict(payload),
    )


def _extract_text(message_value: Any) -> str:
    if isinstance(message_value, list):
        return _extract_text_from_segments(message_value)

    if isinstance(message_value, str):
        raw = message_value.strip()
        if not raw:
            return ""
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return _replace_cq_codes(raw)
        if isinstance(parsed, list):
            return _extract_text_from_segments(parsed)
        return _replace_cq_codes(raw)

    if isinstance(message_value, dict):
        return _extract_text_from_segments([message_value])

    return str(message_value or "")


def _extract_text_from_segments(segments: list[Any]) -> str:
    texts: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            texts.append(str(segment))
            continue

        segment_type = str(segment.get("type") or "").strip().lower()
        data = segment.get("data")
        if not isinstance(data, dict):
            data = {}

        if segment_type == "text":
            texts.append(str(data.get("text") or ""))
            continue
        if segment_type == "at":
            qq = str(data.get("qq") or "").strip()
            if qq:
                texts.append(f"@{qq}")
            continue
        if segment_type == "image":
            texts.append("[图片]")
            continue
        if segment_type == "file":
            texts.append("[文件]")
            continue
        if segment_type == "face":
            texts.append("[表情]")
            continue
        if segment_type == "reply":
            texts.append("[回复]")
            continue

        text = str(data.get("text") or "")
        if text:
            texts.append(text)

    normalized = " ".join(part for part in texts if part).strip()
    return _replace_cq_codes(normalized)


def _replace_cq_codes(text: str) -> str:
    rendered = _CQ_AT_PATTERN.sub(lambda match: f"@{match.group(1)}", text)
    rendered = _CQ_IMAGE_PATTERN.sub("[图片]", rendered)
    rendered = _CQ_FILE_PATTERN.sub("[文件]", rendered)
    rendered = _CQ_FACE_PATTERN.sub("[表情]", rendered)
    rendered = _CQ_REPLY_PATTERN.sub("[回复]", rendered)
    return rendered
