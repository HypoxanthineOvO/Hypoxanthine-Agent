from __future__ import annotations

import asyncio
from pathlib import Path

import hypo_agent.gateway.app as app_module
from hypo_agent.core.config_loader import RuntimeModelConfig
from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.memory.session import SessionMemory
from hypo_agent.memory.structured_store import StructuredStore


class StubSemanticMemory:
    def __init__(self) -> None:
        self.model_router = None
        self.updated_paths: list[Path] = []

    async def search(self, query: str, top_k: int = 5):
        del query, top_k
        return []

    async def update_index(self, file_path: Path | str) -> None:
        self.updated_paths.append(Path(file_path))


def _runtime_config() -> RuntimeModelConfig:
    return RuntimeModelConfig.model_validate(
        {
            "default_model": "Gemini3Pro",
            "task_routing": {
                "chat": "Gemini3Pro",
                "lightweight": "Gemini3Pro",
                "compression": "Gemini3Pro",
                "heartbeat": "Gemini3Pro",
                "reasoning": "Gemini3Pro",
            },
            "models": {
                "Gemini3Pro": {
                    "type": "chat",
                    "provider": "Hiapi",
                    "litellm_model": "openai/gemini-3-pro",
                    "fallback": None,
                    "api_base": "https://example.invalid/v1",
                    "api_key": "test-key",
                }
            },
        }
    )


def test_build_default_pipeline_registers_update_persona_memory_builtin(
    tmp_path: Path,
    monkeypatch,
) -> None:
    memory_root = tmp_path / "memory"
    semantic_memory = StubSemanticMemory()
    deps = app_module.AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        semantic_memory=semantic_memory,
        skill_manager=SkillManager(),
    )

    monkeypatch.setattr(app_module, "get_memory_dir", lambda: memory_root)
    monkeypatch.setattr(app_module, "load_runtime_model_config", _runtime_config)

    app_module._build_default_pipeline(deps)

    output = asyncio.run(
        deps.skill_manager.invoke(
            "update_persona_memory",
            {"key": "回复风格", "value": "简洁"},
            session_id="s1",
        )
    )

    file_path = memory_root / "knowledge" / "persona" / "user_preferences.md"
    assert output.status == "success"
    assert file_path.exists() is True
    content = file_path.read_text(encoding="utf-8")
    assert "## 回复风格" in content
    assert "简洁" in content
    assert semantic_memory.updated_paths == [file_path]


def test_build_default_pipeline_save_sop_description_requires_confirm_wait(
    tmp_path: Path,
    monkeypatch,
) -> None:
    deps = app_module.AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        semantic_memory=StubSemanticMemory(),
        skill_manager=SkillManager(),
    )

    monkeypatch.setattr(app_module, "get_memory_dir", lambda: tmp_path / "memory")
    monkeypatch.setattr(app_module, "load_runtime_model_config", _runtime_config)

    app_module._build_default_pipeline(deps)

    tools = deps.skill_manager.get_tools_schema()
    save_sop = next(tool for tool in tools if tool["function"]["name"] == "save_sop")
    description = save_sop["function"]["description"]

    assert "confirmation" in description.lower()
    assert "same turn" in description.lower()
    assert "wait" in description.lower()


def test_build_default_pipeline_exposes_all_builtin_tools_in_schema(
    tmp_path: Path,
    monkeypatch,
) -> None:
    deps = app_module.AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        semantic_memory=StubSemanticMemory(),
        skill_manager=SkillManager(),
    )

    monkeypatch.setattr(app_module, "get_memory_dir", lambda: tmp_path / "memory")
    monkeypatch.setattr(app_module, "load_runtime_model_config", _runtime_config)

    app_module._build_default_pipeline(deps)

    tools = deps.skill_manager.get_tools_schema()
    tool_names = [tool["function"]["name"] for tool in tools]

    assert "update_persona_memory" in tool_names
    assert "save_sop" in tool_names
    assert "search_sop" in tool_names
    assert tool_names[:3] == [
        "update_persona_memory",
        "save_sop",
        "search_sop",
    ]


def test_build_default_pipeline_runs_antigravity_tool_name_audit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    deps = app_module.AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        semantic_memory=StubSemanticMemory(),
        skill_manager=SkillManager(),
    )
    captured_tool_names: list[str] = []

    monkeypatch.setattr(app_module, "get_memory_dir", lambda: tmp_path / "memory")
    monkeypatch.setattr(app_module, "load_runtime_model_config", _runtime_config)
    monkeypatch.setattr(
        app_module,
        "log_antigravity_tool_name_audit",
        lambda tool_names: captured_tool_names.extend(tool_names),
    )

    app_module._build_default_pipeline(deps)

    assert "search_web" in captured_tool_names
    assert "web_search" not in captured_tool_names


def test_build_default_pipeline_disables_output_compressor_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    deps = app_module.AppDeps(
        session_memory=SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20),
        structured_store=StructuredStore(db_path=tmp_path / "hypo.db"),
        semantic_memory=StubSemanticMemory(),
        skill_manager=SkillManager(),
    )

    monkeypatch.setattr(app_module, "get_memory_dir", lambda: tmp_path / "memory")
    monkeypatch.setattr(app_module, "load_runtime_model_config", _runtime_config)

    pipeline = app_module._build_default_pipeline(deps)

    assert deps.output_compressor is None
    assert pipeline.output_compressor is None
