from __future__ import annotations

from fastapi import HTTPException, Request
import structlog

logger = structlog.get_logger("hypo_agent.gateway.auth")


def _extract_token(request: Request) -> str:
    auth_header = str(request.headers.get("Authorization", ""))
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer ") :].strip()

    query_token = request.query_params.get("token")
    return str(query_token or "").strip()


def require_api_token(request: Request) -> None:
    expected = str(getattr(request.app.state, "auth_token", "")).strip()
    provided = _extract_token(request)
    if expected and provided == expected:
        return

    logger.warning(
        "gateway_auth.verify.denied",
        path=request.url.path,
        has_bearer=bool(request.headers.get("Authorization")),
        has_query_token=bool(request.query_params.get("token")),
    )
    raise HTTPException(status_code=401, detail="Unauthorized")
