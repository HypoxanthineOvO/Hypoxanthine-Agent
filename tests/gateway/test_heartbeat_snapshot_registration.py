from __future__ import annotations

from pathlib import Path

from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.gateway.app import _build_default_pipeline, _register_enabled_skills
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore
from hypo_agent.models import DirectoryWhitelist
from hypo_agent.security.permission_manager import PermissionManager


def test_register_enabled_skills_registers_heartbeat_snapshot(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "skills.yaml").write_text(
        """
default_timeout_seconds: 30
skills:
  heartbeat_snapshot:
    enabled: true
""".strip(),
        encoding="utf-8",
    )

    skill_manager = SkillManager()
    permission_manager = PermissionManager(
        DirectoryWhitelist(rules=[], default_policy="readonly")
    )

    _register_enabled_skills(
        skill_manager=skill_manager,
        permission_manager=permission_manager,
        skills_config_path=config_dir / "skills.yaml",
    )

    assert "heartbeat_snapshot" in skill_manager._skills


def test_build_default_pipeline_uses_snapshot_tools_for_heartbeat(tmp_path: Path, monkeypatch) -> None:
    import hypo_agent.gateway.app as app_module

    deps = app_module.AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        skill_manager=SkillManager(),
    )
    monkeypatch.setattr(app_module, "get_memory_dir", lambda: tmp_path / "memory")

    pipeline = _build_default_pipeline(deps)

    assert pipeline.heartbeat_allowed_tools == {
        "get_system_snapshot",
        "get_mail_snapshot",
        "get_notion_todo_snapshot",
        "get_reminder_snapshot",
        "get_heartbeat_snapshot",
        "get_recent_logs",
        "get_tool_history",
        "get_error_summary",
        "get_session_history",
        "search_sop",
    }
