from pydantic import ValidationError

from hypo_agent.core.logging import configure_logging
from hypo_agent.models import (
    Message,
    ModelConfig,
    PersonaConfig,
    ProviderConfig,
    SecretsConfig,
    SecurityConfig,
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
    assert restored.sender == "user"
    assert restored.timestamp == fixed_timestamp
    assert restored.session_id == "session-1"


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


def test_security_config_whitelist_and_circuit_breaker():
    security = SecurityConfig.model_validate(
        {
            "directory_whitelist": {
                "read": ["./docs"],
                "write": ["./logs"],
                "execute": ["./workflows"],
            },
            "circuit_breaker": {
                "tool_level_max_failures": 3,
                "session_level_max_failures": 5,
                "cooldown_seconds": 120,
                "global_kill_switch": False,
            },
        }
    )

    assert security.directory_whitelist.read == ["./docs"]
    assert security.directory_whitelist.write == ["./logs"]
    assert security.directory_whitelist.execute == ["./workflows"]
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


def test_configure_logging_idempotent_and_usable():
    configure_logging()
    configure_logging()

    import structlog

    logger = structlog.get_logger("hypo_agent.test").bind(component="unit")
    assert hasattr(logger, "info")
