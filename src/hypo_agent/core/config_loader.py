from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from hypo_agent.models import ModelConfig, SecretsConfig


class ResolvedModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    litellm_model: str | None = None
    fallback: str | None = None
    api_base: str | None = None
    api_key: str | None = None


class RuntimeModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_model: str
    task_routing: dict[str, str] = Field(default_factory=dict)
    models: dict[str, ResolvedModelConfig] = Field(default_factory=dict)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected YAML mapping in {path}")
    return payload


def load_runtime_model_config(
    models_path: Path | str = "config/models.yaml",
    secrets_path: Path | str = "config/secrets.yaml",
) -> RuntimeModelConfig:
    models_payload = _load_yaml(Path(models_path))
    secrets_payload = _load_yaml(Path(secrets_path))

    model_config = ModelConfig.model_validate(models_payload)
    secrets_config = SecretsConfig.model_validate(secrets_payload)

    resolved_models: dict[str, ResolvedModelConfig] = {}
    for name, model in model_config.models.items():
        api_base: str | None = None
        api_key: str | None = None

        if model.provider is not None:
            provider = secrets_config.providers.get(model.provider)
            if provider is None:
                raise ValueError(
                    f"Provider '{model.provider}' required by model '{name}' is missing"
                )
            api_base = provider.api_base
            api_key = provider.api_key

        resolved_models[name] = ResolvedModelConfig(
            provider=model.provider,
            litellm_model=model.litellm_model,
            fallback=model.fallback,
            api_base=api_base,
            api_key=api_key,
        )

    if model_config.default_model not in resolved_models:
        raise ValueError(
            f"default_model '{model_config.default_model}' is not defined in models"
        )

    return RuntimeModelConfig(
        default_model=model_config.default_model,
        task_routing=model_config.task_routing,
        models=resolved_models,
    )
