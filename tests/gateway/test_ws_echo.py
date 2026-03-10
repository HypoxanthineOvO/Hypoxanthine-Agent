import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hypo_agent.gateway.app import create_app


class StubPipeline:
    async def stream_reply(self, inbound):
        text = inbound.text or ""
        yield {
            "type": "assistant_chunk",
            "text": text.upper(),
            "sender": "assistant",
            "session_id": inbound.session_id,
        }
        yield {
            "type": "assistant_done",
            "sender": "assistant",
            "session_id": inbound.session_id,
        }


class FailingPipeline:
    async def stream_reply(self, inbound):
        if False:  # pragma: no cover
            yield {}
        raise RuntimeError("fallback chain exhausted")


class TimeoutPipeline:
    async def stream_reply(self, inbound):
        if False:  # pragma: no cover
            yield {}
        raise TimeoutError("llm timeout")


class ToolEventPipeline:
    async def stream_reply(self, inbound):
        yield {
            "type": "tool_call_start",
            "tool_name": "run_command",
            "tool_call_id": "call_1",
            "session_id": inbound.session_id,
        }
        yield {
            "type": "tool_call_result",
            "tool_name": "run_command",
            "tool_call_id": "call_1",
            "status": "success",
            "result": {"stdout": "ok"},
            "error_info": "",
            "metadata": {},
            "session_id": inbound.session_id,
        }
        yield {
            "type": "assistant_chunk",
            "text": "ok",
            "sender": "assistant",
            "session_id": inbound.session_id,
        }
        yield {
            "type": "assistant_done",
            "sender": "assistant",
            "session_id": inbound.session_id,
        }


def _client(token: str = "test-token", pipeline=None) -> TestClient:
    app = create_app(auth_token=token, pipeline=pipeline or StubPipeline())
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


def test_ws_streams_valid_message_payload() -> None:
    with _client() as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"text": "hello", "sender": "user", "session_id": "s1"})
            first = ws.receive_json()
            second = ws.receive_json()
            assert first == {
                "type": "assistant_chunk",
                "text": "HELLO",
                "sender": "assistant",
                "session_id": "s1",
            }
            assert second == {
                "type": "assistant_done",
                "sender": "assistant",
                "session_id": "s1",
            }


def test_ws_rejects_invalid_message_shape() -> None:
    with _client() as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"sender": "user"})
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 4400


def test_ws_sends_error_event_on_pipeline_runtime_error() -> None:
    with _client(pipeline=FailingPipeline()) as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"text": "hello", "sender": "user", "session_id": "s1"})
            event = ws.receive_json()
            assert event == {
                "type": "error",
                "code": "LLM_RUNTIME_ERROR",
                "message": "LLM 调用失败，请检查配置或稍后重试",
                "retryable": True,
                "session_id": "s1",
            }
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1011


def test_ws_sends_retryable_timeout_error_event() -> None:
    with _client(pipeline=TimeoutPipeline()) as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"text": "hello", "sender": "user", "session_id": "s1"})
            event = ws.receive_json()
            assert event == {
                "type": "error",
                "code": "LLM_TIMEOUT",
                "message": "LLM 调用超时，请稍后重试",
                "retryable": True,
                "session_id": "s1",
            }
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 1011


def test_ws_forwards_tool_events_without_modification() -> None:
    with _client(pipeline=ToolEventPipeline()) as client:
        with client.websocket_connect("/ws?token=test-token") as ws:
            ws.send_json({"text": "hello", "sender": "user", "session_id": "s1"})
            first = ws.receive_json()
            second = ws.receive_json()
            third = ws.receive_json()
            fourth = ws.receive_json()
            assert first["type"] == "tool_call_start"
            assert second["type"] == "tool_call_result"
            assert third["type"] == "assistant_chunk"
            assert fourth["type"] == "assistant_done"
