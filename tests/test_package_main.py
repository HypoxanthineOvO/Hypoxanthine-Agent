import runpy
import sys
from unittest.mock import patch


def test_python_m_hypo_agent_forwards_cli_port(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["python", "--host", "127.0.0.1", "--port", "9998"])

    with patch("hypo_agent.gateway.main.run") as mock_run:
        runpy.run_module("hypo_agent", run_name="__main__")

    mock_run.assert_called_once_with(host="127.0.0.1", port=9998)
