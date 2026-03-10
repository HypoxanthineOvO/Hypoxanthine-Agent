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
    message_tag: Literal["reminder", "heartbeat", "email_scan", "tool_status"] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_id: str
    channel: str = "webui"
    sender_id: str | None = None


class SkillOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "error", "partial", "timeout"]
    result: Any = None
    error_info: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SingleModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    litellm_model: str | None = None
    fallback: str | None = None
    supports_tool_calling: bool | None = None
    context_window: int | None = None
    description: str | None = None


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_model: str
    task_routing: dict[str, str] = Field(default_factory=dict)
    models: dict[str, SingleModelConfig] = Field(default_factory=dict)


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_base: str | None = None
    api_key: str


class EmailAccountConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    host: str
    port: int = 993
    username: str
    password: str
    folder: str = "INBOX"
    use_ssl: bool = True


class EmailServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accounts: list[EmailAccountConfig] = Field(default_factory=list)


class QQServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    napcat_ws_url: str
    napcat_http_url: str
    napcat_http_token: str | None = None
    bot_qq: str
    allowed_users: list[str] = Field(default_factory=list)


class ServicesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailServiceConfig | None = None
    qq: QQServiceConfig | None = None


class SecretsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    services: ServicesConfig | None = None


class TaskScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    interval_minutes: int = Field(default=15, ge=1)


class TasksConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    heartbeat: TaskScheduleConfig = Field(default_factory=TaskScheduleConfig)
    email_scan: TaskScheduleConfig = Field(default_factory=TaskScheduleConfig)


class WhitelistRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    permissions: list[Literal["read", "write", "execute"]] = Field(default_factory=list)


class DirectoryWhitelist(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[WhitelistRule] = Field(default_factory=list)
    default_policy: Literal["readonly"] = "readonly"
    blocked_paths: list[str] = Field(default_factory=list)


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


class HeartbeatCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_type: Literal[
        "file_exists",
        "process_running",
        "http_status",
        "custom_command",
    ]
    target: str
    expected: str | int | None = None
    timeout_seconds: int = 10


class ReminderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str | None = None
    schedule_type: Literal["once", "cron"]
    schedule_value: str
    channel: str = "all"
    heartbeat_config: list[HeartbeatCheck] | None = None
    confirm: bool = False


class ReminderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None
    schedule_type: Literal["once", "cron"] | None = None
    schedule_value: str | None = None
    channel: str | None = None
    status: Literal["active", "paused", "completed", "missed", "deleted"] | None = None
    next_run_at: str | None = None
    heartbeat_config: list[HeartbeatCheck] | None = None


class Reminder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    title: str
    description: str | None = None
    schedule_type: Literal["once", "cron"]
    schedule_value: str
    channel: str = "all"
    status: Literal["active", "paused", "completed", "missed", "deleted"] = "active"
    created_at: str
    updated_at: str
    next_run_at: str | None = None
    heartbeat_config: list[HeartbeatCheck] | None = None
