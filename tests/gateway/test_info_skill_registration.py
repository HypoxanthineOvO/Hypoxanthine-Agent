from __future__ import annotations

from pathlib import Path

from hypo_agent.gateway.app import _build_default_deps
from hypo_agent.models import SecurityConfig


class RecordingLogger:
    def __init__(self) -> None:
        self.warning_calls: list[tuple[str, dict]] = []

    def warning(self, event: str, **kwargs) -> None:
        self.warning_calls.append((event, kwargs))

    def info(self, event: str, **kwargs) -> None:  # pragma: no cover - compatibility
        del event, kwargs


def test_build_default_deps_skips_info_skill_when_service_config_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
default_timeout_seconds: 30
skills:
  info:
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text("providers: {}\nservices: {}\n", encoding="utf-8")

    security = SecurityConfig.model_validate(
        {
            "directory_whitelist": {"rules": [], "default_policy": "readonly"},
            "circuit_breaker": {},
        }
    )
    logger = RecordingLogger()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hypo_agent.gateway.app.logger", logger)

    deps = _build_default_deps(security)

    assert deps.skill_manager is not None
    assert "info" not in deps.skill_manager._skills
    assert logger.warning_calls == [
        (
            "info_skill.disabled",
            {
                "reason": (
                    "Missing Hypo-Info config: config/secrets.yaml -> "
                    "services.hypo_info.base_url"
                )
            },
        )
    ]


def test_build_default_deps_registers_info_reach_with_default_hypo_info_base_url(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
default_timeout_seconds: 30
skills:
  info_reach:
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text("providers: {}\nservices: {}\n", encoding="utf-8")

    security = SecurityConfig.model_validate(
        {
            "directory_whitelist": {"rules": [], "default_policy": "readonly"},
            "circuit_breaker": {},
        }
    )
    monkeypatch.chdir(tmp_path)

    deps = _build_default_deps(security)

    assert deps.skill_manager is not None
    skill = deps.skill_manager._skills["info_reach"]
    assert skill._client._base_url == "http://localhost:8200"


def test_build_default_deps_registers_info_reach_with_secrets_hypo_info_base_url(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
default_timeout_seconds: 30
skills:
  info_reach:
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text(
        """
providers: {}
services:
  hypo_info:
    base_url: "http://localhost:9100"
""".strip(),
        encoding="utf-8",
    )

    security = SecurityConfig.model_validate(
        {
            "directory_whitelist": {"rules": [], "default_policy": "readonly"},
            "circuit_breaker": {},
        }
    )
    monkeypatch.chdir(tmp_path)

    deps = _build_default_deps(security)

    assert deps.skill_manager is not None
    skill = deps.skill_manager._skills["info_reach"]
    assert skill._client._base_url == "http://localhost:9100"


def test_build_default_deps_registers_info_reach_before_info_when_both_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
default_timeout_seconds: 30
skills:
  info:
    enabled: true
  info_reach:
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text(
        """
providers: {}
services:
  hypo_info:
    base_url: "http://localhost:9100"
""".strip(),
        encoding="utf-8",
    )

    security = SecurityConfig.model_validate(
        {
            "directory_whitelist": {"rules": [], "default_policy": "readonly"},
            "circuit_breaker": {},
        }
    )
    monkeypatch.chdir(tmp_path)

    deps = _build_default_deps(security)

    assert deps.skill_manager is not None
    assert list(deps.skill_manager._skills.keys())[:2] == ["info_reach", "info"]


def test_build_default_deps_registers_info_runtime_key_with_info_portal_skill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
default_timeout_seconds: 30
skills:
  info:
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text(
        """
providers: {}
services:
  hypo_info:
    base_url: "http://localhost:9100"
""".strip(),
        encoding="utf-8",
    )

    security = SecurityConfig.model_validate(
        {
            "directory_whitelist": {"rules": [], "default_policy": "readonly"},
            "circuit_breaker": {},
        }
    )
    monkeypatch.chdir(tmp_path)

    deps = _build_default_deps(security)

    assert deps.skill_manager is not None
    skill = deps.skill_manager._skills["info"]
    assert skill.__class__.__name__ == "InfoPortalSkill"
