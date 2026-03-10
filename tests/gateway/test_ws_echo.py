import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hypo_agent.gateway.app import create_app


def _client(token: str = "test-token") -> TestClient:
    app = create_app(auth_token=token)
    return TestClient(app)


def test_ws_rejects_missing_token() -> None:
    with _client() as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws"):
                pass
        assert exc_info.value.code == 4401


def test_ws_rejects_invalid_token() -> None:
    with _client() as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws?token=wrong"):
                pass
        assert exc_info.value.code == 4401


def test_ws_echoes_valid_message_payload() -> None:
    with _client() as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"text": "hello", "sender": "user", "session_id": "s1"})
            response = ws.receive_json()
            assert response["sender"] == "assistant"
            assert response["text"] == "hello"
            assert response["session_id"] == "s1"


def test_ws_rejects_invalid_message_shape() -> None:
    with _client() as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"sender": "user"})
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 4400
