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


def test_build_default_deps_skips_coder_skill_when_service_config_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
default_timeout_seconds: 30
skills:
  coder:
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
    assert "coder" not in deps.skill_manager._skills
    assert logger.warning_calls == [
        (
            "coder_skill.disabled",
            {
                "reason": (
                    "Missing Hypo-Coder config: config/secrets.yaml -> "
                    "services.hypo_coder.base_url/agent_token/webhook_secret"
                )
            },
        )
    ]
