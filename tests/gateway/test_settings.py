from pathlib import Path

import pytest

from hypo_agent.gateway.settings import load_gateway_settings


def test_load_gateway_settings_reads_auth_token_and_security(tmp_path: Path) -> None:
    security_yaml = tmp_path / "security.yaml"
    security_yaml.write_text(
        """
auth_token: test-token
directory_whitelist:
  rules:
    - path: "./docs"
      permissions: [read]
    - path: "./logs"
      permissions: [read, write]
    - path: "./workflows"
      permissions: [execute]
  default_policy: readonly
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
    assert settings.security.directory_whitelist.default_policy == "readonly"
    assert settings.security.directory_whitelist.rules[0].path == "./docs"
    assert settings.security.circuit_breaker.cooldown_seconds == 120


def test_load_gateway_settings_rejects_missing_token(tmp_path: Path) -> None:
    security_yaml = tmp_path / "security.yaml"
    security_yaml.write_text(
        """
directory_whitelist:
  rules:
    - path: "./docs"
      permissions: [read]
  default_policy: readonly
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


def test_load_gateway_settings_expands_hypo_agent_root_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "Hypo-Agent"
    repo_root.mkdir(parents=True)
    monkeypatch.setenv("HYPO_AGENT_ROOT", str(repo_root))

    security_yaml = tmp_path / "security.yaml"
    security_yaml.write_text(
        """
auth_token: test-token
directory_whitelist:
  rules:
    - path: "${HYPO_AGENT_ROOT}"
      permissions: [read]
    - path: "${HYPO_AGENT_ROOT}/config"
      permissions: [read, write]
    - path: "${HYPO_AGENT_ROOT}/memory"
      permissions: [read, write]
  default_policy: readonly
circuit_breaker:
  tool_level_max_failures: 3
  session_level_max_failures: 5
  cooldown_seconds: 120
  global_kill_switch: false
""".strip(),
        encoding="utf-8",
    )

    settings = load_gateway_settings(security_yaml)

    assert settings.security.directory_whitelist.rules[0].path == str(repo_root)
    assert settings.security.directory_whitelist.rules[1].path == str(repo_root / "config")
    assert settings.security.directory_whitelist.rules[2].path == str(repo_root / "memory")
