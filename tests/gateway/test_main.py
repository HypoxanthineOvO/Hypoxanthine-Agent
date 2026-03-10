from types import SimpleNamespace
from unittest.mock import patch

from hypo_agent.gateway.main import run


@patch("hypo_agent.gateway.main.uvicorn.run")
@patch("hypo_agent.gateway.main.create_app")
@patch("hypo_agent.gateway.main.load_gateway_settings")
def test_run_starts_uvicorn_with_loaded_settings(
    mock_load_gateway_settings, mock_create_app, mock_uvicorn_run
) -> None:
    mock_load_gateway_settings.return_value = SimpleNamespace(
        auth_token="test-token",
        security=SimpleNamespace(),
    )
    mock_app = object()
    mock_create_app.return_value = mock_app

    run(host="127.0.0.1", port=9999)

    mock_load_gateway_settings.assert_called_once_with()
    mock_create_app.assert_called_once_with(
        auth_token="test-token",
        security=mock_load_gateway_settings.return_value.security,
    )
    mock_uvicorn_run.assert_called_once()
    args, kwargs = mock_uvicorn_run.call_args
    assert args[0] is mock_app
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 9999
    assert kwargs["log_level"] == "info"


@patch("hypo_agent.gateway.main.uvicorn.run")
@patch("hypo_agent.gateway.main.create_app")
@patch("hypo_agent.gateway.main.load_gateway_settings")
def test_port_default(
    mock_load_gateway_settings, mock_create_app, mock_uvicorn_run, monkeypatch
) -> None:
    monkeypatch.delenv("HYPO_PORT", raising=False)
    mock_load_gateway_settings.return_value = SimpleNamespace(
        auth_token="test-token",
        security=SimpleNamespace(),
    )
    mock_app = object()
    mock_create_app.return_value = mock_app

    run(host="127.0.0.1")

    args, kwargs = mock_uvicorn_run.call_args
    assert kwargs["port"] == 8765


@patch("hypo_agent.gateway.main.uvicorn.run")
@patch("hypo_agent.gateway.main.create_app")
@patch("hypo_agent.gateway.main.load_gateway_settings")
def test_port_from_env(
    mock_load_gateway_settings, mock_create_app, mock_uvicorn_run, monkeypatch
) -> None:
    monkeypatch.setenv("HYPO_PORT", "9999")
    mock_load_gateway_settings.return_value = SimpleNamespace(
        auth_token="test-token",
        security=SimpleNamespace(),
    )
    mock_app = object()
    mock_create_app.return_value = mock_app

    run(host="127.0.0.1")

    args, kwargs = mock_uvicorn_run.call_args
    assert kwargs["port"] == 9999
