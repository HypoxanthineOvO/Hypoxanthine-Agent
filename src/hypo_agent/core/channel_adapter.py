from __future__ import annotations

from typing import Any, Protocol

from hypo_agent.core.rich_response import RichResponse


class ChannelAdapter(Protocol):
    def format(
        self,
        event: dict[str, Any] | RichResponse,
        *,
        event_type: str | None = None,
        session_id: str | None = None,
        sender: str = "assistant",
    ) -> dict[str, Any]:
        ...


class WebUIAdapter:
    def format(
        self,
        event: dict[str, Any] | RichResponse,
        *,
        event_type: str | None = None,
        session_id: str | None = None,
        sender: str = "assistant",
    ) -> dict[str, Any]:
        if isinstance(event, dict):
            # Keep passthrough behavior while decoupling pipeline output from transport.
            return dict(event)

        if event_type is None or session_id is None:
            raise ValueError("event_type and session_id are required for RichResponse formatting")

        if event_type == "assistant_chunk":
            return {
                "type": "assistant_chunk",
                "text": event.text,
                "sender": sender,
                "session_id": session_id,
            }

        if event_type == "assistant_done":
            return {
                "type": "assistant_done",
                "sender": sender,
                "session_id": session_id,
            }

        tool_call = event.tool_calls[0] if event.tool_calls else {}

        if event_type == "tool_call_start":
            return {
                "type": "tool_call_start",
                "tool_name": tool_call.get("tool_name", ""),
                "tool_call_id": tool_call.get("tool_call_id", ""),
                "arguments": tool_call.get("arguments", {}),
                "session_id": session_id,
            }

        if event_type == "tool_call_result":
            payload = {
                "type": "tool_call_result",
                "tool_name": tool_call.get("tool_name", ""),
                "tool_call_id": tool_call.get("tool_call_id", ""),
                "status": tool_call.get("status", "error"),
                "result": tool_call.get("result"),
                "error_info": tool_call.get("error_info", ""),
                "metadata": dict(tool_call.get("metadata", {})),
                "session_id": session_id,
            }
            if event.compressed_meta is not None:
                payload["compressed_meta"] = dict(event.compressed_meta)
            return payload

        raise ValueError(f"Unsupported event_type '{event_type}'")
