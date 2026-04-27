from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
import structlog

from hypo_agent.gateway.auth import require_api_token

router = APIRouter(prefix="/api")
logger = structlog.get_logger("hypo_agent.gateway.files_api")


@router.get("/files")
async def serve_file(
    request: Request,
    path: str = Query(..., min_length=1),
):
    return _serve_file_response(request, path=path)


@router.get("/files/{filename:path}")
async def serve_named_file(
    request: Request,
    filename: str,
    path: str = Query(..., min_length=1),
):
    download_filename = Path(str(filename or "")).name or None
    return _serve_file_response(request, path=path, download_filename=download_filename)


def _serve_file_response(
    request: Request,
    *,
    path: str,
    download_filename: str | None = None,
):
    require_api_token(request)

    permission_manager = getattr(request.app.state, "permission_manager", None)
    if permission_manager is None:
        logger.error("files_api.serve.unavailable", path=path)
        raise HTTPException(status_code=503, detail="Permission manager unavailable")

    has_whitelist_match = bool(permission_manager.has_whitelist_match(path))
    if not has_whitelist_match:
        logger.warning(
            "files_api.serve.denied",
            path=path,
            reason="Path is outside configured whitelist",
        )
        raise HTTPException(status_code=403, detail="Permission denied")

    allowed, reason = permission_manager.check_permission(path, "read")
    if not allowed:
        logger.warning(
            "files_api.serve.denied",
            path=path,
            reason=reason,
        )
        raise HTTPException(status_code=403, detail="Permission denied")

    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.exists() or not resolved.is_file():
        logger.info("files_api.serve.not_found", path=path, resolved_path=str(resolved))
        raise HTTPException(status_code=404, detail="File not found")

    logger.info(
        "files_api.serve.hit",
        path=path,
        resolved_path=str(resolved),
        size_bytes=resolved.stat().st_size,
        download_filename=download_filename,
    )
    return FileResponse(path=resolved, filename=download_filename)
