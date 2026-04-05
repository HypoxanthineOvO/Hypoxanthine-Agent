from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
import os
from pathlib import Path
import platform
import re
from typing import Any, Awaitable, Callable

from hypo_agent.core.config_loader import get_memory_dir
from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill

_LOAD_RE = re.compile(r"load average[s]?:\s*([0-9.]+),\s*([0-9.]+),\s*([0-9.]+)")


class HeartbeatSnapshotSkill(BaseSkill):
    name = "heartbeat_snapshot"
    description = "Provide structured heartbeat snapshots for system, mail, notion todo, and reminders."
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        email_skill: Any | None = None,
        reminder_skill: Any | None = None,
        notion_skill: Any | None = None,
        people_index_path: Path | str | None = None,
        system_snapshot_provider: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        now_provider: Callable[[], datetime] | None = None,
        now_iso_provider: Callable[[], str] | None = None,
    ) -> None:
        self.email_skill = email_skill
        self.reminder_skill = reminder_skill
        self.notion_skill = notion_skill
        self.people_index_path = Path(people_index_path or (get_memory_dir() / "people" / "index.md"))
        self.system_snapshot_provider = system_snapshot_provider
        self.now_provider = now_provider
        self.now_iso_provider = now_iso_provider

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            self._tool_schema(
                "get_system_snapshot",
                "获取结构化系统快照，包括负载、内存、磁盘、GPU 和按人聚合的进程概览。返回 JSON。",
            ),
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
                "一次性获取完整 heartbeat 结构化快照，聚合 system/mail/notion/reminders 四个 section。返回 JSON。",
            ),
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        del params
        if tool_name == "get_system_snapshot":
            return SkillOutput(status="success", result=await self._get_system_snapshot())
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
            self._safe_section("system", self._get_system_snapshot()),
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
        }

    async def _get_notion_todo_snapshot(self) -> dict[str, Any]:
        notion_skill = self.notion_skill
        database_id = str(getattr(notion_skill, "_todo_database_id", "") or "").strip()
        client = getattr(notion_skill, "_client", None)
        if notion_skill is None or client is None or not database_id:
            return {"available": False, "error": "notion todo database is unavailable"}
        rows = await client.query_database(database_id, filter=None, sorts=None, page_size=50)
        now = self._now().date()
        due_soon_limit = now + timedelta(days=3)
        pending_today: list[dict[str, Any]] = []
        high_priority_due_soon: list[dict[str, Any]] = []
        completed_today: list[dict[str, Any]] = []
        other_pending: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = self._normalize_notion_row(row)
            due_date = self._parse_date(item.get("due_date"))
            if due_date is None:
                continue
            done = bool(item.get("done"))
            is_high = self._is_high_priority(item)
            if due_date == now and not done:
                pending_today.append(item)
            if now <= due_date <= due_soon_limit and not done and is_high:
                high_priority_due_soon.append(item)
            if due_date == now and done:
                completed_today.append(item)
            if due_date >= now and not done:
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
        }

    async def _get_system_snapshot(self) -> dict[str, Any]:
        if self.system_snapshot_provider is not None:
            payload = await self.system_snapshot_provider()
            payload.setdefault("available", True)
            payload.setdefault("checked_at", self._now_iso())
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
        return {
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
            "errors": errors,
        }

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
                    "top_processes": [],
                },
            )
            entry["cpu_percent_total"] += float(proc.get("pcpu") or 0.0)
            entry["memory_percent_total"] += float(proc.get("pmem") or 0.0)
            if len(entry["top_processes"]) < 3:
                command = str(proc.get("args") or proc.get("comm") or "").strip()
                entry["top_processes"].append(command[:160])
            for gpu_item in pid_to_gpu.get(int(proc.get("pid") or 0), []):
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
                    "top_processes": item["top_processes"],
                }
            )
        ordered.sort(key=lambda item: (item["cpu_percent_total"], item["memory_percent_total"]), reverse=True)
        return ordered

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
        status = self._extract_first_property(properties, "Status", "状态")
        priority = self._extract_first_property(properties, "Priority", "优先级", "优先程度")
        due_date = self._extract_first_property(properties, "日期", "Due Date", "Due", "Deadline", "截止日期", "截至")
        tags = self._extract_first_property(properties, "Tags", "标签")
        done_value = self._extract_first_property(properties, "已完成", "Done", "完成")
        return {
            "id": str(row.get("id") or ""),
            "title": self._extract_title(row),
            "status": status,
            "priority": priority,
            "due_date": due_date[:10] if due_date else "",
            "tags": tags,
            "done": str(done_value).lower() == "true",
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

    def _parse_date(self, value: Any) -> datetime.date | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text[:10]).date()
        except ValueError:
            return None

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
