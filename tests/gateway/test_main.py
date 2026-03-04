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

    run(host="127.0.0.1", port=8000)

    mock_load_gateway_settings.assert_called_once_with()
    mock_create_app.assert_called_once_with(
        auth_token="test-token",
        security=mock_load_gateway_settings.return_value.security,
    )
    mock_uvicorn_run.assert_called_once()
    args, kwargs = mock_uvicorn_run.call_args
    assert args[0] is mock_app
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8000
    assert kwargs["log_level"] == "info"
