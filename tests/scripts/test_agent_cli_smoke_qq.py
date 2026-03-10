from __future__ import annotations

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
