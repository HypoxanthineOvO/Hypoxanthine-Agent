from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from hypo_agent.core.time_utils import normalize_utc_datetime, utc_isoformat, utc_now


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["image", "file", "audio", "video"]
    url: str
    filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    image: str | None = Field(default=None, json_schema_extra={"deprecated": True})
    file: str | None = Field(default=None, json_schema_extra={"deprecated": True})
    audio: str | None = Field(default=None, json_schema_extra={"deprecated": True})
    attachments: list[Attachment] = Field(default_factory=list)
    sender: str
    message_tag: Literal["reminder", "heartbeat", "email_scan", "tool_status", "subscription", "hypo_info"] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime | None = Field(default_factory=utc_now)
    session_id: str
    channel: str = "webui"
    sender_id: str | None = None

    @field_validator("timestamp", mode="after")
    @classmethod
    def _normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return normalize_utc_datetime(value)

    @field_serializer("timestamp", when_used="json")
    def _serialize_timestamp(self, value: datetime | None) -> str | None:
        return utc_isoformat(value)


class SkillOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "error", "partial", "timeout", "fused"]
    result: Any = None
    error_info: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    attachments: list[Attachment] = Field(default_factory=list)


class SingleModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["chat", "embedding", "vision", "audio", "rerank"] = "chat"
    provider: str | None = None
    litellm_model: str | None = None
    fallback: str | None = None
    supports_tool_calling: bool | None = None
    context_window: int | None = None
    description: str | None = None
    reasoning_config: dict[str, str] = Field(default_factory=dict)


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
    napcat_ws_token: str = ""
    napcat_http_url: str
    napcat_http_token: str | None = None
    bot_qq: str
    allowed_users: list[str] = Field(default_factory=list)


class QQBotServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str = ""
    app_secret: str = ""
    enabled: bool = False
    public_base_url: str = ""
    markdown_mode: Literal["native", "template", "disabled"] = "native"
    markdown_template_id: str = ""


class WeixinServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    token_path: str = "memory/weixin_auth.json"
    allowed_users: list[str] = Field(default_factory=list)
    markdown_enabled: bool = True


class WeWeRSSServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    base_url: str = "http://10.15.88.94:4000"
    auth_code: str = ""
    login_timeout_seconds: int = Field(default=180, ge=10)
    poll_interval_seconds: int = Field(default=3, ge=1)


class BilibiliServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cookie: str = ""


class WeiboServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cookie: str = ""


class ZhihuServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cookie: str = ""


class WeReadServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cookie: str = ""


class FeishuServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str
    app_secret: str


class TavilyServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str


class HypoInfoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str


class HypoCoderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    agent_token: str
    webhook_secret: str
    webhook_url: str = ""
    incremental_output_enabled: bool = False


class ProbeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str
    screenshot_dir: str = "memory/probe_screenshots"


class NotionServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    integration_secret: str
    default_workspace: str = ""
    todo_database_id: str = ""
    proxy_url: str = ""


class ServicesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bilibili: BilibiliServiceConfig | None = None
    email: EmailServiceConfig | None = None
    feishu: FeishuServiceConfig | None = None
    hypo_coder: HypoCoderConfig | None = None
    hypo_info: HypoInfoConfig | None = None
    notion: NotionServiceConfig | None = None
    probe: ProbeConfig | None = None
    qq: QQServiceConfig | None = None
    qq_bot: QQBotServiceConfig | None = None
    wewe_rss: WeWeRSSServiceConfig | None = None
    weixin: WeixinServiceConfig | None = None
    tavily: TavilyServiceConfig | None = None
    weibo: WeiboServiceConfig | None = None
    weread: WeReadServiceConfig | None = None
    zhihu: ZhihuServiceConfig | None = None


class SecretsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    services: ServicesConfig | None = None


class TaskScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    mode: Literal["interval", "cron"] = "interval"
    cron: str | None = None
    interval_minutes: int = Field(default=10, ge=1)
    max_rounds: int | None = Field(default=None, ge=1)
    time: str | None = None

    @model_validator(mode="after")
    def _validate_schedule(self) -> "TaskScheduleConfig":
        if self.mode == "cron" and not str(self.cron or "").strip():
            raise ValueError("cron is required when heartbeat mode is 'cron'")
        return self


class HeartbeatTaskConfig(TaskScheduleConfig):
    model_config = ConfigDict(extra="forbid")

    notion_today_match_mode: Literal["cover_today", "due_only"] = "cover_today"


class HypoInfoDigestTaskConfig(TaskScheduleConfig):
    model_config = ConfigDict(extra="forbid")

    time: str | None = None


class EmailStoreTaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_entries: int = Field(default=5000, ge=1)
    retention_days: int = Field(default=90, ge=1)
    warmup_hours: int = Field(default=168, ge=1)


class TasksConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    heartbeat: HeartbeatTaskConfig = Field(default_factory=HeartbeatTaskConfig)
    email_store: EmailStoreTaskConfig = Field(default_factory=EmailStoreTaskConfig)
    hypo_info_digest: HypoInfoDigestTaskConfig = Field(default_factory=HypoInfoDigestTaskConfig)
    subscription: TaskScheduleConfig = Field(default_factory=TaskScheduleConfig)
    wewe_rss: TaskScheduleConfig = Field(default_factory=TaskScheduleConfig)


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
    skill_level_enabled: bool = False
    skill_level_max_failures: int | None = None


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
    system_prompt_template: str = ""

    @field_validator("personality", mode="before")
    @classmethod
    def _normalize_personality(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            normalized: list[str] = []
            for raw_line in value.splitlines():
                item = raw_line.strip()
                if not item:
                    continue
                item = item.rstrip("；;").strip()
                if item:
                    normalized.append(item)
            return normalized
        return [str(value).strip()] if str(value).strip() else []


class NarrationToolLevels(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heavy: list[str] = Field(default_factory=list)
    medium: list[str] = Field(default_factory=list)


class NarrationToolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: str
    fallback: str | None = None


class NarrationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    model: str = "lightweight"
    tool_levels: NarrationToolLevels = Field(default_factory=NarrationToolLevels)
    tool_narration: dict[str, NarrationToolConfig] = Field(default_factory=dict)
    llm_timeout_ms: int = Field(default=500, ge=1)
    llm_repeat_threshold: int = Field(default=3, ge=1)
    dedup_max_consecutive: int = Field(default=2, ge=1)
    debounce_seconds: float = Field(default=2.0, ge=0.0)
    max_narration_length: int = Field(default=80, ge=1)


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
