from __future__ import annotations

from pathlib import Path

from hypo_agent.core.exec_profiles import ExecProfileRegistry


def test_exec_profile_registry_loads_yaml_profiles(tmp_path: Path) -> None:
    config = tmp_path / "exec_profiles.yaml"
    config.write_text(
        """
profiles:
  git:
    allow_prefixes:
      - "git status"
    deny_prefixes:
      - "git push --force"
""".strip(),
        encoding="utf-8",
    )

    registry = ExecProfileRegistry.from_yaml(config)

    decision = registry.evaluate("git status --short", profile_name="git")
    assert decision.allowed is True
    assert decision.profile_name == "git"


def test_exec_profile_registry_blocks_deny_prefix_before_allow(tmp_path: Path) -> None:
    config = tmp_path / "exec_profiles.yaml"
    config.write_text(
        """
profiles:
  git:
    allow_prefixes:
      - "git push"
    deny_prefixes:
      - "git push --force"
""".strip(),
        encoding="utf-8",
    )

    registry = ExecProfileRegistry.from_yaml(config)

    decision = registry.evaluate("git push --force origin main", profile_name="git")
    assert decision.allowed is False
    assert "deny prefix" in decision.reason


def test_exec_profile_registry_unknown_profile_falls_back_to_default(tmp_path: Path) -> None:
    config = tmp_path / "exec_profiles.yaml"
    config.write_text(
        """
profiles:
  default:
    allow_prefixes: ["*"]
    deny_prefixes:
      - "shutdown"
""".strip(),
        encoding="utf-8",
    )

    registry = ExecProfileRegistry.from_yaml(config)

    allowed = registry.evaluate("echo hello", profile_name="missing")
    denied = registry.evaluate("shutdown now", profile_name="missing")

    assert allowed.allowed is True
    assert allowed.profile_name == "default"
    assert denied.allowed is False
