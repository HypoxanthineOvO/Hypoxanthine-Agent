from __future__ import annotations

from pathlib import Path


def test_c1_acceptance_runbook_defines_local_and_opt_in_real_gates() -> None:
    path = Path("docs/runbooks/c1-channel-first-acceptance.md")
    content = path.read_text(encoding="utf-8")

    assert "Default Local Gates" in content
    assert "Optional Real Channel Smoke" in content
    assert "opt-in only" in content
    assert "QQ Bot" in content
    assert "Weixin" in content
    assert "Feishu" in content
    assert "M4 webpage reading was deferred" in content
