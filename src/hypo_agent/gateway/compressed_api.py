from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
import structlog

router = APIRouter(prefix="/api")
logger = structlog.get_logger("hypo_agent.gateway.compressed_api")


@router.get("/compressed/{cache_id}")
async def get_compressed_original(cache_id: str, request: Request) -> dict[str, Any]:
    compressor = getattr(request.app.state, "output_compressor", None)
    if compressor is None:
        logger.warning(
            "compressed_api.fetch.unavailable",
            cache_id=cache_id,
        )
        raise HTTPException(status_code=503, detail="Compressed cache is unavailable")

    output = compressor.get_original_output(cache_id)
    if output is None:
        logger.info("compressed_api.fetch.miss", cache_id=cache_id)
        raise HTTPException(status_code=404, detail="Original output not found")

    recent = compressor.get_recent_originals()
    item = recent.get(cache_id, {}) if isinstance(recent, dict) else {}
    metadata = item.get("metadata", {}) if isinstance(item, dict) else {}
    created_at = item.get("created_at") if isinstance(item, dict) else None

    logger.info(
        "compressed_api.fetch.hit",
        cache_id=cache_id,
        original_chars=len(output),
        session_id=metadata.get("session_id") if isinstance(metadata, dict) else None,
        tool_name=metadata.get("tool_name") if isinstance(metadata, dict) else None,
    )
    return {
        "cache_id": cache_id,
        "original_output": output,
        "metadata": metadata if isinstance(metadata, dict) else {},
        "created_at": created_at,
    }
