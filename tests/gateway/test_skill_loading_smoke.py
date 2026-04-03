from __future__ import annotations

from pathlib import Path

from hypo_agent.channels.probe import ProbeServer
from hypo_agent.core.event_queue import EventQueue
from hypo_agent.core.scheduler import SchedulerService
from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.gateway.app import _register_enabled_skills
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import DirectoryWhitelist
from hypo_agent.security.permission_manager import PermissionManager


class RecordingLogger:
    def __init__(self) -> None:
        self.info_calls: list[tuple[str, dict]] = []

    def info(self, event: str, **kwargs) -> None:
        self.info_calls.append((event, kwargs))

    def warning(self, event: str, **kwargs) -> None:  # pragma: no cover - compatibility
        del event, kwargs


def test_register_enabled_skills_loads_each_enabled_skill_and_logs_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
default_timeout_seconds: 30
skills:
  exec:
    enabled: true
  tmux:
    enabled: false
  code_run:
    enabled: true
  filesystem:
    enabled: true
  agent_search:
    enabled: true
  info:
    enabled: false
  notion:
    enabled: false
  log_inspector:
    enabled: true
  info_reach:
    enabled: true
  export:
    enabled: false
  probe:
    enabled: true
  memory:
    enabled: true
  reminder:
    enabled: true
  email_scanner:
    enabled: true
""".strip(),
        encoding="utf-8",
    )
    (config_dir / "secrets.yaml").write_text("providers: {}\nservices: {}\n", encoding="utf-8")

    skill_manager = SkillManager()
    permission_manager = PermissionManager(
        DirectoryWhitelist(rules=[], default_policy="readonly")
    )
    structured_store = StructuredStore(db_path=tmp_path / "hypo.db")
    scheduler = SchedulerService(structured_store=structured_store, event_queue=EventQueue())
    logger = RecordingLogger()
    monkeypatch.setattr("hypo_agent.gateway.app.logger", logger)

    _register_enabled_skills(
        skill_manager=skill_manager,
        permission_manager=permission_manager,
        structured_store=structured_store,
        scheduler=scheduler,
        message_queue=EventQueue(),
        probe_server=ProbeServer(),
        skills_config_path=config_dir / "skills.yaml",
    )

    assert set(skill_manager._skills) == {
        "exec",
        "code_run",
        "filesystem",
        "agent_search",
        "log_inspector",
        "info_reach",
        "probe",
        "memory",
        "reminder",
        "email_scanner",
    }
    summary_event, summary_payload = logger.info_calls[-1]
    assert summary_event == "skills.registered"
    assert {item["name"] for item in summary_payload["skills"]} == set(skill_manager._skills)
