from __future__ import annotations

from hypo_agent.core.pipeline import ChatPipeline
from hypo_agent.memory.session import SessionMemory


class _StubRouter:
    def get_model_for_task(self, task_type: str) -> str:
        del task_type
        return "GeminiLow"


def test_pipeline_remaps_legacy_tool_names_in_replayed_messages(tmp_path) -> None:
    pipeline = ChatPipeline(
        router=_StubRouter(),
        chat_model="GeminiLow",
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
    )

    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "arguments": "{\"query\": \"hi\"}",
                    },
                }
            ],
        }
    ]

    remapped = pipeline._remap_legacy_tool_names_in_messages(messages)

    assert remapped[0]["tool_calls"][0]["function"]["name"] == "search_web"
    assert messages[0]["tool_calls"][0]["function"]["name"] == "web_search"
