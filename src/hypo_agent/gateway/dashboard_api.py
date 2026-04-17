from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
import math
from pathlib import Path
import shutil
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
import structlog

from hypo_agent.core.recent_logs import get_recent_logs
from hypo_agent.core.config_loader import load_secrets_config
from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.gateway.settings import load_channel_settings
from hypo_agent.gateway.auth import require_api_token
from hypo_agent.gateway.ws import connection_manager
from hypo_agent.utils.timeutil import now_local, to_local

router = APIRouter(prefix="/api")
logger = structlog.get_logger("hypo_agent.gateway.dashboard_api")


def _error_fields(exc: Exception) -> dict[str, str]:
    message = str(exc).strip()
    if len(message) > 200:
        message = f"{message[:197]}..."
    return {
        "error_type": type(exc).__name__,
        "error_msg": message,
    }


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _day_key(dt: datetime) -> str:
    return to_local(dt).date().isoformat()


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    rank = (len(values) - 1) * q
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return float(values[lower])
    weight = rank - lower
    return float(values[lower] * (1.0 - weight) + values[upper] * weight)


def _configured_skill_enabled_map(path: Path | str) -> dict[str, bool]:
    payload = SkillManager._load_skills_payload(path)
    configured_skills = payload.get("skills", {})
    if not isinstance(configured_skills, dict):
        return {}

    enabled_map: dict[str, bool] = {}
    for name, cfg in configured_skills.items():
        enabled_map[str(name)] = isinstance(cfg, dict) and bool(cfg.get("enabled", False))
    return enabled_map


def _external_skill_runtime(request: Request, name: str) -> dict[str, Any] | None:
    if name == "qq":
        return {
            "description": "QQ channel integration",
            "tools": [],
            "active": getattr(request.app.state, "qq_channel_service", None) is not None,
        }
    return None


def _qq_enabled_from_secrets(config_dir: Path) -> bool:
    secrets_path = config_dir / "secrets.yaml"
    if not secrets_path.exists():
        return False
    try:
        secrets = load_secrets_config(secrets_path)
    except Exception as exc:
        logger.warning(
            "dashboard.channel_status.degraded",
            channel="qq",
            operation="load_qq_config",
            **_error_fields(exc),
        )
        return False
    services = secrets.services
    qq_cfg = getattr(services, "qq", None) if services is not None else None
    if qq_cfg is None:
        return False
    return bool(
        str(getattr(qq_cfg, "napcat_ws_url", "") or "").strip()
        and str(getattr(qq_cfg, "napcat_http_url", "") or "").strip()
        and str(getattr(qq_cfg, "bot_qq", "") or "").strip()
    )


def _disabled_qq_napcat_status() -> dict[str, Any]:
    return {
        "status": "disabled",
        "bot_qq": "",
        "napcat_ws_url": "",
        "connected_at": None,
        "last_message_at": None,
        "messages_received": 0,
        "messages_sent": 0,
        "online": None,
        "good": None,
    }


def _disabled_qq_bot_status() -> dict[str, Any]:
    return {
        "status": "disabled",
        "qq_bot_enabled": False,
        "qq_bot_app_id": "",
        "ws_connected": False,
        "connected_at": None,
        "last_message_at": None,
        "messages_received": 0,
        "messages_sent": 0,
    }


def _mask_app_id(app_id: str | None) -> str:
    value = str(app_id or "").strip()
    if not value:
        return ""
    return f"••••{value[-4:]}" if len(value) >= 4 else "••••"


def _disabled_email_status() -> dict[str, Any]:
    return {
        "status": "disabled",
        "accounts": [],
        "last_scan_at": None,
        "next_scan_at": None,
        "emails_processed": 0,
    }


def _disabled_weixin_status() -> dict[str, Any]:
    return {
        "status": "disabled",
        "bot_id": "",
        "user_id": "",
        "last_message_at": None,
        "messages_received": 0,
        "messages_sent": 0,
    }


def _disabled_feishu_status() -> dict[str, Any]:
    return {
        "status": "disabled",
        "app_id": "",
        "chat_count": 0,
        "last_message_at": None,
        "messages_received": 0,
        "messages_sent": 0,
    }


def _disabled_heartbeat_status() -> dict[str, Any]:
    return {
        "status": "disabled",
        "last_heartbeat_at": None,
        "active_tasks": 0,
    }


@router.get("/dashboard/status")
async def dashboard_status(request: Request) -> dict[str, Any]:
    require_api_token(request)

    started_at = getattr(request.app.state, "started_at", now_local())
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    now = now_local()
    uptime_seconds = max((now - started_at).total_seconds(), 0.0)

    structured_store = request.app.state.structured_store
    sessions = await structured_store.list_sessions()
    deps = request.app.state.deps
    circuit_breaker = getattr(deps, "circuit_breaker", None)
    kill_switch = bool(circuit_breaker.get_global_kill_switch()) if circuit_breaker else False

    return {
        "uptime_seconds": uptime_seconds,
        "uptime_human": str(timedelta(seconds=int(uptime_seconds))),
        "session_count": len(sessions),
        "kill_switch": kill_switch,
        "bwrap_available": bool(shutil.which("bwrap")),
    }


@router.get("/channels/status")
async def channels_status(request: Request) -> dict[str, Any]:
    require_api_token(request)

    config_dir = Path(getattr(request.app.state, "config_dir", Path("config")))
    configured_enabled = _configured_skill_enabled_map(config_dir / "skills.yaml")
    deps = request.app.state.deps
    skill_manager = getattr(deps, "skill_manager", None)
    scheduler = getattr(request.app.state, "scheduler", None)

    qq_enabled = _qq_enabled_from_secrets(config_dir)
    email_enabled = configured_enabled.get("email_scanner", False)

    qq_bot_config = None
    secrets_path = config_dir / "secrets.yaml"
    if secrets_path.exists():
        try:
            secrets = load_secrets_config(secrets_path)
        except Exception as exc:
            logger.warning(
                "dashboard.channel_status.degraded",
                channel="qq_bot",
                operation="load_qq_bot_config",
                **_error_fields(exc),
            )
            qq_bot_config = None
        else:
            services = secrets.services
            qq_bot_config = services.qq_bot if services is not None else None
    qq_bot_enabled = bool(
        qq_bot_config
        and qq_bot_config.enabled
        and str(qq_bot_config.app_id or "").strip()
        and str(qq_bot_config.app_secret or "").strip()
    )

    qq_bot_service = getattr(request.app.state, "qq_bot_channel_service", None)
    qq_napcat_status: dict[str, Any] | None = _disabled_qq_napcat_status() if qq_enabled else None
    qq_bot_status = _disabled_qq_bot_status()
    if qq_bot_enabled:
        if qq_bot_service is not None and hasattr(qq_bot_service, "get_status"):
            qq_bot_status = qq_bot_service.get_status()
        else:
            qq_bot_status = {
                "status": "enabled" if qq_bot_config and str(qq_bot_config.app_id or "").strip() else "disabled",
                "qq_bot_enabled": bool(qq_bot_config and qq_bot_config.enabled),
                "qq_bot_app_id": _mask_app_id(getattr(qq_bot_config, "app_id", "")),
                "ws_connected": False,
                "connected_at": None,
                "last_message_at": None,
                "messages_received": 0,
                "messages_sent": 0,
            }
        qq_bot_client = getattr(request.app.state, "qq_ws_client", None)
        if qq_bot_client is not None and hasattr(qq_bot_client, "get_status"):
            try:
                transport_status = qq_bot_client.get_status()
            except Exception as exc:
                logger.warning(
                    "dashboard.token_stats.degraded",
                    channel="qq_bot",
                    operation="qq_bot_transport_status",
                    **_error_fields(exc),
                )
                transport_status = {}
            qq_bot_status = {
                **qq_bot_status,
                "ws_connected": bool(transport_status.get("ws_connected", qq_bot_status.get("ws_connected"))),
                "connected_at": transport_status.get("connected_at", qq_bot_status.get("connected_at")),
            }
        if qq_bot_config is not None:
            ws_connected = bool(qq_bot_status.get("ws_connected"))
            qq_bot_status = {
                **qq_bot_status,
                "status": (
                    "connected"
                    if ws_connected
                    else "enabled"
                    if str(qq_bot_config.app_id or "").strip()
                    else str(qq_bot_status.get("status") or "disabled")
                ),
                "qq_bot_enabled": bool(qq_bot_config.enabled),
                "qq_bot_app_id": _mask_app_id(qq_bot_config.app_id),
            }
    if qq_enabled:
        qq_client = getattr(request.app.state, "qq_ws_client", None)
        qq_channel_service = getattr(request.app.state, "qq_channel_service", None)
        if qq_bot_enabled and (qq_channel_service is None or qq_channel_service is qq_bot_service):
            qq_napcat_status = _disabled_qq_napcat_status()
        elif qq_client is not None and hasattr(qq_client, "get_status"):
            qq_napcat_status = qq_client.get_status()
        else:
            qq_napcat_status = {
                "status": "disconnected",
                "bot_qq": str(getattr(qq_channel_service, "bot_qq", "") or ""),
                "napcat_ws_url": str(getattr(request.app.state, "qq_ws_url", "") or ""),
                "connected_at": None,
                "last_message_at": None,
                "messages_received": 0,
                "messages_sent": 0,
                "online": None,
                "good": None,
            }
        if qq_channel_service is not None and hasattr(qq_channel_service, "get_runtime_status"):
            runtime_status = qq_channel_service.get_runtime_status()
            qq_napcat_status = {
                **dict(qq_napcat_status or _disabled_qq_napcat_status()),
                **runtime_status,
            }
            if runtime_status.get("online") is False:
                qq_napcat_status["status"] = "disconnected"

    email_status = _disabled_email_status()
    email_skill = None
    if skill_manager is not None and hasattr(skill_manager, "_skills"):
        email_skill = skill_manager._skills.get("email_scanner")
    if email_enabled and email_skill is not None and hasattr(email_skill, "get_status"):
        email_status = email_skill.get_status(scheduler=scheduler)

    heartbeat_status = _disabled_heartbeat_status()
    heartbeat_service = getattr(request.app.state, "heartbeat_service", None)
    if heartbeat_service is not None and hasattr(heartbeat_service, "get_status"):
        heartbeat_status = heartbeat_service.get_status(scheduler=scheduler)

    weixin_status = _disabled_weixin_status()
    config_dir = Path(getattr(request.app.state, "config_dir", Path("config")))
    secrets_path = config_dir / "secrets.yaml"
    weixin_enabled = False
    if secrets_path.exists():
        try:
            services = load_secrets_config(secrets_path).services
            weixin_enabled = bool(services and services.weixin and services.weixin.enabled)
        except Exception as exc:
            logger.warning(
                "dashboard.system_info.degraded",
                channel="weixin",
                operation="load_weixin_config",
                **_error_fields(exc),
            )
            weixin_enabled = False
    if weixin_enabled:
        channel = getattr(request.app.state, "weixin_channel", None)
        if channel is not None and hasattr(channel, "get_status"):
            weixin_status = channel.get_status()
        else:
            weixin_status = {
                "status": "disconnected",
                "bot_id": "",
                "user_id": "",
                "last_message_at": None,
                "messages_received": 0,
                "messages_sent": 0,
            }

    feishu_status = _disabled_feishu_status()
    feishu_enabled = False
    config_path = config_dir / "config.yaml"
    if config_path.exists():
        try:
            channel_settings = load_channel_settings(config_path)
            feishu_enabled = bool(channel_settings.feishu.enabled)
        except Exception as exc:
            logger.warning(
                "dashboard.system_info.degraded",
                channel="feishu",
                operation="load_feishu_config",
                **_error_fields(exc),
            )
            feishu_enabled = False
    if feishu_enabled:
        channel = getattr(request.app.state, "feishu_channel", None)
        if channel is not None and hasattr(channel, "get_status"):
            feishu_status = channel.get_status()
        else:
            feishu_status = {
                "status": "disconnected",
                "app_id": "",
                "chat_count": 0,
                "last_message_at": None,
                "messages_received": 0,
                "messages_sent": 0,
            }

    channels: dict[str, Any] = {
        "webui": connection_manager.get_status(),
        "qq_bot": qq_bot_status if qq_bot_enabled else _disabled_qq_bot_status(),
        "weixin": weixin_status,
        "feishu": feishu_status,
        "email": email_status,
        "heartbeat": heartbeat_status,
    }
    if qq_napcat_status is not None:
        channels["qq_napcat"] = qq_napcat_status

    relay = getattr(request.app.state, "channel_relay", None)
    last_delivery_for = getattr(relay, "last_delivery_for", None)
    if callable(last_delivery_for):
        for channel_name in ("webui", "qq_bot", "qq_napcat", "weixin", "feishu"):
            channel_payload = channels.get(channel_name)
            if isinstance(channel_payload, dict):
                channel_payload["last_delivery"] = last_delivery_for(channel_name)

    return {"channels": channels}


@router.get("/dashboard/token-stats")
async def dashboard_token_stats(
    request: Request,
    days: int = Query(default=7, ge=1, le=365),
) -> dict[str, Any]:
    require_api_token(request)

    structured_store = request.app.state.structured_store
    since = datetime.now(UTC) - timedelta(days=days)
    rows = await structured_store.list_token_usage(since_iso=since.isoformat())

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        created_at = _parse_timestamp(row.get("created_at"))
        if created_at is None:
            continue
        day = _day_key(created_at)
        model = str(row.get("resolved_model") or "unknown")
        key = (day, model)
        bucket = grouped.setdefault(
            key,
            {
                "date": day,
                "model": model,
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
        )
        bucket["calls"] += 1
        bucket["input_tokens"] += int(row.get("input_tokens") or 0)
        bucket["output_tokens"] += int(row.get("output_tokens") or 0)
        bucket["total_tokens"] += int(row.get("total_tokens") or 0)

    data = sorted(grouped.values(), key=lambda item: (item["date"], item["model"]))
    return {
        "days": days,
        "data": data,
    }


@router.get("/dashboard/latency-stats")
async def dashboard_latency_stats(
    request: Request,
    days: int = Query(default=7, ge=1, le=365),
    group_by: Literal["day", "model"] = Query(default="day"),
) -> dict[str, Any]:
    require_api_token(request)

    structured_store = request.app.state.structured_store
    since = datetime.now(UTC) - timedelta(days=days)
    token_rows = await structured_store.list_token_usage(since_iso=since.isoformat())

    source = "token_usage.latency_ms"
    if group_by == "model":
        latency_by_model: dict[str, list[float]] = defaultdict(list)
        for row in token_rows:
            latency = row.get("latency_ms")
            model = str(row.get("resolved_model") or "unknown").strip() or "unknown"
            if latency is None:
                continue
            latency_by_model[model].append(float(latency))

        data: list[dict[str, Any]] = []
        for model in sorted(latency_by_model.keys(), key=str.lower):
            values = sorted(latency_by_model[model])
            data.append(
                {
                    "model": model,
                    "count": len(values),
                    "p50_ms": _quantile(values, 0.50),
                    "p95_ms": _quantile(values, 0.95),
                    "p99_ms": _quantile(values, 0.99),
                }
            )

        return {
            "days": days,
            "group_by": group_by,
            "source": source,
            "data": data,
        }

    latency_by_day: dict[str, list[float]] = defaultdict(list)
    for row in token_rows:
        latency = row.get("latency_ms")
        created_at = _parse_timestamp(row.get("created_at"))
        if latency is None or created_at is None:
            continue
        latency_by_day[_day_key(created_at)].append(float(latency))

    if not latency_by_day:
        invocations = await structured_store.list_tool_invocations(since_iso=since.isoformat())
        for row in invocations:
            duration = row.get("duration_ms")
            created_at = _parse_timestamp(row.get("created_at"))
            if duration is None or created_at is None:
                continue
            latency_by_day[_day_key(created_at)].append(float(duration))
        source = "tool_invocations.duration_ms"

    data: list[dict[str, Any]] = []
    for day in sorted(latency_by_day.keys()):
        values = sorted(latency_by_day[day])
        data.append(
            {
                "date": day,
                "count": len(values),
                "p50_ms": _quantile(values, 0.50),
                "p95_ms": _quantile(values, 0.95),
                "p99_ms": _quantile(values, 0.99),
            }
        )

    return {
        "days": days,
        "group_by": group_by,
        "source": source,
        "data": data,
    }


@router.get("/dashboard/recent-latency")
async def dashboard_recent_latency(
    request: Request,
    limit: int = Query(default=24, ge=1, le=200),
) -> dict[str, Any]:
    require_api_token(request)

    structured_store = request.app.state.structured_store
    token_rows = await structured_store.list_token_usage()

    data: list[dict[str, Any]] = []
    for row in token_rows:
        latency = row.get("latency_ms")
        created_at = _parse_timestamp(row.get("created_at"))
        if latency is None or created_at is None:
            continue
        data.append(
            {
                "session_id": row.get("session_id"),
                "model": str(row.get("resolved_model") or "unknown").strip() or "unknown",
                "latency_ms": float(latency),
                "timestamp": to_local(created_at).replace(microsecond=0).isoformat(),
            }
        )
        if len(data) >= limit:
            break

    data.reverse()
    return {
        "limit": limit,
        "data": data,
    }


@router.get("/dashboard/recent-tasks")
async def dashboard_recent_tasks(
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    require_api_token(request)

    structured_store = request.app.state.structured_store
    rows = await structured_store.list_tool_invocations(limit=limit)
    return {
        "limit": limit,
        "data": rows,
    }


@router.get("/dashboard/errors/recent")
async def dashboard_recent_errors(
    request: Request,
    level: Literal["all", "error", "warning"] = Query(default="all"),
    limit: int = Query(default=8, ge=1, le=100),
) -> dict[str, Any]:
    require_api_token(request)
    return {
        "level": level,
        "limit": limit,
        "data": get_recent_logs(level=level, limit=limit),
    }


@router.get("/dashboard/skills")
async def dashboard_skills(request: Request) -> dict[str, Any]:
    require_api_token(request)

    deps = request.app.state.deps
    skill_manager = getattr(deps, "skill_manager", None)
    circuit_breaker = getattr(deps, "circuit_breaker", None)
    global_kill = bool(circuit_breaker.get_global_kill_switch()) if circuit_breaker else False

    config_dir = Path(getattr(request.app.state, "config_dir", Path("config")))
    known_skills = SkillManager.known_skill_names(config_dir / "skills.yaml")
    configured_enabled = _configured_skill_enabled_map(config_dir / "skills.yaml")

    rows: list[dict[str, Any]] = []
    if skill_manager is None:
        for name in sorted(known_skills):
            enabled = configured_enabled.get(name, False)
            external = _external_skill_runtime(request, name)
            rows.append(
                {
                    "name": name,
                    "description": external.get("description", "") if external else "",
                    "enabled": enabled,
                    "status": "healthy" if enabled and external and external.get("active") else "disabled",
                    "tools": external.get("tools", []) if external else [],
                }
            )
        return {"global_kill_switch": global_kill, "data": rows}

    runtime_items: dict[str, dict[str, Any]] = {}
    for item in skill_manager.list_skills():
        name = item.get("name")
        if isinstance(name, str) and name:
            runtime_items[name] = item

    for name in sorted(set(runtime_items.keys()) | known_skills):
        item = runtime_items.get(name)
        external = _external_skill_runtime(request, name)
        enabled = configured_enabled.get(name)
        if item is None:
            rows.append(
                {
                    "name": name,
                    "description": external.get("description", "") if external else "",
                    "enabled": bool(enabled),
                    "status": "healthy" if enabled and external and external.get("active") else "disabled",
                    "tools": external.get("tools", []) if external else [],
                }
            )
            continue

        tools = [tool for tool in item.get("tools", []) if isinstance(tool, str) and tool]
        status = "healthy"
        if enabled is None:
            enabled = bool(item.get("enabled", True))
        if not enabled:
            status = "disabled"
        elif global_kill:
            status = "open"
        elif circuit_breaker is not None:
            for tool_name in tools:
                allowed, _ = circuit_breaker.can_execute(tool_name, None)
                if not allowed:
                    status = "open"
                    break

        rows.append(
            {
                "name": name,
                "description": item.get("description") or (external.get("description") if external else ""),
                "enabled": enabled,
                "status": status,
                "tools": tools,
            }
        )

    return {
        "global_kill_switch": global_kill,
        "data": rows,
    }
