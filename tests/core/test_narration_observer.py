from __future__ import annotations

import asyncio

from hypo_agent.core.narration_observer import NarrationObserver
from hypo_agent.models import NarrationConfig, NarrationToolLevels


class StubRouter:
    def __init__(self, *, reply: str = "我去翻一下你的收件箱看看。") -> None:
        self.reply = reply
        self.calls: list[dict[str, object]] = []

    def get_model_for_task(self, task_type: str) -> str:
        assert task_type == "lightweight"
        return "DeepseekV3_2"

    async def call(
        self,
        model_name: str,
        messages: list[dict[str, object]],
        *,
        session_id: str | None = None,
        tools: list[dict[str, object]] | None = None,
    ) -> str:
        self.calls.append(
            {
                "model_name": model_name,
                "messages": messages,
                "session_id": session_id,
                "tools": tools,
            }
        )
        return self.reply


def _config(*, max_narration_length: int = 80, debounce_seconds: float = 2.0) -> NarrationConfig:
    return NarrationConfig(
        enabled=True,
        model="lightweight",
        tool_levels=NarrationToolLevels(
            heavy=["scan_emails", "run_command"],
            medium=["write_file"],
        ),
        debounce_seconds=debounce_seconds,
        max_narration_length=max_narration_length,
    )


def test_observer_generates_narration_for_heavy_tool() -> None:
    router = StubRouter(reply="我去翻一下你的收件箱，看看最近的新邮件。")
    observer = NarrationObserver(router=router, config=_config(max_narration_length=18))

    narration = asyncio.run(
        observer.maybe_narrate(
            tool_name="scan_emails",
            tool_args={"limit": 10, "mailbox": "INBOX"},
            user_message_context="帮我看看新邮件",
            session_id="main",
        )
    )

    assert narration == "我去翻一下你的收件箱，看看最近的新邮"
    assert narration is not None
    assert len(narration) <= 18
    assert len(router.calls) == 1
    assert router.calls[0]["model_name"] == "DeepseekV3_2"
    prompt_messages = router.calls[0]["messages"]
    assert isinstance(prompt_messages, list)
    assert "帮我看看新邮件" in str(prompt_messages[1]["content"])
    assert "scan_emails" in str(prompt_messages[1]["content"])


def test_observer_skips_light_tools_without_llm_call() -> None:
    router = StubRouter()
    observer = NarrationObserver(router=router, config=_config())

    narration = asyncio.run(
        observer.maybe_narrate(
            tool_name="read_file",
            tool_args={"path": "README.md"},
            user_message_context="帮我看下 README",
        )
    )

    assert narration is None
    assert router.calls == []


def test_observer_debounces_same_tool_within_window() -> None:
    router = StubRouter()
    observer = NarrationObserver(
        router=router,
        config=_config(debounce_seconds=2.0),
        time_fn=lambda: 10.0,
    )

    first = asyncio.run(
        observer.maybe_narrate(
            tool_name="scan_emails",
            tool_args={},
            user_message_context="帮我看邮件",
        )
    )
    second = asyncio.run(
        observer.maybe_narrate(
            tool_name="scan_emails",
            tool_args={},
            user_message_context="再看一次",
        )
    )

    assert first is not None
    assert second is None
    assert len(router.calls) == 1


def test_observer_drops_timeout() -> None:
    class SlowRouter(StubRouter):
        async def call(
            self,
            model_name: str,
            messages: list[dict[str, object]],
            *,
            session_id: str | None = None,
            tools: list[dict[str, object]] | None = None,
        ) -> str:
            del model_name, messages, session_id, tools
            await asyncio.sleep(0.05)
            return "我还在想"

    observer = NarrationObserver(
        router=SlowRouter(),
        config=_config(),
        llm_timeout_seconds=0.01,
    )

    narration = asyncio.run(
        observer.maybe_narrate(
            tool_name="scan_emails",
            tool_args={},
            user_message_context="帮我看邮件",
        )
    )

    assert narration is None
