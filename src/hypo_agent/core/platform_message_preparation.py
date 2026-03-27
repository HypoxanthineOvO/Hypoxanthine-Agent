from __future__ import annotations

import re
from urllib.parse import unquote

import structlog

from hypo_agent.models import Attachment, Message

logger = structlog.get_logger("hypo_agent.core.platform_message_preparation")

_MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\((?P<content>[^)\r\n]+)\)")
_CQ_IMAGE_PATTERN = re.compile(r"\[CQ:image,(?P<content>[^\]]+)\]")
_DATA_IMAGE_PATTERN = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+")
_URL_IMAGE_PATTERN = re.compile(
    r"(?P<content>(?:https?|file)://[^\s<>()]+?\.(?:png|jpe?g|gif|webp|bmp|svg)(?:\?[^\s<>()]*)?)",
    re.IGNORECASE,
)


def prepare_message_for_platform(message: Message, platform: str) -> list[Message]:
    normalized_platform = str(platform or "").strip().lower()
    if normalized_platform not in {"weixin", "qq", "qq_bot"}:
        return [message]

    text = str(message.text or "")
    rewritten_text, inline_image_refs, has_non_image_text = _split_inline_images(text)

    image_attachments: list[Attachment] = []
    non_image_attachments: list[Attachment] = []
    for attachment in message.attachments:
        copied = attachment.model_copy()
        if copied.type == "image":
            image_attachments.append(copied)
            continue
        non_image_attachments.append(copied)

    legacy_image = str(message.image or "").strip()
    prepared_image_attachments = [
        Attachment(type="image", url=image_ref)
        for image_ref in inline_image_refs
        if str(image_ref or "").strip()
    ]
    prepared_image_attachments.extend(image_attachments)
    if legacy_image:
        prepared_image_attachments.append(Attachment(type="image", url=legacy_image))

    prepared: list[Message] = []
    should_send_base_message = any(
        (
            has_non_image_text,
            non_image_attachments,
            str(message.file or "").strip(),
            str(message.audio or "").strip(),
        )
    )
    if should_send_base_message:
        prepared.append(
            message.model_copy(
                update={
                    "text": rewritten_text if has_non_image_text else None,
                    "attachments": non_image_attachments,
                    "image": None,
                }
            )
        )

    for attachment in prepared_image_attachments:
        prepared.append(
            message.model_copy(
                update={
                    "text": None,
                    "image": None,
                    "attachments": [attachment.model_copy()],
                    "file": None,
                    "audio": None,
                }
            )
        )

    return prepared or [message]


def _split_inline_images(text: str) -> tuple[str | None, list[str], bool]:
    if not text:
        return None, [], False

    matches = _ordered_matches(text)
    if not matches:
        return text, [], bool(text.strip())

    residual_parts: list[str] = []
    image_refs: list[str] = []
    supported_matches: list[tuple[int, int, str]] = []
    cursor = 0
    for match in matches:
        start, end = match.span()
        residual_parts.append(text[cursor:start])
        extracted = _extract_image_ref(match)
        if extracted is None:
            residual_parts.append(text[start:end])
            cursor = end
            continue
        image_refs.append(extracted)
        supported_matches.append((start, end, extracted))
        cursor = end
    residual_parts.append(text[cursor:])

    if not image_refs:
        return text, [], bool(text.strip())

    residual_text = "".join(residual_parts)
    has_non_image_text = bool(residual_text.strip())
    if not has_non_image_text:
        return None, image_refs, False

    rebuilt_parts: list[str] = []
    cursor = 0
    total = len(supported_matches)
    for index, (start, end, _image_ref) in enumerate(supported_matches, start=1):
        rebuilt_parts.append(text[cursor:start])
        rebuilt_parts.append(_placeholder_for_index(index, total=total))
        cursor = end
    rebuilt_parts.append(text[cursor:])
    rebuilt = "".join(rebuilt_parts).strip()
    return rebuilt or None, image_refs, has_non_image_text


def _ordered_matches(text: str) -> list[re.Match[str]]:
    matches: list[re.Match[str]] = []
    for pattern in (_MARKDOWN_IMAGE_PATTERN, _CQ_IMAGE_PATTERN, _DATA_IMAGE_PATTERN, _URL_IMAGE_PATTERN):
        matches.extend(pattern.finditer(text))
    matches.sort(key=lambda item: item.span()[0])

    filtered: list[re.Match[str]] = []
    last_end = -1
    for match in matches:
        start, end = match.span()
        if start < last_end:
            continue
        filtered.append(match)
        last_end = end
    return filtered


def _extract_image_ref(match: re.Match[str]) -> str | None:
    pattern = match.re.pattern
    raw = match.group(0)

    if pattern == _MARKDOWN_IMAGE_PATTERN.pattern:
        content = str(match.group("content") or "").strip()
        image_ref = _parse_markdown_image_ref(content)
        if image_ref:
            return image_ref
        logger.warning("platform_message_preparation.markdown_image_unsupported", raw=raw)
        return None

    if pattern == _CQ_IMAGE_PATTERN.pattern:
        content = str(match.group("content") or "").strip()
        image_ref = _parse_cq_image_ref(content)
        if image_ref:
            return image_ref
        logger.warning("platform_message_preparation.cq_image_unsupported", raw=raw)
        return None

    if pattern == _DATA_IMAGE_PATTERN.pattern:
        return raw.strip()

    if pattern == _URL_IMAGE_PATTERN.pattern:
        return str(match.group("content") or raw).strip()

    return None


def _parse_markdown_image_ref(content: str) -> str | None:
    stripped = content.strip()
    if not stripped:
        return None
    if stripped.startswith("<") and ">" in stripped:
        return stripped[1 : stripped.index(">")].strip() or None
    if " " in stripped:
        stripped = stripped.split(" ", 1)[0].strip()
    return stripped or None


def _parse_cq_image_ref(content: str) -> str | None:
    params: dict[str, str] = {}
    for item in content.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        normalized_key = key.strip().lower()
        normalized_value = unquote(value.strip())
        if normalized_key and normalized_value:
            params[normalized_key] = normalized_value

    for key in ("url", "file"):
        candidate = params.get(key)
        if candidate:
            return candidate
    return None


def _placeholder_for_index(index: int, *, total: int) -> str:
    if total <= 1:
        return "【见下方图片】"
    return f"【见下方图片 {index}】"
