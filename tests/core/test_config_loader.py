from pathlib import Path

import pytest

from hypo_agent.core.config_loader import (
    get_database_path,
    load_persona_config,
    get_memory_dir,
    get_port,
    load_secrets_config,
    load_narration_config,
    render_persona_system_prompt,
    load_runtime_model_config,
    load_tasks_config,
)
from hypo_agent.models import SecretsConfig


def test_load_runtime_model_config_merges_models_and_secrets(tmp_path: Path) -> None:
    models_yaml = tmp_path / "models.yaml"
    models_yaml.write_text(
        """
default_model: Gemini3Pro
task_routing:
  chat: Gemini3Pro
  embedding: VolcanoEmbedding
models:
  Gemini3Pro:
    provider: Hiapi
    litellm_model: openai/gemini-2.5-pro
    fallback: DeepseekV3_2
  DeepseekV3_2:
    provider: Volcengine
    litellm_model: openai/ep-20251215171209-4z5qk
    fallback: null
  VolcanoEmbedding:
    provider: volcano
    type: embedding
    litellm_model: openai/doubao-embedding-text-240715
    fallback: null
""".strip(),
        encoding="utf-8",
    )

    secrets_yaml = tmp_path / "secrets.yaml"
    secrets_yaml.write_text(
        """
providers:
  Hiapi:
    api_base: https://hiapi.online/v1
    api_key: sk-hiapi
  Volcengine:
    api_base: https://ark.cn-beijing.volces.com/api/v3
    api_key: volc-key
  volcano:
    api_base: https://ark.cn-beijing.volces.com/api/v3
    api_key: embed-key
""".strip(),
        encoding="utf-8",
    )

    runtime = load_runtime_model_config(models_yaml, secrets_yaml)

    assert runtime.default_model == "Gemini3Pro"
    assert runtime.models["Gemini3Pro"].api_base == "https://hiapi.online/v1"
    assert runtime.models["DeepseekV3_2"].api_key == "volc-key"
    assert runtime.task_routing["embedding"] == "VolcanoEmbedding"
    assert runtime.models["VolcanoEmbedding"].type == "embedding"


def test_load_runtime_model_config_requires_existing_secrets_file(
    tmp_path: Path,
) -> None:
    models_yaml = tmp_path / "models.yaml"
    models_yaml.write_text(
        """
default_model: Gemini3Pro
task_routing: {}
models: {}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError):
        load_runtime_model_config(models_yaml, tmp_path / "missing-secrets.yaml")


def test_load_runtime_model_config_rejects_missing_provider_secret(
    tmp_path: Path,
) -> None:
    models_yaml = tmp_path / "models.yaml"
    models_yaml.write_text(
        """
default_model: Gemini3Pro
task_routing:
  chat: Gemini3Pro
models:
  Gemini3Pro:
    provider: Hiapi
    litellm_model: openai/gemini-2.5-pro
    fallback: null
""".strip(),
        encoding="utf-8",
    )

    secrets_yaml = tmp_path / "secrets.yaml"
    secrets_yaml.write_text("providers: {}", encoding="utf-8")

    with pytest.raises(ValueError, match="Provider 'Hiapi'"):
        load_runtime_model_config(models_yaml, secrets_yaml)


def test_load_runtime_model_config_resolves_env_api_key_and_optional_api_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models_yaml = tmp_path / "models.yaml"
    models_yaml.write_text(
        """
default_model: MiniMaxM2
task_routing:
  chat: MiniMaxM2
models:
  MiniMaxM2:
    provider: minimax
    litellm_model: minimax/MiniMax-M2
    fallback: null
""".strip(),
        encoding="utf-8",
    )

    secrets_yaml = tmp_path / "secrets.yaml"
    secrets_yaml.write_text(
        """
providers:
  minimax:
    api_key: $MINIMAX_API_KEY
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("MINIMAX_API_KEY", "env-minimax-key")

    runtime = load_runtime_model_config(models_yaml, secrets_yaml)

    assert runtime.models["MiniMaxM2"].api_base is None
    assert runtime.models["MiniMaxM2"].api_key == "env-minimax-key"


def test_load_runtime_model_config_rejects_missing_env_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models_yaml = tmp_path / "models.yaml"
    models_yaml.write_text(
        """
default_model: MiniMaxM2
task_routing:
  chat: MiniMaxM2
models:
  MiniMaxM2:
    provider: minimax
    litellm_model: minimax/MiniMax-M2
    fallback: null
""".strip(),
        encoding="utf-8",
    )

    secrets_yaml = tmp_path / "secrets.yaml"
    secrets_yaml.write_text(
        """
providers:
  minimax:
    api_key: $MINIMAX_API_KEY
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    with pytest.raises(ValueError, match="MINIMAX_API_KEY"):
        load_runtime_model_config(models_yaml, secrets_yaml)


def test_load_runtime_model_config_rejects_missing_task_routing_model(
    tmp_path: Path,
) -> None:
    models_yaml = tmp_path / "models.yaml"
    models_yaml.write_text(
        """
default_model: Gemini3Pro
task_routing:
  lightweight: DeepseekV3_2
models:
  Gemini3Pro:
    provider: Hiapi
    litellm_model: openai/gemini-2.5-pro
    fallback: null
""".strip(),
        encoding="utf-8",
    )

    secrets_yaml = tmp_path / "secrets.yaml"
    secrets_yaml.write_text(
        """
providers:
  Hiapi:
    api_base: https://hiapi.online/v1
    api_key: sk-hiapi
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="task_routing 'lightweight' model 'DeepseekV3_2'"):
        load_runtime_model_config(models_yaml, secrets_yaml)


def test_repo_models_config_routes_local_main_and_local_utility_models() -> None:
    runtime = load_runtime_model_config(
        Path(__file__).resolve().parents[2] / "config" / "models.yaml",
        Path(__file__).resolve().parents[2] / "config" / "secrets.yaml",
    )

    assert runtime.default_model == "GenesiQWen35BA3B"
    assert runtime.task_routing["chat"] == "DeepSeekV4"
    assert runtime.task_routing["reasoning"] == "DeepSeekV4"
    assert runtime.task_routing["lightweight"] == "EdenQwen"
    assert runtime.task_routing["compression"] == "DeepSeekV4"
    assert runtime.task_routing["heartbeat"] == "GenesiQWen35BA3B"
    assert runtime.task_routing["vision"] == "GPT"
    assert runtime.models["DeepSeekV4"].litellm_model == "deepseek/deepseek-v4-flash"
    assert runtime.models["DeepSeekV4"].provider == "Deepseek"
    assert runtime.models["DeepSeekV4"].fallback == "GenesiQWen35BA3B"
    assert runtime.models["GenesiQWen35BA3B"].litellm_model == "openai/qwen3.6-35b"
    assert runtime.models["GenesiQWen35BA3B"].provider == "GenesisLocal"
    assert runtime.models["GenesiQWen35BA3B"].fallback is None
    assert runtime.models["EdenQwen"].litellm_model == "ollama_chat/qwen3.5:27b"
    assert runtime.models["EdenQwen"].provider == "Eden"
    assert runtime.models["EdenQwen"].fallback == "CodingPlanAuto"
    assert runtime.models["GeminiFlash"].fallback == "EdenQwen"
    assert runtime.models["Claude"].litellm_model == "anthropic/claude-opus-4-5-thinking"
    assert runtime.models["Claude"].provider == "VSPLab_Claude"
    assert runtime.models["Claude"].fallback == "EdenQwen"


def test_load_tasks_config_accepts_heartbeat_email_store_and_hypo_info_digest(tmp_path: Path) -> None:
    tasks_yaml = tmp_path / "tasks.yaml"
    tasks_yaml.write_text(
        """
heartbeat:
  enabled: true
  interval_minutes: 1
  max_rounds: 8
  notion_today_match_mode: cover_today
email_store:
  enabled: true
  max_entries: 4000
  retention_days: 60
  warmup_hours: 72
hypo_info_digest:
  enabled: true
  interval_minutes: 480
  time: "09:00,21:00"
""".strip(),
        encoding="utf-8",
    )

    tasks = load_tasks_config(tasks_yaml)

    assert tasks.heartbeat.enabled is True
    assert tasks.heartbeat.interval_minutes == 1
    assert tasks.heartbeat.max_rounds == 8
    assert tasks.heartbeat.notion_today_match_mode == "cover_today"
    assert tasks.email_store.enabled is True
    assert tasks.email_store.max_entries == 4000
    assert tasks.email_store.retention_days == 60
    assert tasks.email_store.warmup_hours == 72
    assert tasks.hypo_info_digest.enabled is True
    assert tasks.hypo_info_digest.interval_minutes == 480
    assert tasks.hypo_info_digest.time == "09:00,21:00"


def test_load_tasks_config_accepts_wewe_rss_schedule(tmp_path: Path) -> None:
    tasks_yaml = tmp_path / "tasks.yaml"
    tasks_yaml.write_text(
        """
heartbeat:
  enabled: false
wewe_rss:
  enabled: true
  mode: interval
  interval_minutes: 15
""".strip(),
        encoding="utf-8",
    )

    tasks = load_tasks_config(tasks_yaml)

    assert tasks.wewe_rss.enabled is True
    assert tasks.wewe_rss.mode == "interval"
    assert tasks.wewe_rss.interval_minutes == 15


def test_load_tasks_config_accepts_hypo_info_digest(tmp_path: Path) -> None:
    tasks_yaml = tmp_path / "tasks.yaml"
    tasks_yaml.write_text(
        """
hearbeat:
  enabled: true
hypo_info_digest:
  enabled: true
  interval_minutes: 480
  time: "09:00,21:00"
""".strip(),
        encoding="utf-8",
    )

    tasks = load_tasks_config(tasks_yaml)

    assert tasks.hypo_info_digest.enabled is True
    assert tasks.hypo_info_digest.interval_minutes == 480
    assert tasks.hypo_info_digest.time == "09:00,21:00"


def test_secrets_config_accepts_services_hypo_info_default_shape() -> None:
    config = SecretsConfig.model_validate(
        {"providers": {}, "services": {"hypo_info": {"base_url": "http://localhost:8200"}}}
    )
    assert config.services is not None
    assert config.services.hypo_info is not None
    assert config.services.hypo_info.base_url == "http://localhost:8200"


def test_load_tasks_config_accepts_heartbeat_cron_schedule(tmp_path: Path) -> None:
    tasks_yaml = tmp_path / "tasks.yaml"
    tasks_yaml.write_text(
        """
heartbeat:
  enabled: true
  mode: cron
  cron: "*/10 * * * *"
""".strip(),
        encoding="utf-8",
    )

    tasks = load_tasks_config(tasks_yaml)

    assert tasks.heartbeat.enabled is True
    assert tasks.heartbeat.mode == "cron"
    assert tasks.heartbeat.cron == "*/10 * * * *"


def test_load_secrets_config_accepts_probe_service(tmp_path: Path) -> None:
    secrets_yaml = tmp_path / "secrets.yaml"
    secrets_yaml.write_text(
        """
providers: {}
services:
  probe:
    token: probe-secret
    screenshot_dir: memory/probe_screenshots
""".strip(),
        encoding="utf-8",
    )

    secrets = load_secrets_config(secrets_yaml)

    assert secrets.services is not None
    assert secrets.services.probe is not None
    assert secrets.services.probe.token == "probe-secret"
    assert secrets.services.probe.screenshot_dir == "memory/probe_screenshots"


def test_load_secrets_config_accepts_notion_service(tmp_path: Path) -> None:
    secrets_yaml = tmp_path / "secrets.yaml"
    secrets_yaml.write_text(
        """
providers: {}
services:
  notion:
    integration_secret: secret_xxx
    default_workspace: Hypo
    todo_database_id: todo-db
    proxy_url: http://127.0.0.1:7890
""".strip(),
        encoding="utf-8",
    )

    secrets = load_secrets_config(secrets_yaml)

    assert secrets.services is not None
    assert secrets.services.notion is not None
    assert secrets.services.notion.integration_secret == "secret_xxx"
    assert secrets.services.notion.default_workspace == "Hypo"
    assert secrets.services.notion.todo_database_id == "todo-db"
    assert secrets.services.notion.proxy_url == "http://127.0.0.1:7890"


def test_load_secrets_config_accepts_weibo_and_zhihu_services(tmp_path: Path) -> None:
    secrets_yaml = tmp_path / "secrets.yaml"
    secrets_yaml.write_text(
        """
providers: {}
services:
  weibo:
    cookie: SUB=demo; SUBP=demo
  weread:
    cookie: wr_skey=demo
  zhihu:
    cookie: z_c0=demo
""".strip(),
        encoding="utf-8",
    )

    secrets = load_secrets_config(secrets_yaml)

    assert secrets.services is not None
    assert secrets.services.weibo is not None
    assert secrets.services.weibo.cookie == "SUB=demo; SUBP=demo"
    assert secrets.services.weread is not None
    assert secrets.services.weread.cookie == "wr_skey=demo"
    assert secrets.services.zhihu is not None
    assert secrets.services.zhihu.cookie == "z_c0=demo"


def test_memory_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HYPO_MEMORY_DIR", raising=False)
    assert get_memory_dir() == (Path.cwd() / "memory").resolve(strict=False)


def test_memory_dir_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "mem-root"
    monkeypatch.setenv("HYPO_MEMORY_DIR", str(target))
    assert get_memory_dir() == target.resolve(strict=False)


def test_memory_dir_defaults_to_test_sandbox_in_test_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HYPO_MEMORY_DIR", raising=False)
    monkeypatch.setenv("HYPO_TEST_MODE", "1")

    assert get_memory_dir() == (tmp_path / "test" / "sandbox" / "memory").resolve(strict=False)


def test_memory_dir_ignores_custom_env_in_test_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HYPO_MEMORY_DIR", str(tmp_path / "production-memory"))
    monkeypatch.setenv("HYPO_TEST_MODE", "1")

    assert get_memory_dir() == (tmp_path / "test" / "sandbox" / "memory").resolve(strict=False)


def test_database_path_defaults_to_test_sandbox_in_test_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HYPO_DB_PATH", raising=False)
    monkeypatch.setenv("HYPO_TEST_MODE", "1")

    assert get_database_path() == (tmp_path / "test" / "sandbox" / "hypo.db").resolve(strict=False)


def test_database_path_ignores_custom_env_in_test_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HYPO_DB_PATH", str(tmp_path / "production.db"))
    monkeypatch.setenv("HYPO_TEST_MODE", "1")

    assert get_database_path() == (tmp_path / "test" / "sandbox" / "hypo.db").resolve(strict=False)


def test_port_default_switches_to_test_mode_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HYPO_PORT", raising=False)
    monkeypatch.setenv("HYPO_TEST_MODE", "1")

    assert get_port() == 8766


def test_render_persona_system_prompt_injects_runtime_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "Hypo-Agent"
    repo_root.mkdir(parents=True)
    persona_yaml = tmp_path / "persona.yaml"
    persona_yaml.write_text(
        """
name: Hypo
aliases: [hypo]
personality: [pragmatic]
speaking_style:
  tone: direct
system_prompt_template: |
  你是 Hypo。

  ## 环境信息
  - 代码仓库：${HYPO_AGENT_ROOT}
  - 服务器：${HYPO_SERVER_NAME}
  - 用户名：${HYPO_USERNAME}
  - Conda：${HYPO_CONDA_ENV}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("HYPO_AGENT_ROOT", str(repo_root))
    monkeypatch.setenv("HYPO_SERVER_NAME", "Genesis")
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "HypoAgent")

    persona = load_persona_config(persona_yaml)
    rendered = render_persona_system_prompt(persona)

    assert "## 环境信息" in rendered
    assert str(repo_root) in rendered
    assert "Genesis" in rendered
    assert "HypoAgent" in rendered
    assert "${HYPO_AGENT_ROOT}" not in rendered


def test_load_persona_config_accepts_multiline_personality_string(tmp_path: Path) -> None:
    persona_yaml = tmp_path / "persona.yaml"
    persona_yaml.write_text(
        """
name: Hypo
aliases: [hypo]
personality: |
  搞心态沙雕小助手，日常吐槽、整活、逗乐；
  遇到正事立刻切换专业模式，认真高效；
  能自动识别并接受新昵称。
speaking_style:
  tone: direct
""".strip(),
        encoding="utf-8",
    )

    persona = load_persona_config(persona_yaml)

    assert persona.personality == [
        "搞心态沙雕小助手，日常吐槽、整活、逗乐",
        "遇到正事立刻切换专业模式，认真高效",
        "能自动识别并接受新昵称。",
    ]


def test_render_persona_system_prompt_includes_speaking_style_habits(tmp_path: Path) -> None:
    persona_yaml = tmp_path / "persona.yaml"
    persona_yaml.write_text(
        """
name: Hypo
aliases: [hypo]
personality: [pragmatic]
speaking_style:
  tone: direct
  habits:
    - 回答完直接结束
    - 不要主动给下一步建议
""".strip(),
        encoding="utf-8",
    )

    rendered = render_persona_system_prompt(load_persona_config(persona_yaml))

    assert "表达风格：direct" in rendered
    assert "行为边界：" in rendered
    assert "- 回答完直接结束" in rendered
    assert "- 不要主动给下一步建议" in rendered


def test_default_persona_mentions_directory_index_knowledge_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HYPO_AGENT_ROOT", "/home/heyx/Hypo-Agent")
    fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / "example_persona.yaml"

    rendered = render_persona_system_prompt(load_persona_config(fixture_path))

    assert "memory/knowledge/directory_index.yaml" in rendered


def test_runtime_configs_use_hypo_info_defaults() -> None:
    tasks_text = Path("config/tasks.yaml").read_text(encoding="utf-8")
    security_text = Path("config/security.yaml").read_text(encoding="utf-8")
    skills_text = Path("config/skills.yaml").read_text(encoding="utf-8")

    assert "hypo_info_digest:" in tasks_text
    assert "trendradar_summary:" not in tasks_text
    assert "~/trendradar/output" not in security_text
    assert "output_root:" not in skills_text


def test_load_narration_config_accepts_tool_levels(tmp_path: Path) -> None:
    narration_yaml = tmp_path / "narration.yaml"
    narration_yaml.write_text(
        """
enabled: true
model: GenesiQWen35BA3B
tool_levels:
  heavy:
    - scan_emails
    - exec_command
  medium:
    - write_file
tool_narration:
  update_reminder:
    template: "⏰ 正在更新提醒「{title}」..."
    fallback: "⏰ 正在更新提醒..."
llm_timeout_ms: 800
llm_repeat_threshold: 3
dedup_max_consecutive: 2
debounce_seconds: 2
max_narration_length: 80
""".strip(),
        encoding="utf-8",
    )

    config = load_narration_config(narration_yaml)

    assert config.enabled is True
    assert config.model == "GenesiQWen35BA3B"
    assert config.tool_levels.heavy == ["scan_emails", "exec_command"]
    assert config.tool_levels.medium == ["write_file"]
    assert config.tool_narration["update_reminder"].template == "⏰ 正在更新提醒「{title}」..."
    assert config.tool_narration["update_reminder"].fallback == "⏰ 正在更新提醒..."
    assert config.llm_timeout_ms == 800
    assert config.llm_repeat_threshold == 3
    assert config.dedup_max_consecutive == 2
    assert config.debounce_seconds == 2
    assert config.max_narration_length == 80
