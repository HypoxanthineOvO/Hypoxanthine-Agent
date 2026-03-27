from pathlib import Path

from pydantic import ValidationError
import yaml

from hypo_agent.core.logging import configure_logging
from hypo_agent.models import (
    Attachment,
    HeartbeatCheck,
    Message,
    ModelConfig,
    PersonaConfig,
    ProviderConfig,
    ReminderCreate,
    SecretsConfig,
    SecurityConfig,
    SingleModelConfig,
    SkillOutput,
)


def test_message_round_trip_serialization(fixed_timestamp):
    message = Message(
        text="hello",
        sender="user",
        timestamp=fixed_timestamp,
        session_id="session-1",
    )

    payload = message.model_dump_json()
    restored = Message.model_validate_json(payload)

    assert restored.text == "hello"
    assert restored.image is None
    assert restored.file is None
    assert restored.audio is None
    assert restored.message_tag is None
    assert restored.channel == "webui"
    assert restored.sender_id is None
    assert restored.sender == "user"
    assert restored.timestamp == fixed_timestamp
    assert restored.session_id == "session-1"


def test_attachment_round_trip_serialization() -> None:
    attachment = Attachment(
        type="image",
        url="/tmp/example.png",
        filename="example.png",
        mime_type="image/png",
        size_bytes=1234,
    )

    payload = attachment.model_dump()
    restored = Attachment.model_validate(payload)

    assert restored.type == "image"
    assert restored.url == "/tmp/example.png"
    assert restored.filename == "example.png"
    assert restored.mime_type == "image/png"
    assert restored.size_bytes == 1234


def test_message_serializes_attachments_and_backfills_legacy_payload(fixed_timestamp) -> None:
    message = Message(
        text="describe this",
        sender="user",
        timestamp=fixed_timestamp,
        session_id="session-vision",
        attachments=[
            Attachment(
                type="image",
                url="/tmp/cat.png",
                filename="cat.png",
                mime_type="image/png",
                size_bytes=42,
            )
        ],
    )

    payload = message.model_dump_json()
    restored = Message.model_validate_json(payload)
    legacy = Message.model_validate(
        {
            "text": "legacy",
            "sender": "user",
            "timestamp": fixed_timestamp,
            "session_id": "session-legacy",
        }
    )

    assert len(restored.attachments) == 1
    assert restored.attachments[0].type == "image"
    assert restored.attachments[0].filename == "cat.png"
    assert legacy.attachments == []


def test_message_accepts_optional_message_tag(fixed_timestamp):
    message = Message(
        text="提醒：开会",
        sender="assistant",
        timestamp=fixed_timestamp,
        session_id="main",
        message_tag="reminder",
    )

    payload = message.model_dump_json()
    restored = Message.model_validate_json(payload)
    assert restored.message_tag == "reminder"


def test_message_accepts_tool_status_tag_and_metadata(fixed_timestamp):
    message = Message(
        text="正在创建提醒",
        sender="assistant",
        timestamp=fixed_timestamp,
        session_id="main",
        message_tag="tool_status",
        metadata={"ephemeral": True},
    )
    payload = message.model_dump_json()
    restored = Message.model_validate_json(payload)
    assert restored.message_tag == "tool_status"
    assert restored.metadata["ephemeral"] is True


def test_message_accepts_email_scan_tag(fixed_timestamp):
    message = Message(
        text="邮件扫描完成",
        sender="assistant",
        timestamp=fixed_timestamp,
        session_id="main",
        message_tag="email_scan",
    )

    payload = message.model_dump_json()
    restored = Message.model_validate_json(payload)
    assert restored.message_tag == "email_scan"


def test_skill_output_serializes_attachments() -> None:
    output = SkillOutput(
        status="success",
        result="/tmp/export.pdf",
        attachments=[
            Attachment(
                type="file",
                url="/tmp/export.pdf",
                filename="export.pdf",
                mime_type="application/pdf",
                size_bytes=128,
            )
        ],
    )

    payload = output.model_dump_json()
    restored = SkillOutput.model_validate_json(payload)

    assert restored.attachments[0].filename == "export.pdf"


def test_message_accepts_qq_channel_and_sender_id(fixed_timestamp):
    message = Message(
        text="你好",
        sender="user",
        timestamp=fixed_timestamp,
        session_id="main",
        channel="qq",
        sender_id="123456",
    )

    payload = message.model_dump_json()
    restored = Message.model_validate_json(payload)

    assert restored.channel == "qq"
    assert restored.sender_id == "123456"


def test_reminder_models_validate_once_and_heartbeat_checks():
    heartbeat = HeartbeatCheck(
        check_type="http_status",
        target="https://example.com/health",
        expected=200,
    )
    reminder = ReminderCreate(
        title="生产巡检",
        description="检查服务状态",
        schedule_type="once",
        schedule_value="2026-03-08T15:00:00+08:00",
        heartbeat_config=[heartbeat],
    )

    assert reminder.schedule_type == "once"
    assert reminder.channel == "all"
    assert reminder.heartbeat_config[0].check_type == "http_status"


def test_skill_output_status_validation():
    output = SkillOutput(status="success", result={"ok": True})
    assert output.status == "success"
    assert output.result == {"ok": True}

    try:
        SkillOutput(status="invalid", result=None)
        assert False, "invalid status should raise ValidationError"
    except ValidationError:
        pass


def test_model_config_defaults_and_mapping():
    config = ModelConfig(
        default_model="Gemini3Pro",
        task_routing={"chat": "Gemini3Pro", "lightweight": "DeepseekV3_2"},
        models={
            "Gemini3Pro": {
                "provider": "Hiapi",
                "litellm_model": "openai/gemini-2.5-pro",
                "fallback": "DeepseekV3_2",
                "supports_tool_calling": True,
                "context_window": 32768,
                "description": "Gemini model",
            },
            "ClaudeSonnet": {
                "provider": None,
                "litellm_model": None,
                "fallback": "Gemini3Pro",
            },
        },
    )

    data = config.model_dump()
    restored = ModelConfig.model_validate(data)

    assert restored.default_model == "Gemini3Pro"
    assert restored.task_routing["chat"] == "Gemini3Pro"
    assert restored.task_routing["lightweight"] == "DeepseekV3_2"
    assert restored.models["ClaudeSonnet"].provider is None
    assert restored.models["Gemini3Pro"].supports_tool_calling is True
    assert restored.models["Gemini3Pro"].context_window == 32768


def test_provider_config_round_trip():
    provider = ProviderConfig(
        api_base="https://hiapi.online/v1",
        api_key="sk-test",
    )

    data = provider.model_dump()
    restored = ProviderConfig.model_validate(data)

    assert restored.api_base == "https://hiapi.online/v1"
    assert restored.api_key == "sk-test"


def test_secrets_config_round_trip():
    config = SecretsConfig(
        providers={
            "Hiapi": {
                "api_base": "https://hiapi.online/v1",
                "api_key": "sk-hiapi",
            }
        }
    )

    data = config.model_dump()
    restored = SecretsConfig.model_validate(data)

    assert restored.providers["Hiapi"].api_base == "https://hiapi.online/v1"


def test_secrets_config_accepts_services_email_accounts():
    config = SecretsConfig.model_validate(
        {
            "providers": {},
            "services": {
                "email": {
                    "accounts": [
                        {
                            "name": "主邮箱",
                            "host": "imap.example.com",
                            "port": 993,
                            "username": "ops@example.com",
                            "password": "secret",
                        }
                    ]
                }
            },
        }
    )

    assert config.services is not None
    assert config.services.email is not None
    assert config.services.email.accounts[0].name == "主邮箱"


def test_secrets_config_accepts_services_qq():
    config = SecretsConfig.model_validate(
        {
            "providers": {},
            "services": {
                "qq": {
                    "napcat_ws_url": "ws://localhost:3001",
                    "napcat_ws_token": "ws-token-xyz",
                    "napcat_http_url": "http://localhost:3000",
                    "napcat_http_token": "token-abc",
                    "bot_qq": "123456789",
                    "allowed_users": ["10001"],
                }
            },
        }
    )

    assert config.services is not None
    assert config.services.qq is not None
    assert config.services.qq.bot_qq == "123456789"
    assert config.services.qq.napcat_ws_token == "ws-token-xyz"
    assert config.services.qq.napcat_http_token == "token-abc"
    assert config.services.qq.allowed_users == ["10001"]


def test_secrets_config_accepts_services_qq_bot() -> None:
    config = SecretsConfig.model_validate(
        {
            "providers": {},
            "services": {
                "qq_bot": {
                    "app_id": "1029384756",
                    "app_secret": "bot-secret-xyz",
                    "enabled": True,
                }
            },
        }
    )

    assert config.services is not None
    assert config.services.qq_bot is not None
    assert config.services.qq_bot.app_id == "1029384756"
    assert config.services.qq_bot.app_secret == "bot-secret-xyz"
    assert config.services.qq_bot.enabled is True


def test_secrets_config_accepts_services_weixin() -> None:
    config = SecretsConfig.model_validate(
        {
            "providers": {},
            "services": {
                "weixin": {
                    "enabled": True,
                    "token_path": "memory/weixin_auth.json",
                    "allowed_users": ["alice@im.wechat"],
                }
            },
        }
    )

    assert config.services is not None
    assert config.services.weixin is not None
    assert config.services.weixin.enabled is True
    assert config.services.weixin.token_path == "memory/weixin_auth.json"
    assert config.services.weixin.allowed_users == ["alice@im.wechat"]


def test_secrets_config_accepts_services_tavily():
    config = SecretsConfig.model_validate(
        {
            "providers": {},
            "services": {
                "tavily": {
                    "api_key": "tvly-dev-key",
                }
            },
        }
    )

    assert config.services is not None
    assert config.services.tavily is not None
    assert config.services.tavily.api_key == "tvly-dev-key"


def test_secrets_yaml_example_includes_qq_template() -> None:
    example_path = Path(__file__).resolve().parents[1] / "config" / "secrets.yaml.example"
    payload = yaml.safe_load(example_path.read_text(encoding="utf-8"))

    config = SecretsConfig.model_validate(payload)

    assert config.services is not None
    assert config.services.qq is not None
    assert config.services.qq.napcat_ws_url == "ws://127.0.0.1:3009/onebot/v11/ws"
    assert config.services.qq.napcat_ws_token == ""
    assert config.services.qq.bot_qq == "123456789"
    assert config.services.qq.allowed_users == ["10001"]
    assert config.services.qq_bot is not None
    assert config.services.qq_bot.enabled is False
    assert config.services.qq_bot.app_id == ""
    assert config.services.weixin is not None
    assert config.services.weixin.enabled is False
    assert config.services.weixin.token_path == "memory/weixin_auth.json"
    assert config.services.weixin.allowed_users == []
    assert config.services.tavily is not None
    assert config.services.tavily.api_key == "PLACEHOLDER_TAVILY_API_KEY"


def test_security_config_whitelist_and_circuit_breaker():
    security = SecurityConfig.model_validate(
        {
            "directory_whitelist": {
                "rules": [
                    {"path": "./docs", "permissions": ["read"]},
                    {"path": "./logs", "permissions": ["read", "write"]},
                    {"path": "./workflows", "permissions": ["execute"]},
                ],
                "default_policy": "readonly",
                "blocked_paths": ["/etc/passwd"],
            },
            "circuit_breaker": {
                "tool_level_max_failures": 3,
                "session_level_max_failures": 5,
                "cooldown_seconds": 120,
                "global_kill_switch": False,
            },
        }
    )

    assert security.directory_whitelist.default_policy == "readonly"
    assert security.directory_whitelist.rules[0].path == "./docs"
    assert security.directory_whitelist.rules[0].permissions == ["read"]
    assert security.directory_whitelist.rules[1].permissions == ["read", "write"]
    assert security.directory_whitelist.blocked_paths == ["/etc/passwd"]
    assert security.circuit_breaker.tool_level_max_failures == 3
    assert security.circuit_breaker.session_level_max_failures == 5
    assert security.circuit_breaker.cooldown_seconds == 120
    assert security.circuit_breaker.global_kill_switch is False


def test_persona_config_required_fields():
    persona = PersonaConfig.model_validate(
        {
            "name": "Hypo",
            "aliases": ["hypo", "assistant"],
            "personality": ["pragmatic", "concise"],
            "speaking_style": {"tone": "direct", "language": "zh-CN"},
        }
    )

    assert persona.name == "Hypo"
    assert persona.aliases == ["hypo", "assistant"]
    assert "pragmatic" in persona.personality
    assert persona.speaking_style["tone"] == "direct"


def test_single_model_config_defaults_to_chat_type() -> None:
    config = SingleModelConfig.model_validate(
        {
            "provider": "volcano",
            "litellm_model": "openai/doubao-embedding-text-240715",
        }
    )

    assert config.type == "chat"


def test_configure_logging_idempotent_and_usable():
    configure_logging()
    configure_logging()

    import structlog

    logger = structlog.get_logger("hypo_agent.test").bind(component="unit")
    assert hasattr(logger, "info")
