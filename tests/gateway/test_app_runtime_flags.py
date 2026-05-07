from __future__ import annotations

from hypo_agent.gateway.app import _nonblocking_runtime_enabled_from_env


def test_nonblocking_runtime_is_enabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("HYPO_NONBLOCKING_RUNTIME", raising=False)

    assert _nonblocking_runtime_enabled_from_env() is True


def test_nonblocking_runtime_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("HYPO_NONBLOCKING_RUNTIME", "0")

    assert _nonblocking_runtime_enabled_from_env() is False


def test_nonblocking_runtime_accepts_legacy_enabled_value(monkeypatch) -> None:
    monkeypatch.setenv("HYPO_NONBLOCKING_RUNTIME", "1")

    assert _nonblocking_runtime_enabled_from_env() is True
