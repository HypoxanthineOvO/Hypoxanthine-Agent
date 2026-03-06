from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
import math
from pathlib import Path
import shutil
from typing import Any

from fastapi import APIRouter, Query, Request

from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.gateway.auth import require_api_token

router = APIRouter(prefix="/api")


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
    return dt.astimezone(UTC).date().isoformat()


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


@router.get("/dashboard/status")
async def dashboard_status(request: Request) -> dict[str, Any]:
    require_api_token(request)

    started_at = getattr(request.app.state, "started_at", datetime.now(UTC))
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    now = datetime.now(UTC)
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
) -> dict[str, Any]:
    require_api_token(request)

    structured_store = request.app.state.structured_store
    since = datetime.now(UTC) - timedelta(days=days)

    latency_by_day: dict[str, list[float]] = defaultdict(list)
    token_rows = await structured_store.list_token_usage(since_iso=since.isoformat())
    for row in token_rows:
        latency = row.get("latency_ms")
        created_at = _parse_timestamp(row.get("created_at"))
        if latency is None or created_at is None:
            continue
        latency_by_day[_day_key(created_at)].append(float(latency))

    source = "token_usage.latency_ms"
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
        "source": source,
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


@router.get("/dashboard/skills")
async def dashboard_skills(request: Request) -> dict[str, Any]:
    require_api_token(request)

    deps = request.app.state.deps
    skill_manager = getattr(deps, "skill_manager", None)
    circuit_breaker = getattr(deps, "circuit_breaker", None)
    global_kill = bool(circuit_breaker.get_global_kill_switch()) if circuit_breaker else False

    config_dir = Path(getattr(request.app.state, "config_dir", Path("config")))
    known_skills = SkillManager.known_skill_names(config_dir / "skills.yaml")

    rows: list[dict[str, Any]] = []
    if skill_manager is None:
        for name in sorted(known_skills):
            rows.append(
                {
                    "name": name,
                    "description": "",
                    "enabled": False,
                    "status": "disabled",
                    "tools": [],
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
        if item is None:
            rows.append(
                {
                    "name": name,
                    "description": "",
                    "enabled": False,
                    "status": "disabled",
                    "tools": [],
                }
            )
            continue

        tools = [tool for tool in item.get("tools", []) if isinstance(tool, str) and tool]
        status = "healthy"
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
                "description": item.get("description"),
                "enabled": enabled,
                "status": status,
                "tools": tools,
            }
        )

    return {
        "global_kill_switch": global_kill,
        "data": rows,
    }
