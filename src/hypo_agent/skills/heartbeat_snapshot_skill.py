from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import platform
import re
from typing import Any, Awaitable, Callable

import structlog

from hypo_agent.core.config_loader import get_memory_dir
from hypo_agent.core.notion_todo_binding import (
    discover_notion_todo_candidate,
    get_bound_notion_todo_database_id,
)
from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill

_LOAD_RE = re.compile(r"load average[s]?:\s*([0-9.]+),\s*([0-9.]+),\s*([0-9.]+)")
logger = structlog.get_logger("hypo_agent.skills.heartbeat_snapshot")


class HeartbeatSnapshotSkill(BaseSkill):
    name = "heartbeat_snapshot"
    description = "Provide structured heartbeat snapshots for mail, notion todo, and reminders."
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        email_skill: Any | None = None,
        reminder_skill: Any | None = None,
        notion_skill: Any | None = None,
        structured_store: Any | None = None,
        people_index_path: Path | str | None = None,
        system_snapshot_provider: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        now_provider: Callable[[], datetime] | None = None,
        now_iso_provider: Callable[[], str] | None = None,
    ) -> None:
        self.email_skill = email_skill
        self.reminder_skill = reminder_skill
        self.notion_skill = notion_skill
        self.structured_store = structured_store
        self.people_index_path = Path(people_index_path or (get_memory_dir() / "people" / "index.md"))
        self.system_snapshot_provider = system_snapshot_provider
        self.now_provider = now_provider
        self.now_iso_provider = now_iso_provider

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            self._tool_schema(
                "get_mail_snapshot",
                "获取结构化邮件快照。内部以 heartbeat 模式扫描未读邮件，返回 JSON。",
            ),
            self._tool_schema(
                "get_notion_todo_snapshot",
                "获取结构化 Notion 待办快照，聚合今天到期、三天内高优和今日已完成任务。返回 JSON。",
            ),
            self._tool_schema(
                "get_reminder_snapshot",
                "获取结构化提醒快照，聚合过期提醒和半天内即将触发的提醒。返回 JSON。",
            ),
            self._tool_schema(
                "get_heartbeat_snapshot",
                "一次性获取完整 heartbeat 结构化快照，聚合 mail/notion/reminders 三个 section。返回 JSON。",
            ),
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        del params
        if tool_name == "get_mail_snapshot":
            return SkillOutput(status="success", result=await self._get_mail_snapshot())
        if tool_name == "get_notion_todo_snapshot":
            return SkillOutput(status="success", result=await self._get_notion_todo_snapshot())
        if tool_name == "get_reminder_snapshot":
            return SkillOutput(status="success", result=await self._get_reminder_snapshot())
        if tool_name == "get_heartbeat_snapshot":
            return SkillOutput(status="success", result=await self._get_heartbeat_snapshot())
        return SkillOutput(status="error", error_info=f"Unsupported tool '{tool_name}'")

    def _tool_schema(self, name: str, description: str) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        }

    def _now(self) -> datetime:
        if self.now_provider is not None:
            return self.now_provider()
        if self.now_iso_provider is not None:
            return datetime.fromisoformat(self.now_iso_provider())
        return datetime.now().astimezone()

    def _now_iso(self) -> str:
        if self.now_iso_provider is not None:
            return self.now_iso_provider()
        return self._now().isoformat()

    async def _get_heartbeat_snapshot(self) -> dict[str, Any]:
        sections = await asyncio.gather(
            self._safe_section("mail", self._get_mail_snapshot()),
            self._safe_section("notion_todo", self._get_notion_todo_snapshot()),
            self._safe_section("reminders", self._get_reminder_snapshot()),
        )
        payload: dict[str, Any] = {"checked_at": self._now_iso()}
        for name, section in sections:
            payload[name] = section
        return payload

    async def _safe_section(self, name: str, awaitable: Awaitable[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        try:
            return name, await awaitable
        except Exception as exc:
            return name, {"available": False, "error": str(exc)}

    async def _get_mail_snapshot(self) -> dict[str, Any]:
        if self.email_skill is None or not callable(getattr(self.email_skill, "scan_emails", None)):
            return {"available": False, "error": "email_scanner skill is unavailable"}
        result = await self.email_skill.scan_emails(
            params={"triggered_by": "heartbeat", "unread_only": True}
        )
        items = result.get("items", [])
        normalized_items = [
            self._normalize_email_item(item)
            for item in items
            if isinstance(item, dict)
        ]
        counts = {"important": 0, "low_priority": 0, "archive": 0, "system": 0, "failed": 0}
        important: list[dict[str, Any]] = []
        other: list[dict[str, Any]] = []
        for item in normalized_items:
            category = str(item.get("category") or "")
            if category in counts:
                counts[category] += 1
            if category == "important":
                important.append(item)
            else:
                other.append(item)
        return {
            "available": True,
            "checked_at": self._now_iso(),
            "summary": str(result.get("summary") or ""),
            "new_emails": int(result.get("new_emails") or 0),
            "counts": counts,
            "important": important,
            "other": other,
            "human_summary": self._render_mail_human_summary(
                counts=counts,
                important=important,
                other=other,
            ),
        }

    async def _get_reminder_snapshot(self) -> dict[str, Any]:
        if self.reminder_skill is None or not callable(getattr(self.reminder_skill, "execute", None)):
            return {"available": False, "error": "reminder skill is unavailable"}
        output = await self.reminder_skill.execute("list_reminders", {"status": "all"})
        if output.status != "success" or not isinstance(output.result, dict):
            return {"available": False, "error": output.error_info or "failed to list reminders"}
        rows = output.result.get("items", [])
        now = self._now()
        soon_threshold = now + timedelta(hours=12)
        overdue: list[dict[str, Any]] = []
        due_soon: list[dict[str, Any]] = []
        active: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = self._normalize_reminder(row)
            active.append(item)
            next_run_at = self._parse_datetime(item.get("next_run_at"))
            if str(item.get("status") or "") != "active" or next_run_at is None:
                continue
            if next_run_at < now:
                overdue.append(item)
            elif next_run_at <= soon_threshold:
                due_soon.append(item)
        return {
            "available": True,
            "checked_at": self._now_iso(),
            "counts": {
                "total": len(active),
                "overdue": len(overdue),
                "due_soon": len(due_soon),
            },
            "overdue": overdue,
            "due_soon": due_soon,
            "items": active,
            "human_summary": self._render_reminder_human_summary(
                overdue=overdue,
                due_soon=due_soon,
                total=len(active),
            ),
        }

    async def _get_notion_todo_snapshot(self) -> dict[str, Any]:
        notion_skill = self.notion_skill
        if notion_skill is None:
            return {"available": False, "error": "notion todo database is unavailable"}
        plan_snapshot_getter = getattr(notion_skill, "get_plan_snapshot", None)
        if callable(plan_snapshot_getter):
            try:
                plan_payload = plan_snapshot_getter()
                if asyncio.iscoroutine(plan_payload):
                    plan_payload = await plan_payload
                if isinstance(plan_payload, dict) and bool(plan_payload.get("available", False)):
                    return {
                        **plan_payload,
                        "source": "HYX的计划通",
                        "pending_today": plan_payload.get("undone_items", []),
                        "completed_today": plan_payload.get("done_items", []),
                        "high_priority_due_soon": plan_payload.get("important_items", []),
                    }
            except Exception as exc:
                logger.warning("heartbeat.plan_snapshot_failed_fallback", error=str(exc))
        todo_snapshot_getter = getattr(notion_skill, "get_todo_snapshot", None)
        if callable(todo_snapshot_getter):
            snapshot_payload = todo_snapshot_getter(
                structured_store=self.structured_store,
                limit=50,
            )
            if asyncio.iscoroutine(snapshot_payload):
                snapshot_payload = await snapshot_payload
            if isinstance(snapshot_payload, dict):
                if not bool(snapshot_payload.get("available", False)):
                    return snapshot_payload
                database_id = str(snapshot_payload.get("database_id") or "").strip()
                rows = snapshot_payload.get("items", [])
            else:
                database_id = ""
                rows = []
        else:
            client = getattr(notion_skill, "_client", None)
            configured_database_id = str(getattr(notion_skill, "_todo_database_id", "") or "").strip()
            database_id = await get_bound_notion_todo_database_id(
                self.structured_store,
                configured_database_id=configured_database_id,
            )
            if client is None:
                return {"available": False, "error": "notion todo database is unavailable"}
            if not database_id:
                discovery = await discover_notion_todo_candidate(self.structured_store, client)
                return {
                    "available": False,
                    "error": str(discovery.get("error") or "notion todo database is unavailable"),
                    "human_summary": str(discovery.get("human_summary") or "").strip(),
                    "binding_status": str(discovery.get("status") or "").strip(),
                    "candidate": discovery.get("candidate"),
                    "candidates": discovery.get("candidates"),
                }
            raw_rows = await client.query_database(database_id, filter=None, sorts=None, page_size=50)
            normalizer = getattr(notion_skill, "normalize_todo_rows", None)
            if callable(normalizer):
                rows = normalizer(raw_rows)
                if asyncio.iscoroutine(rows):
                    rows = await rows
            else:
                rows = raw_rows
        now = self._now().date()
        due_soon_limit = now + timedelta(days=3)
        pending_today: list[dict[str, Any]] = []
        high_priority_due_soon: list[dict[str, Any]] = []
        completed_today: list[dict[str, Any]] = []
        other_pending: list[dict[str, Any]] = []
        notion_today_matcher = getattr(notion_skill, "todo_item_matches_today", None)
        notion_due_date_getter = getattr(notion_skill, "todo_item_due_date", None)
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = self._normalize_notion_row(row)
            if callable(notion_due_date_getter):
                due_date = notion_due_date_getter(item)
            else:
                due_date = self._notion_item_due_date(item)
            done = bool(item.get("done"))
            is_high = self._is_high_priority(item)
            if callable(notion_today_matcher):
                matches_today = bool(notion_today_matcher(item, today=now))
            else:
                matches_today = self._notion_item_matches_today(item, today=now)
            if matches_today and not done:
                pending_today.append(item)
            if (
                due_date is not None
                and
                now <= due_date <= due_soon_limit
                and not done
                and is_high
                and not self._is_daily_recurring_task(item)
            ):
                high_priority_due_soon.append(item)
            if matches_today and done:
                completed_today.append(item)
            if due_date is not None and due_date >= now and not done:
                other_pending.append(item)
        return {
            "available": True,
            "checked_at": self._now_iso(),
            "database_id": database_id,
            "counts": {
                "pending_today": len(pending_today),
                "high_priority_due_soon": len(high_priority_due_soon),
                "completed_today": len(completed_today),
                "other_pending": len(other_pending),
            },
            "pending_today": pending_today,
            "high_priority_due_soon": high_priority_due_soon,
            "completed_today": completed_today,
            "other_pending": other_pending,
            "human_summary": self._render_notion_human_summary(
                pending_today=pending_today,
                high_priority_due_soon=high_priority_due_soon,
                completed_today=completed_today,
            ),
        }

    async def _get_system_snapshot(self) -> dict[str, Any]:
        if self.system_snapshot_provider is not None:
            payload = await self.system_snapshot_provider()
            payload.setdefault("available", True)
            payload.setdefault("checked_at", self._now_iso())
            payload.setdefault(
                "project_activity_summary",
                [
                    str(item.get("activity_summary") or "").strip()
                    for item in (payload.get("projects_by_user") or [])
                    if isinstance(item, dict) and str(item.get("activity_summary") or "").strip()
                ],
            )
            payload.setdefault("top_system_processes", [])
            payload.setdefault("human_summary", self._render_system_human_summary(payload))
            return payload
        people = self._load_people_index()
        results = await asyncio.gather(
            self._run_command("uptime"),
            self._run_command("free", "-h"),
            self._run_command("df", "-h", "/"),
            self._run_command(
                "ps",
                "-eo",
                "user,pid,etime,pcpu,pmem,comm,args",
                "--sort=-pcpu",
            ),
            self._run_command(
                "nvidia-smi",
                "--query-gpu=index,name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ),
            self._run_command(
                "nvidia-smi",
                "--query-compute-apps=gpu_uuid,gpu_bus_id,pid,process_name,used_memory",
                "--format=csv,noheader,nounits",
            ),
            self._run_command(
                "nvidia-smi",
                "--query-gpu=gpu_uuid,index",
                "--format=csv,noheader",
            ),
        )
        (
            uptime_result,
            free_result,
            disk_result,
            ps_result,
            gpu_result,
            gpu_proc_result,
            gpu_uuid_result,
        ) = results
        processes = self._parse_ps_rows(ps_result.get("stdout", ""))
        gpu_uuid_to_index = self._parse_gpu_uuid_map(gpu_uuid_result.get("stdout", ""))
        gpu_processes = self._parse_gpu_processes(gpu_proc_result.get("stdout", ""), gpu_uuid_to_index)
        projects_by_user = self._group_processes_by_user(processes, people, gpu_processes)
        errors = [
            {
                "command": item.get("command"),
                "error": item.get("stderr") or item.get("error") or "command failed",
            }
            for item in results
            if not item.get("ok", False)
        ]
        payload = {
            "available": True,
            "checked_at": self._now_iso(),
            "host": platform.node() or "",
            "load": self._parse_uptime(uptime_result.get("stdout", "")),
            "memory": self._parse_free_output(free_result.get("stdout", "")),
            "disk": self._parse_df_output(disk_result.get("stdout", "")),
            "gpu": {
                "cards": self._parse_gpu_cards(gpu_result.get("stdout", "")),
                "processes": gpu_processes,
                "available": gpu_result.get("ok", False),
                "error": "" if gpu_result.get("ok", False) else str(gpu_result.get("stderr") or ""),
            },
            "projects_by_user": projects_by_user,
            "project_activity_summary": [
                str(item.get("activity_summary") or "").strip()
                for item in projects_by_user
                if str(item.get("activity_summary") or "").strip()
            ],
            "top_system_processes": self._top_system_processes(processes, people, pid_to_gpu=gpu_processes),
            "errors": errors,
        }
        payload["human_summary"] = self._render_system_human_summary(payload)
        return payload

    async def _run_command(self, *args: str, timeout_seconds: float = 5.0) -> dict[str, Any]:
        command = " ".join(args)
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return {"ok": False, "command": command, "stdout": "", "stderr": str(exc), "exit_code": None}
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            with suppress(Exception):
                await process.communicate()
            return {
                "ok": False,
                "command": command,
                "stdout": "",
                "stderr": f"timed out after {timeout_seconds:.1f}s",
                "exit_code": None,
            }
        finally:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                with suppress(Exception):
                    await process.communicate()
        return {
            "ok": process.returncode == 0,
            "command": command,
            "stdout": stdout.decode("utf-8", errors="replace").strip(),
            "stderr": stderr.decode("utf-8", errors="replace").strip(),
            "exit_code": process.returncode,
        }

    def _load_people_index(self) -> dict[str, str]:
        if not self.people_index_path.exists():
            return {}
        rows = self.people_index_path.read_text(encoding="utf-8").splitlines()
        mapping: dict[str, str] = {}
        for line in rows:
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) < 2 or cells[0] in {"账号", "---"}:
                continue
            account = re.sub(r"^\[(.+?)\]\(.+\)$", r"\1", cells[0]).strip()
            display_name = cells[1].strip()
            if account and display_name and display_name != "---":
                mapping[account] = display_name
        return mapping

    def _parse_uptime(self, text: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"raw": text}
        match = _LOAD_RE.search(text)
        if match is None:
            return payload
        one, five, fifteen = (float(match.group(i)) for i in range(1, 4))
        cpu_count = max(1, int(os.cpu_count() or 1))
        payload.update(
            {
                "load_average": {"1m": one, "5m": five, "15m": fifteen},
                "cpu_count": cpu_count,
                "load_ok": one <= max(1.0, cpu_count * 1.2),
            }
        )
        return payload

    def _parse_free_output(self, text: str) -> dict[str, Any]:
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return {"raw": text}
        headers = lines[0].split()
        mem_line = next((line for line in lines if line.startswith("Mem:")), "")
        if not mem_line:
            return {"raw": text}
        values = mem_line.split()[1:]
        return {"raw": text, **dict(zip(headers, values, strict=False))}

    def _parse_df_output(self, text: str) -> dict[str, Any]:
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return {"raw": text}
        parts = lines[1].split()
        if len(parts) < 6:
            return {"raw": text}
        return {
            "filesystem": parts[0],
            "size": parts[1],
            "used": parts[2],
            "available": parts[3],
            "use_percent": parts[4],
            "mounted_on": parts[5],
        }

    def _parse_ps_rows(self, text: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in text.splitlines()[1:]:
            parts = line.split(None, 6)
            if len(parts) < 7:
                continue
            rows.append(
                {
                    "user": parts[0],
                    "pid": int(parts[1]),
                    "etime": parts[2],
                    "pcpu": self._safe_float(parts[3]),
                    "pmem": self._safe_float(parts[4]),
                    "comm": parts[5],
                    "args": parts[6],
                }
            )
        return rows

    def _parse_gpu_cards(self, text: str) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        for line in text.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 7:
                continue
            cards.append(
                {
                    "index": parts[0],
                    "name": parts[1],
                    "gpu_utilization_percent": parts[2],
                    "memory_utilization_percent": parts[3],
                    "memory_used_mb": parts[4],
                    "memory_total_mb": parts[5],
                    "temperature_c": parts[6],
                }
            )
        return cards

    def _parse_gpu_uuid_map(self, text: str) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for line in text.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) >= 2:
                mapping[parts[0]] = parts[1]
        return mapping

    def _parse_gpu_processes(self, text: str, uuid_to_index: dict[str, str]) -> list[dict[str, Any]]:
        processes: list[dict[str, Any]] = []
        for line in text.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 5:
                continue
            pid = self._safe_int(parts[2])
            processes.append(
                {
                    "gpu_index": uuid_to_index.get(parts[0], ""),
                    "pid": pid,
                    "process_name": parts[3],
                    "used_memory_mb": self._safe_int(parts[4]),
                }
            )
        return processes

    def _group_processes_by_user(
        self,
        processes: list[dict[str, Any]],
        people: dict[str, str],
        gpu_processes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        pid_to_gpu: dict[int, list[dict[str, Any]]] = {}
        for item in gpu_processes:
            pid = int(item.get("pid") or 0)
            if pid <= 0:
                continue
            pid_to_gpu.setdefault(pid, []).append(item)
        grouped: dict[str, dict[str, Any]] = {}
        for proc in processes:
            user = str(proc.get("user") or "").strip()
            if not user:
                continue
            entry = grouped.setdefault(
                user,
                {
                    "account": user,
                    "display_name": people.get(user, user),
                    "cpu_percent_total": 0.0,
                    "memory_percent_total": 0.0,
                    "gpu_memory_mb": 0,
                    "gpu_cards": set(),
                    "process_count": 0,
                    "top_processes": [],
                    "top_process_details": [],
                },
            )
            entry["cpu_percent_total"] += float(proc.get("pcpu") or 0.0)
            entry["memory_percent_total"] += float(proc.get("pmem") or 0.0)
            entry["process_count"] += 1
            gpu_items = pid_to_gpu.get(int(proc.get("pid") or 0), [])
            proc_gpu_memory = sum(int(item.get("used_memory_mb") or 0) for item in gpu_items)
            proc_gpu_cards = sorted(
                {
                    str(item.get("gpu_index") or "").strip()
                    for item in gpu_items
                    if str(item.get("gpu_index") or "").strip()
                }
            )
            if len(entry["top_processes"]) < 3:
                command = str(proc.get("args") or proc.get("comm") or "").strip()
                entry["top_processes"].append(command[:160])
            if len(entry["top_process_details"]) < 3:
                entry["top_process_details"].append(
                    {
                        "pid": int(proc.get("pid") or 0),
                        "cpu_percent": round(float(proc.get("pcpu") or 0.0), 2),
                        "memory_percent": round(float(proc.get("pmem") or 0.0), 2),
                        "gpu_memory_mb": proc_gpu_memory,
                        "gpu_cards": proc_gpu_cards,
                        "command": str(proc.get("args") or proc.get("comm") or "").strip()[:240],
                    }
                )
            for gpu_item in gpu_items:
                gpu_index = str(gpu_item.get("gpu_index") or "").strip()
                if gpu_index:
                    entry["gpu_cards"].add(gpu_index)
                entry["gpu_memory_mb"] += int(gpu_item.get("used_memory_mb") or 0)
        ordered: list[dict[str, Any]] = []
        for item in grouped.values():
            ordered.append(
                {
                    "account": item["account"],
                    "display_name": item["display_name"],
                    "cpu_percent_total": round(float(item["cpu_percent_total"]), 2),
                    "memory_percent_total": round(float(item["memory_percent_total"]), 2),
                    "gpu_memory_mb": int(item["gpu_memory_mb"]),
                    "gpu_cards": sorted(item["gpu_cards"]),
                    "process_count": int(item["process_count"]),
                    "top_processes": item["top_processes"],
                    "top_process_details": item["top_process_details"],
                    "activity_summary": self._build_user_activity_summary(item),
                }
            )
        ordered.sort(key=lambda item: (item["cpu_percent_total"], item["memory_percent_total"]), reverse=True)
        return ordered

    def _top_system_processes(
        self,
        processes: list[dict[str, Any]],
        people: dict[str, str],
        *,
        pid_to_gpu: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        gpu_map: dict[int, list[dict[str, Any]]] = {}
        for item in pid_to_gpu:
            pid = int(item.get("pid") or 0)
            if pid <= 0:
                continue
            gpu_map.setdefault(pid, []).append(item)

        top_rows: list[dict[str, Any]] = []
        for proc in processes[:8]:
            user = str(proc.get("user") or "").strip()
            gpu_items = gpu_map.get(int(proc.get("pid") or 0), [])
            top_rows.append(
                {
                    "user": user,
                    "display_name": people.get(user, user),
                    "pid": int(proc.get("pid") or 0),
                    "cpu_percent": round(float(proc.get("pcpu") or 0.0), 2),
                    "memory_percent": round(float(proc.get("pmem") or 0.0), 2),
                    "gpu_memory_mb": sum(int(item.get("used_memory_mb") or 0) for item in gpu_items),
                    "gpu_cards": sorted(
                        {
                            str(item.get("gpu_index") or "").strip()
                            for item in gpu_items
                            if str(item.get("gpu_index") or "").strip()
                        }
                    ),
                    "command": str(proc.get("args") or proc.get("comm") or "").strip()[:240],
                }
            )
        return top_rows

    def _build_user_activity_summary(self, item: dict[str, Any]) -> str:
        display_name = str(item.get("display_name") or item.get("account") or "").strip()
        account = str(item.get("account") or "").strip()
        name = f"{display_name}/{account}" if display_name and display_name != account else (display_name or account)
        process_count = int(item.get("process_count") or 0)
        cpu_percent = round(float(item.get("cpu_percent_total") or 0.0), 2)
        memory_percent = round(float(item.get("memory_percent_total") or 0.0), 2)
        gpu_memory_mb = int(item.get("gpu_memory_mb") or 0)
        gpu_cards = [str(card) for card in (item.get("gpu_cards") or []) if str(card).strip()]
        gpu_text = "无 GPU 占用"
        if gpu_memory_mb > 0:
            card_text = f"（{', '.join(gpu_cards)}）" if gpu_cards else ""
            gpu_text = f"GPU {gpu_memory_mb} MiB{card_text}"
        processes = [str(proc).strip() for proc in (item.get("top_processes") or []) if str(proc).strip()]
        process_text = "；".join(processes[:3]) if processes else "无显著进程"
        return (
            f"{name}：{process_count} 个进程，CPU {cpu_percent}%，内存 {memory_percent}%，"
            f"{gpu_text}；主要进程：{process_text}"
        )

    def _render_system_human_summary(self, payload: dict[str, Any]) -> str:
        lines: list[str] = []
        host = str(payload.get("host") or "").strip()
        if host:
            lines.append(f"主机：{host}")
        load = payload.get("load") if isinstance(payload.get("load"), dict) else {}
        load_avg = load.get("load_average") if isinstance(load, dict) else {}
        if isinstance(load_avg, dict) and load_avg:
            lines.append(
                "负载："
                f"{load_avg.get('1m', '—')} / {load_avg.get('5m', '—')} / {load_avg.get('15m', '—')}"
            )
        memory = payload.get("memory") if isinstance(payload.get("memory"), dict) else {}
        total = str(memory.get("total") or "").strip()
        used = str(memory.get("used") or "").strip()
        available = str(memory.get("available") or "").strip()
        if total or used or available:
            lines.append(f"内存：总 {total or '—'}，已用 {used or '—'}，可用 {available or '—'}")
        disk = payload.get("disk") if isinstance(payload.get("disk"), dict) else {}
        if disk:
            lines.append(
                f"磁盘：{str(disk.get('mounted_on') or '/')} "
                f"{str(disk.get('used') or '—')}/{str(disk.get('size') or '—')} "
                f"（{str(disk.get('use_percent') or '—')}）"
            )
        gpu = payload.get("gpu") if isinstance(payload.get("gpu"), dict) else {}
        cards = gpu.get("cards") if isinstance(gpu, dict) else []
        if isinstance(cards, list) and cards:
            lines.append(f"GPU：{len(cards)} 张卡在线")
        activity_lines = [
            str(item).strip()
            for item in (payload.get("project_activity_summary") or [])
            if str(item).strip()
        ]
        if activity_lines:
            lines.append("按人运行情况：")
            lines.extend(f"- {item}" for item in activity_lines[:5])
        top_processes = payload.get("top_system_processes")
        if isinstance(top_processes, list) and top_processes:
            top = top_processes[0]
            if isinstance(top, dict):
                lines.append(
                    "最高 CPU 进程："
                    f"{top.get('display_name') or top.get('user') or 'unknown'} "
                    f"PID {top.get('pid') or '—'} "
                    f"CPU {top.get('cpu_percent') or 0}% "
                    f"{str(top.get('command') or '')[:160]}"
                )
        return "\n".join(lines).strip()

    def _render_mail_human_summary(
        self,
        *,
        counts: dict[str, int],
        important: list[dict[str, Any]],
        other: list[dict[str, Any]],
    ) -> str:
        lines = [
            f"邮件统计：重要 {counts.get('important', 0)}，普通 {counts.get('low_priority', 0)}，"
            f"归档 {counts.get('archive', 0)}，系统 {counts.get('system', 0)}。"
        ]
        if important:
            lines.append("重要邮件：")
            for item in important[:3]:
                lines.append(
                    f"- {item.get('from') or '未知发件人'} / {item.get('subject') or '无主题'}："
                    f"{item.get('summary') or '暂无摘要'}"
                )
        elif other:
            lines.append("暂无重要邮件。")
        else:
            lines.append("没有新邮件。")
        return "\n".join(lines).strip()

    def _render_notion_human_summary(
        self,
        *,
        pending_today: list[dict[str, Any]],
        high_priority_due_soon: list[dict[str, Any]],
        completed_today: list[dict[str, Any]],
    ) -> str:
        sections: list[str] = []
        if pending_today:
            items = "\n".join(f"- {self._display_notion_item(item)}" for item in pending_today[:5])
            sections.append(f"今日相关未完成：\n\n{items}")
        if high_priority_due_soon:
            items = "\n".join(f"- {self._display_notion_item(item)}" for item in high_priority_due_soon[:5])
            sections.append(f"三天内高优未完成：\n\n{items}")
        if completed_today:
            items = "\n".join(f"- {self._display_notion_item(item)}" for item in completed_today[:5])
            sections.append(f"今日已完成：\n\n{items}")
        if not sections:
            return "Notion 待办暂无需要汇报的事项。"
        return "\n\n".join(sections).strip()

    def _render_reminder_human_summary(
        self,
        *,
        overdue: list[dict[str, Any]],
        due_soon: list[dict[str, Any]],
        total: int,
    ) -> str:
        lines = [f"提醒总数：{total}"]
        if overdue:
            lines.append("过期提醒：")
            for item in overdue[:5]:
                lines.append(
                    f"- {item.get('title') or '未命名提醒'} @ {item.get('next_run_at') or '未知时间'}"
                )
        if due_soon:
            lines.append("半天内提醒：")
            for item in due_soon[:5]:
                lines.append(
                    f"- {item.get('title') or '未命名提醒'} @ {item.get('next_run_at') or '未知时间'}"
                )
        if not overdue and not due_soon:
            lines.append("暂无过期或半天内提醒。")
        return "\n".join(lines).strip()

    def _normalize_email_item(self, item: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            "message_id": str(item.get("message_id") or ""),
            "category": str(item.get("category") or item.get("status") or ""),
            "from": str(item.get("from") or item.get("sender") or ""),
            "subject": str(item.get("subject") or ""),
            "summary": str(item.get("summary") or ""),
            "received_at": str(item.get("received_at") or item.get("date") or ""),
            "attachments": [
                Path(str(path)).name
                for path in (item.get("attachment_paths") or [])
                if str(path).strip()
            ],
        }
        return normalized

    def _normalize_reminder(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("id", item.get("reminder_id")),
            "title": str(item.get("title") or ""),
            "status": str(item.get("status") or ""),
            "next_run_at": str(item.get("next_run_at") or ""),
            "schedule_type": str(item.get("schedule_type") or ""),
            "schedule_value": str(item.get("schedule_value") or ""),
        }

    def _normalize_notion_row(self, row: dict[str, Any]) -> dict[str, Any]:
        properties = row.get("properties", {})
        status = str(row.get("status") or "").strip() or self._extract_first_property(properties, "Status", "状态")
        priority = str(row.get("priority") or "").strip() or self._extract_first_property(
            properties,
            "Priority",
            "优先级",
            "优先程度",
        )
        date_start = str(row.get("date_start") or "").strip()
        date_end = str(row.get("date_end") or "").strip()
        if not date_start:
            date_start, date_end = self._extract_first_date_range(
                properties,
                "日期",
                "Due Date",
                "Due",
                "Deadline",
                "截止日期",
                "截至",
            )
        if not date_end:
            date_end = date_start
        is_date_span = bool(row.get("is_date_span")) or (
            bool(date_start) and bool(date_end) and date_end != date_start
        )
        due_date = str(row.get("due_date") or "").strip() or date_end or date_start
        tags = str(row.get("tags") or "").strip() or self._extract_first_property(properties, "Tags", "标签")
        done_value = str(row.get("done") or "").strip() or self._extract_first_property(
            properties,
            "已完成",
            "Done",
            "完成",
        )
        recurrence = str(row.get("recurrence") or "").strip() or self._extract_first_property(
            properties,
            "Repeat",
            "Repeating",
            "Recurring",
            "Recurrence",
            "重复",
            "重复规则",
            "周期",
            "频率",
        )
        parent_title = str(row.get("parent_title") or "").strip()
        title = str(row.get("title") or "").strip() or self._extract_title(row)
        display_title = str(row.get("display_title") or "").strip()
        if not display_title:
            display_title = f"{parent_title} / {title}" if parent_title else title
        return {
            "id": str(row.get("id") or ""),
            "title": title,
            "display_title": display_title,
            "status": status,
            "priority": priority,
            "due_date": due_date[:10] if due_date else "",
            "date_start": date_start[:10] if date_start else "",
            "date_end": date_end[:10] if date_end else "",
            "is_date_span": is_date_span,
            "tags": tags,
            "done": str(done_value).lower() == "true",
            "recurrence": recurrence,
            "parent_page_id": str(row.get("parent_page_id") or "").strip(),
            "parent_title": parent_title,
        }

    def _extract_title(self, payload: dict[str, Any]) -> str:
        properties = payload.get("properties", {})
        if isinstance(properties, dict):
            for value in properties.values():
                if not isinstance(value, dict):
                    continue
                if str(value.get("type") or "") == "title":
                    return self._extract_property_value(value)
        title = payload.get("title")
        if isinstance(title, list):
            return self._extract_rich_text(title)
        return ""

    def _extract_first_property(self, properties: Any, *names: str) -> str:
        if not isinstance(properties, dict):
            return ""
        for name in names:
            value = properties.get(name)
            if isinstance(value, dict):
                parsed = self._extract_property_value(value)
                if parsed:
                    return parsed
        return ""

    def _extract_property_value(self, value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        prop_type = str(value.get("type") or "").strip()
        payload = value.get(prop_type)
        if prop_type in {"title", "rich_text"} and isinstance(payload, list):
            return self._extract_rich_text(payload)
        if prop_type in {"select", "status"} and isinstance(payload, dict):
            return str(payload.get("name") or "")
        if prop_type == "multi_select" and isinstance(payload, list):
            return ", ".join(
                str(item.get("name") or "")
                for item in payload
                if isinstance(item, dict)
            )
        if prop_type == "checkbox":
            return "true" if bool(payload) else "false"
        if prop_type == "date" and isinstance(payload, dict):
            return str(payload.get("start") or "")
        if prop_type == "number":
            return str(payload)
        return ""

    def _extract_rich_text(self, items: Any) -> str:
        if not isinstance(items, list):
            return ""
        return "".join(
            str(item.get("plain_text") or item.get("text", {}).get("content") or "")
            for item in items
            if isinstance(item, dict)
        ).strip()

    def _is_high_priority(self, item: dict[str, Any]) -> bool:
        text = " ".join(
            [
                str(item.get("priority") or ""),
                str(item.get("tags") or ""),
                str(item.get("status") or ""),
            ]
        ).casefold()
        return any(
            marker in text
            for marker in ("high", "urgent", "important", "高", "紧急", "p0", "p1")
        )

    def _is_daily_recurring_task(self, item: dict[str, Any]) -> bool:
        text = " ".join(
            [
                str(item.get("recurrence") or ""),
                str(item.get("tags") or ""),
            ]
        ).casefold()
        return any(
            marker in text
            for marker in ("每天", "每日", "daily", "everyday", "every day", "habit", "日常", "例行")
        )

    def _display_notion_item(self, item: dict[str, Any]) -> str:
        display_title = str(item.get("display_title") or "").strip()
        if display_title:
            return display_title
        title = str(item.get("title") or "").strip() or "未命名任务"
        parent_title = str(item.get("parent_title") or "").strip()
        if parent_title:
            return f"{parent_title} / {title}"
        return title

    def _notion_item_due_date(self, item: dict[str, Any]) -> date | None:
        due_text = (
            str(item.get("date_end") or "").strip()
            or str(item.get("due_date") or "").strip()
            or str(item.get("date_start") or "").strip()
        )
        return self._parse_date(due_text)

    def _notion_item_matches_today(self, item: dict[str, Any], *, today: date) -> bool:
        start = self._parse_date(
            str(item.get("date_start") or "").strip() or str(item.get("due_date") or "").strip()
        )
        end = self._parse_date(
            str(item.get("date_end") or "").strip()
            or str(item.get("due_date") or "").strip()
            or str(item.get("date_start") or "").strip()
        )
        if start is None:
            return False
        if end is None:
            end = start
        if end < start:
            end = start
        return start <= today <= end

    def _parse_datetime(self, value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=self._now().tzinfo)
        return parsed

    def _parse_date(self, value: Any) -> date | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text[:10]).date()
        except ValueError:
            return None

    def _extract_first_date_range(self, properties: Any, *names: str) -> tuple[str, str]:
        if not isinstance(properties, dict):
            return "", ""
        for name in names:
            value = properties.get(name)
            start, end = self._extract_date_range_property(value)
            if start:
                return start, end
        return "", ""

    def _extract_date_range_property(self, value: Any) -> tuple[str, str]:
        if not isinstance(value, dict):
            return "", ""
        if str(value.get("type") or "").strip() != "date":
            return "", ""
        payload = value.get("date")
        if not isinstance(payload, dict):
            return "", ""
        start = str(payload.get("start") or "").strip()[:10]
        end = str(payload.get("end") or "").strip()[:10] or start
        return start, end

    def _safe_float(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _safe_int(self, value: Any) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0
