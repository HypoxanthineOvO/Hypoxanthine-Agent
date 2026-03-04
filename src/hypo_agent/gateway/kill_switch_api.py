from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
import structlog

router = APIRouter(prefix="/api")
logger = structlog.get_logger()


class KillSwitchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


@router.post("/kill-switch")
async def set_kill_switch(payload: KillSwitchPayload, request: Request) -> dict[str, bool]:
    deps = request.app.state.deps
    logger.warning("kill_switch.toggled", enabled=payload.enabled)
    deps.circuit_breaker.set_global_kill_switch(payload.enabled)
    return {"enabled": deps.circuit_breaker.get_global_kill_switch()}
