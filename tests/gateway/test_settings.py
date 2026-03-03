from pathlib import Path

import pytest

from hypo_agent.gateway.settings import load_gateway_settings


def test_load_gateway_settings_reads_auth_token_and_security(tmp_path: Path) -> None:
    security_yaml = tmp_path / "security.yaml"
    security_yaml.write_text(
        """
auth_token: test-token
directory_whitelist:
  read: ["./docs"]
  write: ["./logs"]
  execute: ["./workflows"]
circuit_breaker:
  tool_level_max_failures: 3
  session_level_max_failures: 5
  cooldown_seconds: 120
  global_kill_switch: false
""".strip(),
        encoding="utf-8",
    )

    settings = load_gateway_settings(security_yaml)

    assert settings.auth_token == "test-token"
    assert settings.security.directory_whitelist.read == ["./docs"]
    assert settings.security.circuit_breaker.cooldown_seconds == 120


def test_load_gateway_settings_rejects_missing_token(tmp_path: Path) -> None:
    security_yaml = tmp_path / "security.yaml"
    security_yaml.write_text(
        """
directory_whitelist:
  read: ["./docs"]
  write: ["./logs"]
  execute: ["./workflows"]
circuit_breaker:
  tool_level_max_failures: 3
  session_level_max_failures: 5
  cooldown_seconds: 120
  global_kill_switch: false
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="auth_token"):
        load_gateway_settings(security_yaml)
