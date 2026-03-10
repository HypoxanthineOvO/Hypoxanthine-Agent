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
    )
    return FileResponse(path=resolved)
