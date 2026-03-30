from __future__ import annotations

import asyncio
import json
from urllib import request as urllib_request

from hypo_agent.channels.qq_adapter import QQAdapter
from hypo_agent.core.delivery import DeliveryResult
from hypo_agent.models import Message


def test_qq_adapter_downgrades_markdown_inline_styles() -> None:
    adapter = QQAdapter(napcat_http_url="http://localhost:3000")

    text = adapter.downgrade_markdown("**粗体** *斜体* `代码`")

    assert "**" not in text
    assert "*" not in text
    assert "`" not in text
    assert "粗体" in text
    assert "斜体" in text
    assert "代码" in text


def test_qq_adapter_keeps_code_block_indentation() -> None:
    adapter = QQAdapter(napcat_http_url="http://localhost:3000")
    content = "示例:\n```python\n  a = 1\n    print(a)\n```"

    text = adapter.downgrade_markdown(content)

    assert "a = 1" in text
    assert "    print(a)" in text
    assert "```" not in text


def test_qq_adapter_converts_markdown_table_to_plain_text() -> None:
    adapter = QQAdapter(napcat_http_url="http://localhost:3000")
    content = "| 列A | 列B |\n| --- | --- |\n| 1 | 2 |"

    text = adapter.downgrade_markdown(content)

    assert "列A | 列B" in text
    assert "1 | 2" in text


def test_qq_adapter_replaces_message_tag_with_emoji_prefix() -> None:
    adapter = QQAdapter(napcat_http_url="http://localhost:3000")
    message = Message(
        text="提醒：开会",
        sender="assistant",
        session_id="main",
        message_tag="reminder",
    )

    rendered = adapter.render_message_text(message)

    assert rendered.startswith("🔔 ")
    assert "提醒：开会" in rendered


def test_qq_adapter_splits_long_message() -> None:
    adapter = QQAdapter(napcat_http_url="http://localhost:3000")
    source = "a" * 55

    chunks = adapter.split_message(source, limit=20)

    assert len(chunks) == 3
    assert all(len(item) <= 20 for item in chunks)
    assert "".join(chunks) == source


def test_qq_adapter_adds_access_token_to_request_url() -> None:
    adapter = QQAdapter(
        napcat_http_url="http://localhost:3008",
        napcat_http_token="token-123",
    )

    url = adapter._build_request_url("/send_private_msg")

    assert url == "http://localhost:3008/send_private_msg?access_token=token-123"


def test_qq_adapter_sets_authorization_header_when_token_configured(monkeypatch) -> None:
    adapter = QQAdapter(
        napcat_http_url="http://localhost:3008",
        napcat_http_token="token-123",
    )
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb
            return None

        def read(self) -> bytes:
            return json.dumps({"status": "ok"}).encode("utf-8")

    def fake_urlopen(req: urllib_request.Request, timeout: float):
        captured["authorization"] = req.headers.get("Authorization")
        captured["content_type"] = req.headers.get("Content-type")
        captured["timeout"] = timeout
        captured["url"] = req.full_url
        return FakeResponse()

    monkeypatch.setattr("hypo_agent.channels.qq_adapter.urllib_request.urlopen", fake_urlopen)

    result = adapter._post_json("/send_private_msg", {"message": "hello", "user_id": 10001})

    assert result == {"status": "ok"}
    assert captured["authorization"] == "Bearer token-123"
    assert captured["content_type"] == "application/json"
    assert captured["url"] == "http://localhost:3008/send_private_msg?access_token=token-123"


def test_qq_adapter_omits_authorization_header_when_token_empty(monkeypatch) -> None:
    adapter = QQAdapter(
        napcat_http_url="http://localhost:3008",
        napcat_http_token="",
    )
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb
            return None

        def read(self) -> bytes:
            return json.dumps({"status": "ok"}).encode("utf-8")

    def fake_urlopen(req: urllib_request.Request, timeout: float):
        captured["authorization"] = req.headers.get("Authorization")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("hypo_agent.channels.qq_adapter.urllib_request.urlopen", fake_urlopen)

    result = adapter._post_json("/send_private_msg", {"message": "hello", "user_id": 10001})

    assert result == {"status": "ok"}
    assert captured["authorization"] is None


def test_qq_adapter_sends_mixed_segments_in_single_napcat_message(monkeypatch) -> None:
    adapter = QQAdapter(napcat_http_url="http://localhost:3008")
    captured: dict[str, object] = {}

    async def fake_format(_message) -> list[dict[str, object]]:
        return [
            {"type": "text", "data": {"text": "前文"}},
            {"type": "image", "data": {"file": "file:///tmp/table.png"}},
            {"type": "text", "data": {"text": "后文"}},
        ]

    async def fake_send_private_segments(*, user_id: str, segments: list[dict[str, object]]) -> DeliveryResult:
        captured["user_id"] = user_id
        captured["segments"] = segments
        return DeliveryResult.ok("qq_napcat", segment_count=len(segments))

    monkeypatch.setattr(adapter, "format", fake_format)
    monkeypatch.setattr(adapter, "send_private_segments", fake_send_private_segments)

    result = asyncio.run(
        adapter.send_message(
            user_id="10001",
            message=Message(text="ignored", sender="assistant", session_id="main"),
        )
    )

    assert result.success is True
    assert captured["user_id"] == "10001"
    assert captured["segments"] == [
        {"type": "text", "data": {"text": "前文"}},
        {"type": "image", "data": {"file": "file:///tmp/table.png"}},
        {"type": "text", "data": {"text": "后文"}},
    ]


def test_qq_adapter_send_private_segments_posts_single_mixed_message(monkeypatch) -> None:
    adapter = QQAdapter(napcat_http_url="http://localhost:3008")
    captured: dict[str, object] = {}

    def fake_post_json(path: str, payload: dict[str, object]) -> dict[str, object]:
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = asyncio.run(
        adapter.send_private_segments(
            user_id="10001",
            segments=[
                {"type": "text", "data": {"text": "前文"}},
                {"type": "image", "data": {"file": "file:///tmp/table.png"}},
                {"type": "text", "data": {"text": "后文"}},
            ],
        )
    )

    assert result.success is True
    assert captured["path"] == "/send_private_msg"
    assert captured["payload"] == {
        "user_id": 10001,
        "message": [
            {"type": "text", "data": {"text": "前文"}},
            {"type": "image", "data": {"file": "file:///tmp/table.png"}},
            {"type": "text", "data": {"text": "后文"}},
        ],
    }
