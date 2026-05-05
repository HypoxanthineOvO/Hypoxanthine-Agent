from __future__ import annotations

from pathlib import Path


def test_hypo_agent_send_skill_wrappers_exist_without_hardcoded_token() -> None:
    paths = [
        Path(".codex/skills/hypo-agent-send/SKILL.md"),
        Path(".claude/skills/hypo-agent-send/SKILL.md"),
        Path(".opencode/commands/send-to-hyx.md"),
    ]

    for path in paths:
        assert path.exists(), path
        text = path.read_text(encoding="utf-8")
        assert "hypo-agent send" in text
        assert "HYPO_AGENT_TOKEN" in text
        assert "dev-token-change-me" not in text
        assert "secret-token" not in text
