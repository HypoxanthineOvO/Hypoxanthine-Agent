from pathlib import Path

import pytest

from hypo_agent.core.config_loader import (
    get_memory_dir,
    load_runtime_model_config,
    load_tasks_config,
)


def test_load_runtime_model_config_merges_models_and_secrets(tmp_path: Path) -> None:
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
    fallback: DeepseekV3_2
  DeepseekV3_2:
    provider: Volcengine
    litellm_model: openai/ep-20251215171209-4z5qk
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
""".strip(),
        encoding="utf-8",
    )

    runtime = load_runtime_model_config(models_yaml, secrets_yaml)

    assert runtime.default_model == "Gemini3Pro"
    assert runtime.models["Gemini3Pro"].api_base == "https://hiapi.online/v1"
    assert runtime.models["DeepseekV3_2"].api_key == "volc-key"


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


def test_load_tasks_config_accepts_heartbeat_and_email_scan(tmp_path: Path) -> None:
    tasks_yaml = tmp_path / "tasks.yaml"
    tasks_yaml.write_text(
        """
heartbeat:
  enabled: true
  interval_minutes: 1
email_scan:
  enabled: true
  interval_minutes: 5
""".strip(),
        encoding="utf-8",
    )

    tasks = load_tasks_config(tasks_yaml)

    assert tasks.heartbeat.enabled is True
    assert tasks.heartbeat.interval_minutes == 1
    assert tasks.email_scan.enabled is True
    assert tasks.email_scan.interval_minutes == 5


def test_memory_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HYPO_MEMORY_DIR", raising=False)
    assert get_memory_dir() == (Path.cwd() / "memory").resolve(strict=False)


def test_memory_dir_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "mem-root"
    monkeypatch.setenv("HYPO_MEMORY_DIR", str(target))
    assert get_memory_dir() == target.resolve(strict=False)
