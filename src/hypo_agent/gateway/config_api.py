from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
import yaml

from hypo_agent.gateway.auth import require_api_token
from hypo_agent.models import (
    ModelConfig,
    NarrationConfig,
    PersonaConfig,
    SecretsConfig,
    SecurityConfig,
    TasksConfig,
)

router = APIRouter(prefix="/api")

MASKED_VALUE = "••••••••"
SENSITIVE_KEYWORDS = ("token", "password", "secret", "key")

CONFIG_LIST: list[dict[str, str]] = [
    {
        "filename": "persona.yaml",
        "label": "人设配置",
        "icon": "🎭",
        "description": "Agent 的性格、语气、系统提示词",
    },
    {
        "filename": "skills.yaml",
        "label": "技能配置",
        "icon": "🔧",
        "description": "各技能开关与参数",
    },
    {
        "filename": "tasks.yaml",
        "label": "定时任务",
        "icon": "⏰",
        "description": "Heartbeat 与邮件缓存等定时任务配置",
    },
    {
        "filename": "narration.yaml",
        "label": "旁白配置",
        "icon": "💬",
        "description": "工具调用旁白的开关、模型、分级",
    },
    {
        "filename": "email_rules.yaml",
        "label": "邮件规则",
        "icon": "📧",
        "description": "邮件分类硬规则 + LLM 偏好",
    },
    {
        "filename": "secrets.yaml",
        "label": "密钥配置",
        "icon": "🔐",
        "description": "API Token、密码等敏感配置（脱敏显示）",
    },
    {
        "filename": "security.yaml",
        "label": "安全白名单",
        "icon": "🛡️",
        "description": "文件系统访问白名单",
    },
]

SUPPORTED_CONFIG_FILES = {
    "models.yaml",
    *(item["filename"] for item in CONFIG_LIST),
}


class ConfigUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    data: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _require_content_or_data(self) -> "ConfigUpdatePayload":
        if self.content is None and self.data is None:
            raise ValueError("content or data is required")
        return self


class SkillConfigEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    deprecated: bool | None = None
    timeout_seconds: int | None = None
    auto_confirm: bool | None = None


class SkillsConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_timeout_seconds: int = 30
    skills: dict[str, SkillConfigEntry] = Field(default_factory=dict)


class TasksConfigFile(TasksConfig):
    model_config = ConfigDict(extra="allow")


def _config_root(request: Request) -> Path:
    root = getattr(request.app.state, "config_dir", "config")
    return Path(root)


def _config_path(request: Request, filename: str) -> Path:
    return _config_root(request) / filename


def _dump_yaml(payload: dict[str, Any]) -> str:
    return yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


def _load_yaml_text(path: Path) -> tuple[str, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    content = path.read_text(encoding="utf-8")
    payload = yaml.safe_load(content) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root must be a mapping: {path.name}")
    return content, payload


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(keyword in lowered for keyword in SENSITIVE_KEYWORDS)


def _mask_sensitive_values(payload: Any, *, path: str = "") -> tuple[Any, list[str]]:
    if isinstance(payload, dict):
        masked: dict[str, Any] = {}
        masked_fields: list[str] = []
        for key, value in payload.items():
            next_path = f"{path}.{key}" if path else key
            if _is_sensitive_key(key):
                masked[key] = MASKED_VALUE
                masked_fields.append(next_path)
                continue
            child_value, child_fields = _mask_sensitive_values(value, path=next_path)
            masked[key] = child_value
            masked_fields.extend(child_fields)
        return masked, masked_fields
    if isinstance(payload, list):
        masked_items: list[Any] = []
        masked_fields: list[str] = []
        for index, item in enumerate(payload):
            item_path = f"{path}[{index}]"
            child_value, child_fields = _mask_sensitive_values(item, path=item_path)
            masked_items.append(child_value)
            masked_fields.extend(child_fields)
        return masked_items, masked_fields
    return payload, []


def _merge_masked_values(original: Any, submitted: Any) -> Any:
    if isinstance(submitted, dict):
        original_dict = original if isinstance(original, dict) else {}
        merged: dict[str, Any] = {}
        for key, value in submitted.items():
            original_value = original_dict.get(key)
            if _is_sensitive_key(key) and value == MASKED_VALUE:
                merged[key] = original_value
                continue
            merged[key] = _merge_masked_values(original_value, value)
        return merged
    if isinstance(submitted, list):
        original_items = original if isinstance(original, list) else []
        return [
            _merge_masked_values(original_items[index] if index < len(original_items) else None, item)
            for index, item in enumerate(submitted)
        ]
    return submitted


def _validate_config_payload(filename: str, payload: dict[str, Any]) -> None:
    if filename == "models.yaml":
        ModelConfig.model_validate(payload)
        return
    if filename == "skills.yaml":
        SkillsConfigFile.model_validate(payload)
        return
    if filename == "security.yaml":
        token = str(payload.get("auth_token", "")).strip()
        if not token:
            raise ValueError("auth_token is required in security.yaml")
        SecurityConfig.model_validate(
            {
                "directory_whitelist": payload.get("directory_whitelist", {}),
                "circuit_breaker": payload.get("circuit_breaker", {}),
            }
        )
        return
    if filename == "persona.yaml":
        PersonaConfig.model_validate(payload)
        return
    if filename == "tasks.yaml":
        TasksConfigFile.model_validate(payload)
        return
    if filename == "narration.yaml":
        NarrationConfig.model_validate(payload)
        return
    if filename == "secrets.yaml":
        SecretsConfig.model_validate(payload)
        return
    if filename == "email_rules.yaml":
        return
    raise ValueError(f"Unsupported config file '{filename}'")


def _serialize_config_response(
    *,
    filename: str,
    payload: dict[str, Any],
    content: str,
) -> dict[str, Any]:
    masked_fields: list[str] = []
    response_payload = payload
    response_content = content

    if filename == "secrets.yaml":
        response_payload, masked_fields = _mask_sensitive_values(payload)
        response_content = _dump_yaml(response_payload)

    return {
        "filename": filename,
        "content": response_content,
        "data": response_payload,
        "masked_fields": masked_fields,
    }


async def _trigger_reload(request: Request) -> None:
    reload_handler = getattr(request.app.state.deps, "reload_config", None)
    if reload_handler is None:
        reload_handler = getattr(request.app.state, "reload_config", None)
    if reload_handler is None:
        return

    result = reload_handler()
    if inspect.isawaitable(result):
        await result


@router.get("/config")
async def list_config_files(request: Request) -> list[dict[str, Any]]:
    require_api_token(request)

    root = _config_root(request)
    return [
        {
            **item,
            "exists": (root / item["filename"]).exists(),
            "editable": True,
        }
        for item in CONFIG_LIST
    ]


@router.get("/config/files")
async def list_config_files_legacy(request: Request) -> dict[str, Any]:
    require_api_token(request)
    return {"files": await list_config_files(request)}


@router.get("/config/{filename}")
async def get_config_file(filename: str, request: Request) -> dict[str, Any]:
    require_api_token(request)

    if filename not in SUPPORTED_CONFIG_FILES:
        raise HTTPException(status_code=404, detail="Config file not found")

    path = _config_path(request, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Config file not found")

    try:
        content, payload = _load_yaml_text(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Config file not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid YAML: {exc}") from exc

    return _serialize_config_response(filename=filename, payload=payload, content=content)


@router.put("/config/{filename}")
async def update_config_file(
    filename: str,
    payload: ConfigUpdatePayload,
    request: Request,
) -> dict[str, Any]:
    require_api_token(request)

    if filename not in SUPPORTED_CONFIG_FILES:
        raise HTTPException(status_code=404, detail="Config file not found")

    path = _config_path(request, filename)
    existing_payload: dict[str, Any] = {}
    if path.exists():
        try:
            _, existing_payload = _load_yaml_text(path)
        except (ValueError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        if payload.data is not None:
            submitted_payload = payload.data
            serialized_content = _dump_yaml(submitted_payload)
        else:
            assert payload.content is not None
            submitted_payload = yaml.safe_load(payload.content) or {}
            if not isinstance(submitted_payload, dict):
                raise ValueError("YAML root must be a mapping")
            serialized_content = payload.content

        merged_payload = (
            _merge_masked_values(existing_payload, submitted_payload)
            if filename == "secrets.yaml"
            else submitted_payload
        )
        if not isinstance(merged_payload, dict):
            raise ValueError("YAML root must be a mapping")

        _validate_config_payload(filename, merged_payload)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid YAML: {exc}") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    if payload.data is not None or filename == "secrets.yaml":
        serialized_content = _dump_yaml(merged_payload)
    path.write_text(serialized_content, encoding="utf-8")

    try:
        await _trigger_reload(request)
    except Exception as exc:  # pragma: no cover - defensive fallback
        raise HTTPException(status_code=500, detail=f"Config reload failed: {exc}") from exc

    response = _serialize_config_response(
        filename=filename,
        payload=merged_payload,
        content=serialized_content,
    )
    response["reloaded"] = True
    return response
