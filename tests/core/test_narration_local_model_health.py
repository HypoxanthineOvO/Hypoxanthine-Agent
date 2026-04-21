from __future__ import annotations

import httpx

from hypo_agent.core.narration_observer import NarrationObserver
from hypo_agent.gateway.app import check_narration_local_model
from hypo_agent.models import NarrationConfig, NarrationToolLevels


class StubRouter:
    def __init__(self) -> None:
        self.config = type(
            "RuntimeConfig",
            (),
            {
                "models": {
                    "GenesiQWen35BA3B": type(
                        "ModelConfig",
                        (),
                        {
                            "provider": "GenesisLocal",
                            "api_base": "http://localhost:18081/v1",
                            "api_key": "genesis-llm-2026",
                        },
                    )()
                }
            },
        )()

    def get_model_for_task(self, task_type: str) -> str:
        assert task_type == "lightweight"
        return "GenesiQWen35BA3B"

    async def call(self, model_name, messages, *, session_id=None, tools=None) -> str:
        del model_name, messages, session_id, tools
        return "ok"


def _observer() -> NarrationObserver:
    return NarrationObserver(
        router=StubRouter(),
        config=NarrationConfig(
            enabled=True,
            model="GenesiQWen35BA3B",
            tool_levels=NarrationToolLevels(heavy=["heavy_tool"], medium=[]),
            llm_timeout_ms=800,
            debounce_seconds=2.0,
            max_narration_length=80,
        ),
    )


def test_check_narration_local_model_marks_ready() -> None:
    observer = _observer()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"id": "qwen3.6-35b"}]})
    )

    check_narration_local_model(observer, StubRouter(), transport=transport)

    assert observer.is_llm_ready() is True


def test_check_narration_local_model_marks_silent_when_unavailable() -> None:
    observer = _observer()
    transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("boom", request=request))
    )

    check_narration_local_model(observer, StubRouter(), transport=transport)

    assert observer.is_llm_ready() is False
