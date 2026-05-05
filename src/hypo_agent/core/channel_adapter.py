from __future__ import annotations

from typing import Any, Protocol

from hypo_agent.core.markdown_capability import ChannelMarkdownCapability
from hypo_agent.core.markdown_splitter import MarkdownBlock, split_markdown
from hypo_agent.core.rich_response import RichResponse
from hypo_agent.core.time_utils import utc_isoformat, utc_now
from hypo_agent.core.tool_display import classify_tool_error, summarize_tool_failure, tool_display_payload
from hypo_agent.models import Attachment

ChannelMessage = dict[str, Any]


def _serialize_attachments(
    attachments: list[Attachment | dict[str, Any]],
) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for attachment in attachments:
        if isinstance(attachment, Attachment):
            serialized.append(attachment.model_dump(mode="json"))
            continue
        if isinstance(attachment, dict):
            serialized.append(dict(attachment))
    return serialized


class ChannelAdapter(Protocol):
    async def format(
        self,
        event: dict[str, Any] | RichResponse,
        *,
        event_type: str | None = None,
        session_id: str | None = None,
        sender: str = "assistant",
    ) -> dict[str, Any]:
        ...


class WebUIAdapter:
    async def format(
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
                "timestamp": utc_isoformat(utc_now()),
            }

        if event_type == "assistant_done":
            payload = {
                "type": "assistant_done",
                "sender": sender,
                "session_id": session_id,
                "timestamp": utc_isoformat(utc_now()),
            }
            if event.attachments:
                payload["attachments"] = _serialize_attachments(event.attachments)
            return payload

        tool_call = event.tool_calls[0] if event.tool_calls else {}

        if event_type == "tool_call_start":
            tool_name = str(tool_call.get("tool_name") or "")
            payload = {
                "type": "tool_call_start",
                "tool_name": tool_name,
                "tool_call_id": tool_call.get("tool_call_id", ""),
                "arguments": tool_call.get("arguments", {}),
                "session_id": session_id,
            }
            payload.update(tool_display_payload(tool_name))
            if tool_call.get("iteration") is not None:
                payload["iteration"] = tool_call.get("iteration")
            return payload

        if event_type == "tool_call_result":
            tool_name = str(tool_call.get("tool_name") or "")
            metadata = dict(tool_call.get("metadata", {}))
            attempts = tool_call.get("attempts") or metadata.get("attempts")
            outcome_class = str(
                tool_call.get("outcome_class")
                or metadata.get("outcome_class")
                or classify_tool_error(str(tool_call.get("error_info") or ""))
            )
            summary = str(tool_call.get("summary") or "").strip()
            if str(tool_call.get("status") or "").strip() != "success" and not summary:
                summary = summarize_tool_failure(
                    tool_name=tool_name,
                    error=str(tool_call.get("error_info") or ""),
                    outcome_class=outcome_class,
                    attempts=_int_or_none(attempts),
                    retryable=tool_call.get("retryable") if isinstance(tool_call.get("retryable"), bool) else None,
                )
            payload = {
                "type": "tool_call_result",
                "tool_name": tool_name,
                "tool_call_id": tool_call.get("tool_call_id", ""),
                "status": tool_call.get("status", "error"),
                "result": tool_call.get("result"),
                "error_info": tool_call.get("error_info", ""),
                "metadata": metadata,
                "session_id": session_id,
                "outcome_class": outcome_class,
            }
            payload.update(tool_display_payload(tool_name))
            if summary:
                payload["summary"] = summary
            if attempts is not None:
                payload["attempts"] = attempts
            if isinstance(tool_call.get("retryable"), bool):
                payload["retryable"] = tool_call.get("retryable")
            if tool_call.get("duration_ms") is not None:
                payload["duration_ms"] = tool_call.get("duration_ms")
            if tool_call.get("iteration") is not None:
                payload["iteration"] = tool_call.get("iteration")
            if event.compressed_meta is not None:
                payload["compressed_meta"] = dict(event.compressed_meta)
            if event.attachments:
                payload["attachments"] = _serialize_attachments(event.attachments)
            return payload

        raise ValueError(f"Unsupported event_type '{event_type}'")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class BaseChannelAdapter:
    def __init__(self, capability: ChannelMarkdownCapability) -> None:
        self.capability = capability

    async def format(self, response: RichResponse) -> list[ChannelMessage]:
        blocks = split_markdown(str(response.text or ""))
        return await self.render_blocks(blocks)

    async def render_blocks(self, blocks: list[MarkdownBlock]) -> list[ChannelMessage]:
        raise NotImplementedError
