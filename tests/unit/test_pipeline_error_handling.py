from __future__ import annotations

import asyncio

import pytest
from structlog.testing import capture_logs

from hypo_agent.core.model_router import ModelFallbackError
from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.models import Message
from tests.shared import NoopRouter


class _StubSessionMemory:
    def append(self, message: Message) -> None:
        del message

    def get_recent_messages(self, session_id: str, limit: int | None = None) -> list[Message]:
        del session_id, limit
        return []


def _has_event(logs: list[dict[str, object]], event_name: str, caplog) -> bool:
    for entry in logs:
        if any(entry.get(key) == event_name for key in ("event", "log", "message")):
            return True
    return event_name in caplog.text


@pytest.mark.unit
def test_pipeline_logs_timeout_handled_for_timeout(caplog) -> None:
    async def _run() -> None:
        pipeline = ChatPipeline(
            router=NoopRouter(),
            chat_model="Gemini3Pro",
            session_memory=_StubSessionMemory(),
        )
        emitted: list[dict[str, object]] = []

        async def _failing_stream_reply(inbound: Message):
            del inbound
            if False:  # pragma: no cover
                yield {}
            raise TimeoutError("llm timed out")

        pipeline.stream_reply = _failing_stream_reply  # type: ignore[method-assign]

        with capture_logs() as logs:
            await pipeline._consume_user_message_event(
                {
                    "message": Message(text="hi", sender="user", session_id="s-timeout"),
                    "emit": emitted.append,
                }
            )

        assert emitted[0]["code"] == "LLM_TIMEOUT"
        assert _has_event(logs, "pipeline.timeout_handled", caplog)

    asyncio.run(_run())


@pytest.mark.unit
def test_pipeline_logs_error_converted_for_runtime_error(caplog) -> None:
    async def _run() -> None:
        pipeline = ChatPipeline(
            router=NoopRouter(),
            chat_model="Gemini3Pro",
            session_memory=_StubSessionMemory(),
        )
        emitted: list[dict[str, object]] = []

        async def _failing_stream_reply(inbound: Message):
            del inbound
            if False:  # pragma: no cover
                yield {}
            raise RuntimeError("model crashed")

        pipeline.stream_reply = _failing_stream_reply  # type: ignore[method-assign]

        with capture_logs() as logs:
            await pipeline._consume_user_message_event(
                {
                    "message": Message(text="hi", sender="user", session_id="s-runtime"),
                    "emit": emitted.append,
                }
            )

        assert emitted[0]["code"] == "LLM_RUNTIME_ERROR"
        assert _has_event(logs, "pipeline.error_converted", caplog)

    asyncio.run(_run())


@pytest.mark.unit
def test_pipeline_timeout_runtime_error_uses_generic_timeout_message(caplog) -> None:
    async def _run() -> None:
        pipeline = ChatPipeline(
            router=NoopRouter(),
            chat_model="Gemini3Pro",
            session_memory=_StubSessionMemory(),
        )
        emitted: list[dict[str, object]] = []

        async def _failing_stream_reply(inbound: Message):
            del inbound
            if False:  # pragma: no cover
                yield {}
            raise RuntimeError("Request timed out after 60 seconds.")

        pipeline.stream_reply = _failing_stream_reply  # type: ignore[method-assign]

        with capture_logs() as logs:
            await pipeline._consume_user_message_event(
                {
                    "message": Message(text="hi", sender="user", session_id="s-timeout-runtime"),
                    "emit": emitted.append,
                }
            )

        assert emitted[0]["code"] == "LLM_TIMEOUT"
        assert emitted[0]["message"] == "模型调用超时，请稍后重试"
        assert "Request timed out after 60 seconds" not in str(emitted[0]["message"])
        assert _has_event(logs, "pipeline.error_converted", caplog)

    asyncio.run(_run())


@pytest.mark.unit
def test_pipeline_emits_structured_model_fallback_error(caplog) -> None:
    async def _run() -> None:
        pipeline = ChatPipeline(
            router=NoopRouter(),
            chat_model="Gemini3Pro",
            session_memory=_StubSessionMemory(),
        )
        emitted: list[dict[str, object]] = []

        async def _failing_stream_reply(inbound: Message):
            del inbound
            if False:  # pragma: no cover
                yield {}
            raise ModelFallbackError(
                "all failed",
                requested_model="Gemini3Pro",
                task_type="vision",
                attempted_chain=[
                    {"model": "Gemini3Pro", "error_class": "TimeoutError"},
                    {"model": "VisionBackup", "error_class": "RuntimeError"},
                ],
            )

        pipeline.stream_reply = _failing_stream_reply  # type: ignore[method-assign]

        with capture_logs() as logs:
            await pipeline._consume_user_message_event(
                {
                    "message": Message(text="看图", sender="user", session_id="s-model"),
                    "emit": emitted.append,
                }
            )

        assert emitted[0]["code"] == "LLM_FALLBACK_EXHAUSTED"
        assert emitted[0]["attempted_chain"] == [
            {"model": "Gemini3Pro", "error_class": "TimeoutError"},
            {"model": "VisionBackup", "error_class": "RuntimeError"},
        ]
        assert "Gemini3Pro -> VisionBackup" in str(emitted[0]["message"])
        assert _has_event(logs, "pipeline.model_fallback_exhausted", caplog)

    asyncio.run(_run())


@pytest.mark.unit
def test_pipeline_logs_degraded_when_sop_path_lookup_fails(caplog) -> None:
    class _BrokenSopManager:
        def is_sop_path(self, file_path: str) -> bool:
            del file_path
            raise RuntimeError("sop index unavailable")

    pipeline = ChatPipeline(
        router=NoopRouter(),
        chat_model="Gemini3Pro",
        session_memory=_StubSessionMemory(),
        sop_manager=_BrokenSopManager(),
    )

    with capture_logs() as logs:
        result = pipeline._is_sop_result("/knowledge/sop/test.md")

    assert result is False
    assert _has_event(logs, "pipeline.sop_read.degraded", caplog)


@pytest.mark.unit
def test_pipeline_logs_degraded_when_preferences_lookup_fails(caplog) -> None:
    class _BrokenStore:
        def list_preferences_sync(self, *, limit: int = 20) -> list[tuple[str, str]]:
            del limit
            raise RuntimeError("preferences db unavailable")

    pipeline = ChatPipeline(
        router=NoopRouter(),
        chat_model="Gemini3Pro",
        session_memory=_StubSessionMemory(),
        structured_store=_BrokenStore(),
    )

    with capture_logs() as logs:
        result = pipeline._preferences_context()

    assert result == ""
    assert _has_event(logs, "pipeline.preference_read.degraded", caplog)
