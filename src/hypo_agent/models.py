from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    image: str | None = None
    file: str | None = None
    audio: str | None = None
    sender: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_id: str


class SkillOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "error", "partial", "timeout"]
    result: Any = None
    error_info: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_model: str
    models: dict[str, dict[str, Any]] = Field(default_factory=dict)
    task_type_to_model: dict[str, str] = Field(default_factory=dict)


class DirectoryWhitelist(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)
    execute: list[str] = Field(default_factory=list)


class CircuitBreakerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_level_max_failures: int = 3
    session_level_max_failures: int = 5
    cooldown_seconds: int = 120
    global_kill_switch: bool = False


class SecurityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory_whitelist: DirectoryWhitelist
    circuit_breaker: CircuitBreakerConfig


class PersonaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    aliases: list[str] = Field(default_factory=list)
    personality: list[str] = Field(default_factory=list)
    speaking_style: dict[str, Any] = Field(default_factory=dict)
