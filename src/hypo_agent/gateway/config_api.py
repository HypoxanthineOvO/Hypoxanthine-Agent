from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
import yaml

from hypo_agent.gateway.auth import require_api_token
from hypo_agent.models import ModelConfig, PersonaConfig, SecurityConfig, TasksConfig

router = APIRouter(prefix="/api")

EDITABLE_CONFIG_FILES = [
    "models.yaml",
    "skills.yaml",
    "security.yaml",
    "persona.yaml",
    "tasks.yaml",
]


class ConfigUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


class SkillConfigEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    timeout_seconds: int | None = None


class SkillsConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_timeout_seconds: int = 30
    skills: dict[str, SkillConfigEntry] = Field(default_factory=dict)


class TasksConfigFile(TasksConfig):
    model_config = ConfigDict(extra="allow")


def _config_root(request: Request) -> Path:
    root = getattr(request.app.state, "config_dir", "config")
    return Path(root)


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

    raise ValueError(f"Unsupported config file '{filename}'")


async def _trigger_reload(request: Request) -> None:
    reload_handler = getattr(request.app.state.deps, "reload_config", None)
    if reload_handler is None:
        reload_handler = getattr(request.app.state, "reload_config", None)
    if reload_handler is None:
        return

    result = reload_handler()
    if inspect.isawaitable(result):
        await result


@router.get("/config/files")
async def list_config_files(request: Request) -> dict[str, Any]:
    require_api_token(request)

    root = _config_root(request)
    files = [
        {
            "filename": filename,
            "exists": (root / filename).exists(),
            "editable": True,
        }
        for filename in EDITABLE_CONFIG_FILES
    ]
    return {"files": files}


@router.get("/config/{filename}")
async def get_config_file(filename: str, request: Request) -> dict[str, Any]:
    require_api_token(request)

    if filename not in EDITABLE_CONFIG_FILES:
        raise HTTPException(status_code=404, detail="Config file not found")

    path = _config_root(request) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Config file not found")

    return {
        "filename": filename,
        "content": path.read_text(encoding="utf-8"),
    }


@router.put("/config/{filename}")
async def update_config_file(
    filename: str,
    payload: ConfigUpdatePayload,
    request: Request,
) -> dict[str, Any]:
    require_api_token(request)

    if filename not in EDITABLE_CONFIG_FILES:
        raise HTTPException(status_code=404, detail="Config file not found")

    try:
        parsed = yaml.safe_load(payload.content) or {}
        if not isinstance(parsed, dict):
            raise ValueError("YAML root must be a mapping")
        _validate_config_payload(filename, parsed)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid YAML: {exc}") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    path = _config_root(request) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload.content, encoding="utf-8")

    try:
        await _trigger_reload(request)
    except Exception as exc:  # pragma: no cover - defensive fallback
        raise HTTPException(status_code=500, detail=f"Config reload failed: {exc}") from exc

    return {
        "filename": filename,
        "reloaded": True,
    }
