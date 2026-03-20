from __future__ import annotations

from datetime import datetime
import mimetypes
from pathlib import Path
import re
from typing import Literal
from uuid import uuid4

from hypo_agent.core.config_loader import get_memory_dir

AttachmentType = Literal["image", "file", "audio", "video"]

_INVALID_FILENAME_CHARS = re.compile(r"[\x00-\x1f/\\]+")


def get_uploads_dir(uploads_dir: Path | str | None = None) -> Path:
    root = Path(uploads_dir) if uploads_dir is not None else get_memory_dir() / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve(strict=False)


def sanitize_upload_filename(filename: str | None) -> str:
    raw_name = Path(str(filename or "").strip() or "upload.bin").name
    cleaned = _INVALID_FILENAME_CHARS.sub("_", raw_name).strip().strip(".")
    return cleaned or "upload.bin"


def build_upload_path(
    original_filename: str | None,
    *,
    uploads_dir: Path | str | None = None,
) -> Path:
    target_dir = get_uploads_dir(uploads_dir)
    safe_name = sanitize_upload_filename(original_filename)
    stamped_name = f"{datetime.now().strftime('%Y%m%d')}_{uuid4().hex}_{safe_name}"
    return (target_dir / stamped_name).resolve(strict=False)


def guess_mime_type(filename: str | None, declared_mime_type: str | None = None) -> str:
    explicit = str(declared_mime_type or "").strip()
    if explicit:
        return explicit
    guessed, _ = mimetypes.guess_type(str(filename or ""))
    return guessed or "application/octet-stream"


def classify_attachment_type(
    *,
    mime_type: str | None = None,
    filename: str | None = None,
) -> AttachmentType:
    resolved_mime = guess_mime_type(filename, mime_type)
    prefix = resolved_mime.split("/", 1)[0].strip().lower()
    if prefix == "image":
        return "image"
    if prefix == "audio":
        return "audio"
    if prefix == "video":
        return "video"
    return "file"
