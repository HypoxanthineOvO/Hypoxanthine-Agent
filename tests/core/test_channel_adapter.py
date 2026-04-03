from __future__ import annotations

import asyncio

from hypo_agent.core.channel_adapter import WebUIAdapter
from hypo_agent.core.rich_response import RichResponse


def test_webui_adapter_passthrough_dict_copy() -> None:
    adapter = WebUIAdapter()
    event = {
        "type": "assistant_chunk",
        "text": "hello",
        "sender": "assistant",
        "session_id": "s1",
    }

    formatted = asyncio.run(adapter.format(event))

    assert formatted == event
    assert formatted is not event


def test_webui_adapter_preserves_tool_result_fields() -> None:
    adapter = WebUIAdapter()
    event = {
        "type": "tool_call_result",
        "tool_name": "exec_command",
        "tool_call_id": "call_1",
        "status": "success",
        "result": {"stdout": "ok"},
        "error_info": "",
        "metadata": {"compressed": False},
        "session_id": "s1",
    }

    formatted = asyncio.run(adapter.format(event))

    assert formatted["type"] == "tool_call_result"
    assert formatted["tool_name"] == "exec_command"
    assert formatted["status"] == "success"
    assert formatted["session_id"] == "s1"


def test_webui_adapter_formats_assistant_chunk_from_rich_response() -> None:
    adapter = WebUIAdapter()
    response = RichResponse(text="chunk-1")

    formatted = asyncio.run(
        adapter.format(
            response,
            event_type="assistant_chunk",
            session_id="s1",
        )
    )

    assert formatted["type"] == "assistant_chunk"
    assert formatted["text"] == "chunk-1"
    assert formatted["sender"] == "assistant"
    assert formatted["session_id"] == "s1"
    assert formatted["timestamp"].endswith("Z")


def test_webui_adapter_formats_assistant_done_with_attachments() -> None:
    adapter = WebUIAdapter()
    response = RichResponse(
        attachments=[
            {
                "type": "file",
                "url": "/tmp/export.pdf",
                "filename": "export.pdf",
                "mime_type": "application/pdf",
            }
        ]
    )

    formatted = asyncio.run(
        adapter.format(
            response,
            event_type="assistant_done",
            session_id="s1",
        )
    )

    assert formatted["type"] == "assistant_done"
    assert formatted["attachments"][0]["filename"] == "export.pdf"


def test_webui_adapter_formats_tool_result_with_compressed_meta() -> None:
    adapter = WebUIAdapter()
    response = RichResponse(
        compressed_meta={
            "cache_id": "abc",
            "original_chars": 5000,
            "compressed_chars": 1200,
        },
        tool_calls=[
            {
                "tool_name": "exec_command",
                "tool_call_id": "call_1",
                "status": "success",
                "result": "compressed text",
                "error_info": "",
                "metadata": {"compressed": True},
            }
        ],
    )

    formatted = asyncio.run(
        adapter.format(
            response,
            event_type="tool_call_result",
            session_id="s1",
        )
    )

    assert formatted["type"] == "tool_call_result"
    assert formatted["tool_name"] == "exec_command"
    assert formatted["compressed_meta"] == {
        "cache_id": "abc",
        "original_chars": 5000,
        "compressed_chars": 1200,
    }


def test_webui_adapter_includes_attachments_on_tool_result() -> None:
    adapter = WebUIAdapter()
    response = RichResponse(
        tool_calls=[
            {
                "tool_name": "export_to_file",
                "tool_call_id": "call_export",
                "status": "success",
                "result": "/tmp/export.pdf",
                "error_info": "",
                "metadata": {},
            }
        ],
        attachments=[
            {
                "type": "file",
                "url": "/tmp/export.pdf",
                "filename": "export.pdf",
                "mime_type": "application/pdf",
            }
        ],
    )

    formatted = asyncio.run(
        adapter.format(
            response,
            event_type="tool_call_result",
            session_id="s1",
        )
    )

    assert formatted["attachments"][0]["filename"] == "export.pdf"
