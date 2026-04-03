from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from hypo_agent.core.config_loader import expand_runtime_payload
from hypo_agent.models import SecurityConfig


class FeishuChannelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class QQChannelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class QQBotChannelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class WeixinChannelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class ChannelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feishu: FeishuChannelSettings = Field(default_factory=FeishuChannelSettings)
    qq: QQChannelSettings = Field(default_factory=QQChannelSettings)
    qq_bot: QQBotChannelSettings = Field(default_factory=QQBotChannelSettings)
    weixin: WeixinChannelSettings = Field(default_factory=WeixinChannelSettings)


class GatewaySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auth_token: str
    security: SecurityConfig
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)


def load_channel_settings(path: Path | str = "config/config.yaml") -> ChannelsConfig:
    config_path = Path(path)
    if not config_path.exists():
        return ChannelsConfig()
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    payload = expand_runtime_payload(payload)
    channels_payload = payload.get("channels", {}) if isinstance(payload, dict) else {}
    return ChannelsConfig.model_validate(channels_payload)


def load_gateway_settings(
    path: Path | str = "config/security.yaml",
    channels_path: Path | str | None = None,
) -> GatewaySettings:
    security_path = Path(path)
    payload = yaml.safe_load(security_path.read_text(encoding="utf-8")) or {}
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
    resolved_channels_path = security_path.with_name("config.yaml") if channels_path is None else channels_path
    channels = load_channel_settings(resolved_channels_path)

    return GatewaySettings(auth_token=token, security=security, channels=channels)
