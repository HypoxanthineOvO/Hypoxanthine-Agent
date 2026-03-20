from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys


def _load_agent_cli_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "agent_cli.py"
    spec = importlib.util.spec_from_file_location("agent_cli_module", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_agent_cli_mock_case_rejects_non_whitelist_user() -> None:
    module = _load_agent_cli_module()
    result = module._case_qq_non_whitelist_user_mock()

    assert result.status.value == "PASS"


def test_agent_cli_mock_case_calls_send_private_msg_api() -> None:
    module = _load_agent_cli_module()
    result = module._case_qq_send_private_api_mock()

    assert result.status.value == "PASS"


def test_agent_cli_email_scan_case_skips_when_interval_not_smoke_sized() -> None:
    module = _load_agent_cli_module()

    class DummySmoke:
        async def wait_for_tag(self, tag: str, timeout: int):
            raise AssertionError(f"wait_for_tag should not be called for {tag} / {timeout}")

    result = asyncio.run(
        module._case_email_scan_trigger(
            DummySmoke(),
            {"email_scan": {"enabled": True, "interval_minutes": 60}},
        )
    )

    assert result.status.value == "SKIP"
    assert "interval_minutes=60" in result.detail


def test_agent_cli_agent_search_case_skips_when_tavily_not_configured(monkeypatch) -> None:
    module = _load_agent_cli_module()
    monkeypatch.setattr(module, "_has_tavily_api_key", lambda config_path=module.Path("config/secrets.yaml"): False)

    class DummySmoke:
        async def send(self, text: str) -> None:
            raise AssertionError(f"send should not be called: {text}")

        async def wait_for_assistant_done(self, timeout: int = 30):
            raise AssertionError(f"wait_for_assistant_done should not be called: {timeout}")

    result = asyncio.run(module._case_agent_search_tool(DummySmoke()))

    assert result.status.value == "SKIP"
    assert "tavily" in result.detail.lower()


def test_agent_cli_smoke_refuses_production_port_in_test_mode(monkeypatch, capsys) -> None:
    module = _load_agent_cli_module()
    monkeypatch.setenv("HYPO_TEST_MODE", "1")
    monkeypatch.setattr(module, "_load_token", lambda: "test-token")

    result = asyncio.run(module.cmd_smoke(port=8765, session_id="main"))

    captured = capsys.readouterr()
    assert result == 2
    assert "请先停止生产进程或确认隔离" in captured.out


def test_agent_cli_smoke_refuses_when_8765_is_listening(monkeypatch, capsys) -> None:
    module = _load_agent_cli_module()
    monkeypatch.setenv("HYPO_TEST_MODE", "1")
    monkeypatch.setattr(module, "_load_token", lambda: "test-token")
    monkeypatch.setattr(module, "_port_is_listening", lambda host, port, timeout=1.0: port == 8765)

    result = asyncio.run(module.cmd_smoke(port=8766, session_id="main"))

    captured = capsys.readouterr()
    assert result == 2
    assert "请先停止生产进程或确认隔离" in captured.out
