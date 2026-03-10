from pathlib import Path

import pytest

from hypo_agent.core.config_loader import load_runtime_model_config


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
