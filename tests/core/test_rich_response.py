from __future__ import annotations

from hypo_agent.core.rich_response import RichResponse


def test_rich_response_defaults() -> None:
    response = RichResponse()
    assert response.text == ""
    assert response.compressed_meta is None
    assert response.tool_calls == []
    assert response.attachments == []


def test_rich_response_preserves_fields() -> None:
    payload = RichResponse(
        text="hello",
        compressed_meta={"cache_id": "abc", "original_chars": 5000, "compressed_chars": 1200},
        tool_calls=[{"tool_name": "exec_command"}],
        attachments=[{"path": "./logs/out.txt"}],
    )

    assert payload.text == "hello"
    assert payload.compressed_meta == {
        "cache_id": "abc",
        "original_chars": 5000,
        "compressed_chars": 1200,
    }
    assert payload.tool_calls == [{"tool_name": "exec_command"}]
    assert payload.attachments == [{"path": "./logs/out.txt"}]
