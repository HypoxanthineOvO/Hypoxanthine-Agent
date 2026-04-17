from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


ROOT_DIR = Path(__file__).resolve().parents[3]
SECRETS_PATH = ROOT_DIR / "config" / "secrets.yaml"


def load_secrets_payload() -> dict[str, Any]:
    if not SECRETS_PATH.exists():
        return {}
    payload = yaml.safe_load(SECRETS_PATH.read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {}


def _lookup_mapping_value(payload: dict[str, Any], keys: list[str]) -> str:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    if current is None:
        return ""
    return str(current).strip()


def get_secret_value(*paths: str) -> str:
    payload = load_secrets_payload()
    for raw_path in paths:
        value = _lookup_mapping_value(payload, raw_path.split("."))
        if value:
            return value
    return ""


def get_env_or_secret(*env_names: str, secret_paths: list[str] | None = None) -> str:
    for env_name in env_names:
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    if secret_paths:
        return get_secret_value(*secret_paths)
    return ""


def parse_cookie_string(raw_cookie: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for chunk in raw_cookie.split(";"):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            cookies[key] = value
    return cookies


def merge_cookie_sources(
    raw_cookie: str,
    *,
    env_prefix: str,
    key_map: dict[str, str],
) -> dict[str, str]:
    cookies = parse_cookie_string(raw_cookie)
    for cookie_key, env_suffix in key_map.items():
        env_name = f"{env_prefix}_{env_suffix}"
        value = os.getenv(env_name, "").strip()
        if value:
            cookies[cookie_key] = value
    return cookies


def mask_cookie_keys(cookies: dict[str, str]) -> str:
    if not cookies:
        return "<none>"
    keys = sorted(cookies)
    return ", ".join(keys)
