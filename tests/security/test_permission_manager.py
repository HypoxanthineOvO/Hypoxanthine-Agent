from __future__ import annotations

from pathlib import Path

from hypo_agent.models import DirectoryWhitelist, WhitelistRule
from hypo_agent.security import permission_manager as permission_module
from hypo_agent.security.permission_manager import PermissionManager


def _manager(base_dir: Path) -> PermissionManager:
    projects = base_dir / "projects"
    docs = base_dir / "documents"
    sandbox = base_dir / "sandbox"
    projects.mkdir(parents=True, exist_ok=True)
    docs.mkdir(parents=True, exist_ok=True)
    sandbox.mkdir(parents=True, exist_ok=True)

    whitelist = DirectoryWhitelist(
        rules=[
            WhitelistRule(path=str(projects), permissions=["read", "write", "execute"]),
            WhitelistRule(path=str(docs), permissions=["read"]),
            WhitelistRule(path=str(sandbox), permissions=["read", "write", "execute"]),
        ],
        default_policy="readonly",
    )
    return PermissionManager(whitelist)


def test_permission_manager_allows_operations_inside_whitelist(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    inside = tmp_path / "projects" / "app.py"

    allowed_read, _ = manager.check_permission(str(inside), "read")
    allowed_write, _ = manager.check_permission(str(inside), "write")
    allowed_exec, _ = manager.check_permission(str(inside), "execute")

    assert allowed_read is True
    assert allowed_write is True
    assert allowed_exec is True


def test_permission_manager_applies_rule_specific_permissions(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    docs_file = tmp_path / "documents" / "notes.txt"

    allowed_read, _ = manager.check_permission(str(docs_file), "read")
    denied_write, reason = manager.check_permission(str(docs_file), "write")

    assert allowed_read is True
    assert denied_write is False
    assert "not allowed" in reason.lower()


def test_permission_manager_allows_readonly_outside_whitelist(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    outside = tmp_path / "outside" / "history.log"

    allowed_read, _ = manager.check_permission(str(outside), "read")
    denied_write, write_reason = manager.check_permission(str(outside), "write")
    denied_exec, exec_reason = manager.check_permission(str(outside), "execute")

    assert allowed_read is True
    assert denied_write is False
    assert "readonly" in write_reason.lower()
    assert denied_exec is False
    assert "readonly" in exec_reason.lower()


def test_permission_manager_denies_path_traversal_after_resolve(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    traversed = tmp_path / "projects" / ".." / "outside.txt"

    allowed, reason = manager.check_permission(str(traversed), "write")

    assert allowed is False
    assert "outside whitelist" in reason.lower()


def test_permission_manager_follows_symlink_and_denies_escape(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    inside = tmp_path / "projects"
    outside = tmp_path / "private"
    outside.mkdir(parents=True, exist_ok=True)
    target = outside / "secret.txt"
    target.write_text("secret", encoding="utf-8")
    link = inside / "secret-link.txt"
    link.symlink_to(target)

    allowed_read, _ = manager.check_permission(str(link), "read")
    allowed_write, reason = manager.check_permission(str(link), "write")

    assert allowed_read is True
    assert allowed_write is False
    assert "outside whitelist" in reason.lower()


def test_permission_manager_emits_allowed_and_denied_logs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class LogRecorder:
        def __init__(self) -> None:
            self.events: list[tuple[str, str, dict]] = []

        def info(self, event: str, **kwargs) -> None:
            self.events.append(("info", event, kwargs))

        def warning(self, event: str, **kwargs) -> None:
            self.events.append(("warning", event, kwargs))

    recorder = LogRecorder()
    monkeypatch.setattr(permission_module, "logger", recorder)
    manager = _manager(tmp_path)

    manager.check_permission(str(tmp_path / "projects" / "allowed.txt"), "read")
    manager.check_permission(str(tmp_path / "outside" / "denied.txt"), "write")

    event_names = [item[1] for item in recorder.events]
    assert "permission.check.allowed" in event_names
    assert "permission.check.denied" in event_names
