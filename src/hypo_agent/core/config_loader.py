from __future__ import annotations

import getpass
import os
from pathlib import Path
import platform
import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from hypo_agent.models import (
    ModelConfig,
    NarrationConfig,
    PersonaConfig,
    SecretsConfig,
    TasksConfig,
    WeixinServiceConfig,
)

_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


class ResolvedModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "chat"
    provider: str | None = None
    litellm_model: str | None = None
    fallback: str | None = None
    supports_tool_calling: bool | None = None
    context_window: int | None = None
    description: str | None = None
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


def _looks_like_agent_root(path: Path) -> bool:
    return (path / "src").exists() and (path / "config").exists()


def _read_os_summary() -> str:
    os_release = Path("/etc/os-release")
    if os_release.exists():
        for line in os_release.read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    return platform.platform()


def get_agent_root() -> Path:
    raw = os.getenv("HYPO_AGENT_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve(strict=False)

    cwd = Path.cwd().resolve(strict=False)
    if _looks_like_agent_root(cwd):
        return cwd

    project_root = Path(__file__).resolve().parents[3]
    if _looks_like_agent_root(project_root):
        return project_root

    return cwd


def build_runtime_template_context(
    extra_context: dict[str, str] | None = None,
) -> dict[str, str]:
    context = {
        "HYPO_AGENT_ROOT": str(get_agent_root()),
        "HYPO_OS": _read_os_summary(),
        "HYPO_USERNAME": getpass.getuser(),
        "HYPO_CONDA_ENV": os.getenv("CONDA_DEFAULT_ENV", "").strip() or "HypoAgent",
        "HYPO_SERVER_NAME": os.getenv("HYPO_SERVER_NAME", "").strip() or "Genesis",
    }
    if extra_context:
        context.update({key: str(value) for key, value in extra_context.items()})
    return context


def expand_runtime_placeholders(
    value: str,
    *,
    extra_context: dict[str, str] | None = None,
) -> str:
    context = build_runtime_template_context(extra_context)

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return context.get(key, match.group(0))

    return _PLACEHOLDER_PATTERN.sub(_replace, value)


def expand_runtime_payload(
    payload: Any,
    *,
    extra_context: dict[str, str] | None = None,
) -> Any:
    if isinstance(payload, str):
        return expand_runtime_placeholders(payload, extra_context=extra_context)
    if isinstance(payload, list):
        return [expand_runtime_payload(item, extra_context=extra_context) for item in payload]
    if isinstance(payload, dict):
        return {
            key: expand_runtime_payload(value, extra_context=extra_context)
            for key, value in payload.items()
        }
    return payload


def _resolve_api_key(raw_api_key: str, *, provider_name: str, model_name: str) -> str:
    api_key = raw_api_key.strip()
    if not api_key:
        raise ValueError(
            f"Provider '{provider_name}' required by model '{model_name}' has empty api_key"
        )
    if not api_key.startswith("$"):
        return api_key

    env_name = api_key[1:]
    env_value = os.getenv(env_name, "").strip()
    if not env_value:
        raise ValueError(
            f"Environment variable '{env_name}' required by provider "
            f"'{provider_name}' for model '{model_name}' is missing"
        )
    return env_value


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
            api_base = (provider.api_base or "").strip() or None
            api_key = _resolve_api_key(
                provider.api_key,
                provider_name=model.provider,
                model_name=name,
            )

        resolved_models[name] = ResolvedModelConfig(
            type=model.type,
            provider=model.provider,
            litellm_model=model.litellm_model,
            fallback=model.fallback,
            supports_tool_calling=model.supports_tool_calling,
            context_window=model.context_window,
            description=model.description,
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


def load_secrets_config(
    secrets_path: Path | str = "config/secrets.yaml",
) -> SecretsConfig:
    secrets_payload = _load_yaml(Path(secrets_path))
    return SecretsConfig.model_validate(secrets_payload)


def load_persona_config(
    persona_path: Path | str = "config/persona.yaml",
) -> PersonaConfig:
    persona_payload = _load_yaml(Path(persona_path))
    return PersonaConfig.model_validate(persona_payload)


def load_narration_config(
    narration_path: Path | str = "config/narration.yaml",
) -> NarrationConfig:
    narration_payload = _load_yaml(Path(narration_path))
    return NarrationConfig.model_validate(narration_payload)


def normalize_speaking_style_habits(speaking_style: dict[str, Any]) -> list[str]:
    raw_habits = speaking_style.get("habits")
    if raw_habits is None:
        return []
    if isinstance(raw_habits, str):
        return [raw_habits.strip()] if raw_habits.strip() else []
    if isinstance(raw_habits, list):
        return [str(item).strip() for item in raw_habits if str(item).strip()]
    return []


def render_persona_system_prompt(
    persona: PersonaConfig | Path | str = "config/persona.yaml",
    *,
    extra_context: dict[str, str] | None = None,
) -> str:
    persona_config = (
        load_persona_config(persona)
        if isinstance(persona, (str, Path))
        else persona
    )

    template = persona_config.system_prompt_template.strip()
    if template:
        return expand_runtime_placeholders(template, extra_context=extra_context).strip()

    lines = [f"你是 {persona_config.name}。"]
    if persona_config.personality:
        lines.append("人格特征：" + "、".join(persona_config.personality))
    tone = persona_config.speaking_style.get("tone")
    if tone:
        lines.append(f"表达风格：{tone}")
    habits = normalize_speaking_style_habits(persona_config.speaking_style)
    if habits:
        lines.append("行为边界：")
        lines.extend(f"- {item}" for item in habits)
    return "\n".join(lines).strip()


def load_tasks_config(
    tasks_path: Path | str = "config/tasks.yaml",
) -> TasksConfig:
    tasks_payload = _load_yaml(Path(tasks_path))
    return TasksConfig.model_validate(tasks_payload)


def is_test_mode() -> bool:
    return os.getenv("HYPO_TEST_MODE", "").strip() == "1"


def get_test_sandbox_dir() -> Path:
    raw = os.getenv("HYPO_TEST_SANDBOX_DIR", "").strip() or "./test/sandbox"
    return Path(raw).expanduser().resolve(strict=False)


def get_memory_dir() -> Path:
    """Return the configured memory directory (defaults to ./memory).

    This is intentionally resolved at call time so tests / processes can
    override it via HYPO_MEMORY_DIR.
    """

    if is_test_mode():
        return (get_test_sandbox_dir() / "memory").resolve(strict=False)

    raw = os.getenv("HYPO_MEMORY_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve(strict=False)

    raw = "./memory"
    return Path(raw).expanduser().resolve(strict=False)


def get_database_path() -> Path:
    if is_test_mode():
        return (get_test_sandbox_dir() / "hypo.db").resolve(strict=False)

    raw = os.getenv("HYPO_DB_PATH", "").strip()
    if raw:
        return Path(raw).expanduser().resolve(strict=False)

    return (get_memory_dir() / "hypo.db").resolve(strict=False)


def get_port(*, default: int = 8765) -> int:
    raw = os.getenv("HYPO_PORT", "").strip()
    if not raw:
        return 8766 if is_test_mode() else default

    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("HYPO_PORT must be an integer") from exc

    if port < 1 or port > 65535:
        raise ValueError("HYPO_PORT must be between 1 and 65535")

    return port
