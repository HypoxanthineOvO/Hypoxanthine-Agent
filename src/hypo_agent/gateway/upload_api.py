from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from starlette.datastructures import UploadFile as StarletteUploadFile

from hypo_agent.core.uploads import (
    build_upload_path,
    classify_attachment_type,
    get_uploads_dir,
    guess_mime_type,
    sanitize_upload_filename,
)
from hypo_agent.gateway.auth import require_api_token
from hypo_agent.models import Attachment

router = APIRouter(prefix="/api")

MAX_UPLOAD_FILES = 5
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024


def _file_too_large_error() -> HTTPException:
    return HTTPException(status_code=413, detail="File size cannot exceed 100MB")


async def _extract_uploads(request: Request) -> list[UploadFile]:
    try:
        form = await request.form(
            max_files=MAX_UPLOAD_FILES,
            max_part_size=MAX_UPLOAD_BYTES,
        )
    except HTTPException as exc:
        detail = str(exc.detail or "")
        if exc.status_code == 400 and "Part exceeded maximum size" in detail:
            raise _file_too_large_error() from exc
        if exc.status_code == 400 and "Too many files" in detail:
            raise HTTPException(status_code=400, detail="Too many files") from exc
        raise

    uploads = [item for item in form.getlist("file") if isinstance(item, StarletteUploadFile)]
    return [item for item in uploads if item.filename is not None]


async def _persist_upload(upload: UploadFile, *, uploads_dir: Path) -> Attachment:
    original_filename = sanitize_upload_filename(upload.filename)
    target_path = build_upload_path(original_filename, uploads_dir=uploads_dir)
    size_bytes = 0
    reported_size = getattr(upload, "size", None)

    if reported_size is not None:
        try:
            if int(reported_size) > MAX_UPLOAD_BYTES:
                raise _file_too_large_error()
        except (TypeError, ValueError):
            pass

    try:
        with target_path.open("wb") as handle:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > MAX_UPLOAD_BYTES:
                    raise _file_too_large_error()
                handle.write(chunk)
    except HTTPException:
        target_path.unlink(missing_ok=True)
        raise
    except Exception:
        target_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    mime_type = guess_mime_type(original_filename, upload.content_type)
    return Attachment(
        type=classify_attachment_type(mime_type=mime_type, filename=original_filename),
        url=str(target_path),
        filename=original_filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
    )


@router.post("/upload")
async def upload_files(
    request: Request,
) -> dict[str, list[dict[str, object]]]:
    require_api_token(request)

    uploads = await _extract_uploads(request)
    if not uploads:
        raise HTTPException(status_code=400, detail="No files uploaded")
    if len(uploads) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail="Too many files")

    uploads_dir = get_uploads_dir(getattr(request.app.state, "uploads_dir", None))
    attachments = [await _persist_upload(item, uploads_dir=uploads_dir) for item in uploads]
    return {
        "attachments": [
            attachment.model_dump(mode="json")
            for attachment in attachments
        ]
    }
