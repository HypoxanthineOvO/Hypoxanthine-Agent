from __future__ import annotations

from datetime import UTC, datetime
import inspect
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable

import structlog

from hypo_agent.core.config_loader import expand_runtime_placeholders, get_memory_dir

logger = structlog.get_logger("hypo_agent.heartbeat")


EventSourceCallback = Callable[[], Any]


class HeartbeatService:
    def __init__(
        self,
        *,
        structured_store: Any,
        model_router: Any,
        message_queue: Any,
        scheduler: Any | None = None,
        default_session_id: str = "main",
        db_path: Path | str | None = None,
        decision_prompt_template: str = "",
    ) -> None:
        self.structured_store = structured_store
        self.model_router = model_router
        self.message_queue = message_queue
        self.scheduler = scheduler
        self.default_session_id = default_session_id
        self.decision_prompt_template = str(decision_prompt_template or "")

        if db_path is None:
            db_path = get_memory_dir() / "hypo.db"
        self.db_path = Path(db_path)
        self._event_sources: dict[str, EventSourceCallback] = {}
        self.last_heartbeat_at: str | None = None

    def register_event_source(self, name: str, callback: EventSourceCallback) -> None:
        key = str(name).strip()
        if not key:
            raise ValueError("event source name is required")
        self._event_sources[key] = callback

    async def run(self) -> dict[str, Any]:
        builtins = await self._collect_builtin_checks()
        overdue = await self._collect_overdue_reminders()
        sources = await self._collect_event_source_results()

        decision_payload = await self._decide_should_push(
            builtins=builtins,
            overdue=overdue,
            sources=sources,
        )
        should_push = bool(decision_payload.get("should_push"))
        summary = str(decision_payload.get("summary") or "").strip()
        if not summary:
            summary = self._fallback_summary(overdue=overdue, sources=sources, builtins=builtins)

        result = {
            "should_push": should_push,
            "summary": summary,
            "overdue_count": len(overdue),
            "event_sources": sources,
            "checks": builtins,
        }
        self.last_heartbeat_at = datetime.now(UTC).isoformat()

        if should_push:
            event = {
                "event_type": "heartbeat_trigger",
                "session_id": self.default_session_id,
                "message_tag": "heartbeat",
                "summary": summary,
                "title": "heartbeat",
                "description": summary,
                "overdue_count": len(overdue),
                "event_sources": sources,
            }
            await self.message_queue.put(event)
            logger.info("heartbeat.push", summary=summary, overdue_count=len(overdue))
        else:
            logger.info("heartbeat.silent", summary=summary, overdue_count=len(overdue))
        return result

    async def _collect_builtin_checks(self) -> dict[str, Any]:
        checks: dict[str, Any] = {}

        checks["db_ok"] = self._check_db_access()
        checks["scheduler_running"] = bool(getattr(self.scheduler, "is_running", False))
        checks["router_available"] = bool(
            self.model_router is not None and hasattr(self.model_router, "call_lightweight_json")
        )
        return checks

    def _check_db_access(self) -> bool:
        try:
            if not self.db_path.exists():
                # Treat missing DB as non-fatal; StructuredStore may create on demand.
                return True
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    async def _collect_overdue_reminders(self) -> list[dict[str, Any]]:
        method = getattr(self.structured_store, "list_overdue_pending_reminders", None)
        if callable(method):
            try:
                rows = method(limit=20)
                if inspect.isawaitable(rows):
                    rows = await rows
                if isinstance(rows, list):
                    return [item for item in rows if isinstance(item, dict)]
            except Exception:
                logger.exception("heartbeat.overdue_query.failed")
        return []

    async def _collect_event_source_results(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for name, callback in self._event_sources.items():
            try:
                result = callback()
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                logger.exception("heartbeat.source.failed", source=name)
                items.append({"name": name, "status": "error", "error": str(exc)})
                continue

            normalized: dict[str, Any]
            if isinstance(result, dict):
                normalized = dict(result)
                normalized.setdefault("name", name)
            else:
                normalized = {"name": name, "result": result}
            items.append(normalized)
        return items

    async def _decide_should_push(
        self,
        *,
        builtins: dict[str, Any],
        overdue: list[dict[str, Any]],
        sources: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # Fallback local heuristic when router is unavailable.
        fallback_should_push = (not builtins.get("db_ok", False)) or bool(overdue) or any(
            bool(item.get("should_push")) or int(item.get("new_items") or 0) > 0
            for item in sources
            if isinstance(item, dict)
        )

        if not (self.model_router is not None and hasattr(self.model_router, "call_lightweight_json")):
            return {"should_push": fallback_should_push, "summary": ""}

        prompt = self._build_decision_prompt(
            builtins=builtins,
            overdue=overdue,
            sources=sources,
        )
        try:
            payload = await self.model_router.call_lightweight_json(
                prompt,
                session_id=self.default_session_id,
            )
        except TypeError:
            payload = await self.model_router.call_lightweight_json(prompt)
        except Exception:
            logger.exception("heartbeat.decision.failed")
            return {"should_push": fallback_should_push, "summary": ""}

        if not isinstance(payload, dict):
            return {"should_push": fallback_should_push, "summary": ""}

        should_push = payload.get("should_push")
        if should_push is None and str(payload.get("decision") or "").strip().lower() in {
            "abnormal",
            "alert",
            "push",
            "true",
        }:
            should_push = True
        if should_push is None:
            should_push = fallback_should_push
        # Safety-first: if deterministic signals already indicate abnormality,
        # do not allow model judgment to suppress the proactive push.
        if fallback_should_push and not bool(should_push):
            should_push = True

        return {
            "should_push": bool(should_push),
            "summary": str(payload.get("summary") or "").strip(),
        }

    def _build_decision_prompt(
        self,
        *,
        builtins: dict[str, Any],
        overdue: list[dict[str, Any]],
        sources: list[dict[str, Any]],
    ) -> str:
        checks_json = json.dumps(builtins, ensure_ascii=False)
        overdue_json = json.dumps(overdue, ensure_ascii=False)
        sources_json = json.dumps(sources, ensure_ascii=False)
        template = str(self.decision_prompt_template or "").strip()
        if not template:
            return (
                "你是 heartbeat 判定器。仅返回 JSON 对象，包含 should_push(bool) 与 summary(str)。"
                "依据以下输入判断是否需要主动推送："
                f"\nchecks={checks_json}"
                f"\noverdue={overdue_json}"
                f"\nsources={sources_json}"
            )

        rendered = expand_runtime_placeholders(template).strip()
        placeholder_pairs = {
            "${checks}": checks_json,
            "${overdue}": overdue_json,
            "${sources}": sources_json,
        }
        has_context_placeholders = any(token in rendered for token in placeholder_pairs)
        for token, value in placeholder_pairs.items():
            rendered = rendered.replace(token, value)

        if has_context_placeholders:
            return rendered

        return (
            f"{rendered}\n\n"
            f"checks={checks_json}\n"
            f"overdue={overdue_json}\n"
            f"sources={sources_json}"
        )

    def _fallback_summary(
        self,
        *,
        overdue: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        builtins: dict[str, Any],
    ) -> str:
        if not builtins.get("db_ok", False):
            return "heartbeat 检测到数据库不可用"
        if overdue:
            return f"heartbeat 检测到 {len(overdue)} 条过期未触发提醒"
        total_new = sum(
            int(item.get("new_items") or 0)
            for item in sources
            if isinstance(item, dict)
        )
        if total_new > 0:
            return f"heartbeat 检测到 {total_new} 条新事件"
        return "heartbeat 巡检正常，无需推送"

    def get_status(self, *, scheduler: Any | None = None) -> dict[str, Any]:
        runtime_scheduler = scheduler or self.scheduler
        running = bool(getattr(runtime_scheduler, "is_running", False))
        has_job = False
        active_tasks = 0
        if runtime_scheduler is not None and hasattr(runtime_scheduler, "has_job_id"):
            has_job = bool(runtime_scheduler.has_job_id("heartbeat"))
        if runtime_scheduler is not None and hasattr(runtime_scheduler, "get_active_job_count"):
            active_tasks = int(runtime_scheduler.get_active_job_count())
        return {
            "status": "running" if running and (has_job or active_tasks > 0) else "disabled",
            "last_heartbeat_at": self.last_heartbeat_at,
            "active_tasks": active_tasks,
        }
