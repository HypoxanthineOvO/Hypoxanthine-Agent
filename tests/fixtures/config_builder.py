from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
            continue
        merged[key] = deepcopy(value)
    return merged


def make_config(overrides: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Build a minimal config tree for tests, with optional per-file overrides."""

    base = {
        "skills": {
            "default_timeout_seconds": 30,
            "skills": {
                "memory": {"enabled": True},
                "email_scanner": {"enabled": False},
            },
        },
        "secrets": {
            "providers": {},
            "services": {},
        },
        "tasks": {
            "heartbeat": {"enabled": False, "interval_minutes": 60},
            "email_scan": {"enabled": False, "interval_minutes": 60},
            "email_store": {
                "enabled": False,
                "warmup_hours": 24,
                "max_entries": 200,
                "retention_days": 30,
            },
        },
        "config": {
            "channels": {
                "feishu": {"enabled": False},
                "qq": {"enabled": False},
                "qq_bot": {"enabled": False},
                "weixin": {"enabled": False},
            }
        },
        "security": {
            "auth_token": "test-token",
            "security": {"auth_token": "test-token"},
        },
    }
    return _deep_merge(base, overrides or {})


def write_config_tree(config_dir: Path, overrides: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Write a config tree produced by make_config() into a test config directory."""

    payloads = make_config(overrides=overrides)
    config_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        (config_dir / f"{name}.yaml").write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    return payloads
