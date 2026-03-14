from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from hypo_agent.core.config_loader import expand_runtime_payload
from hypo_agent.models import SecurityConfig


class GatewaySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auth_token: str
    security: SecurityConfig


def load_gateway_settings(path: Path | str = "config/security.yaml") -> GatewaySettings:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    payload = expand_runtime_payload(payload)
    token = str(payload.get("auth_token", "")).strip()
    if not token:
        raise ValueError("auth_token is required in security.yaml")

    # SecurityConfig forbids extras; pass only declared keys.
    security_payload = {
        "directory_whitelist": payload.get("directory_whitelist", {}),
        "circuit_breaker": payload.get("circuit_breaker", {}),
    }
    security = SecurityConfig.model_validate(security_payload)

    return GatewaySettings(auth_token=token, security=security)
