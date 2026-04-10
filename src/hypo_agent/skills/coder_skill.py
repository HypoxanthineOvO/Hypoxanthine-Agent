from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from hypo_agent.channels.coder import CoderClient, CoderTaskService, CoderUnavailableError
from hypo_agent.core.config_loader import load_secrets_config
from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill


class CoderSkill(BaseSkill):
    name = "coder"
    description = "调用 Hypo-Coder 提交、查询、列出和中止编码任务。"
    required_permissions: list[str] = []

    def __init__(
        self,
        *,
        secrets_path: Path | str = "config/secrets.yaml",
        coder_client: Any | None = None,
        coder_task_service: CoderTaskService | None = None,
        webhook_url: str | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.secrets_path = Path(secrets_path)
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.webhook_url = str(webhook_url or "").strip() or None
        self._client = coder_client or self._build_client_from_config()
        self._service = coder_task_service or CoderTaskService(
            coder_client=self._client,
            structured_store=None,
            webhook_url=self.webhook_url,
            now_fn=self.now_fn,
        )

    @property
    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "coder_submit_task",
                    "description": "Submit a coding task to Hypo-Coder.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string"},
                            "working_directory": {"type": "string"},
                            "model": {"type": "string"},
                        },
                        "required": ["prompt", "working_directory"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "coder_task_status",
                    "description": "Get the current status and result of a coder task.",
                    "parameters": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "coder_list_tasks",
                    "description": "List coder tasks, optionally filtered by status.",
                    "parameters": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "coder_abort_task",
                    "description": "Abort a coder task.",
                    "parameters": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "coder_health",
                    "description": "Check whether the Hypo-Coder service is healthy.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    async def execute(self, tool_name: str, params: dict[str, Any]) -> SkillOutput:
        try:
            if tool_name == "coder_submit_task":
                prompt = str(params.get("prompt") or "").strip()
                working_directory = str(params.get("working_directory") or "").strip()
                model = str(params.get("model") or "").strip() or None
                if not prompt:
                    return SkillOutput(status="error", error_info="prompt is required")
                if not working_directory:
                    return SkillOutput(status="error", error_info="working_directory is required")
                return SkillOutput(
                    status="success",
                    result=await self.coder_submit_task(
                        prompt=prompt,
                        working_directory=working_directory,
                        model=model,
                    ),
                )
            if tool_name == "coder_task_status":
                task_id = str(params.get("task_id") or "").strip()
                if not task_id:
                    return SkillOutput(status="error", error_info="task_id is required")
                return SkillOutput(status="success", result=await self.coder_task_status(task_id))
            if tool_name == "coder_list_tasks":
                status = str(params.get("status") or "").strip() or None
                return SkillOutput(status="success", result=await self.coder_list_tasks(status=status))
            if tool_name == "coder_abort_task":
                task_id = str(params.get("task_id") or "").strip()
                if not task_id:
                    return SkillOutput(status="error", error_info="task_id is required")
                return SkillOutput(status="success", result=await self.coder_abort_task(task_id))
            if tool_name == "coder_health":
                return SkillOutput(status="success", result=await self.coder_health())
        except CoderUnavailableError as exc:
            return SkillOutput(status="error", error_info=str(exc))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return SkillOutput(status="error", error_info=str(exc))
        return SkillOutput(status="error", error_info=f"Unsupported tool '{tool_name}'")

    async def coder_submit_task(
        self,
        *,
        prompt: str,
        working_directory: str,
        model: str | None = None,
    ) -> str:
        payload = await self._service.submit_task(
            session_id="skill-coder",
            prompt=prompt,
            working_directory=working_directory,
            model=model,
        )
        task_id = str(payload.get("task_id") or payload.get("taskId") or "").strip() or "unknown"
        return f"任务已提交，task_id={task_id}，正在执行中。完成后会通知你。"

    async def coder_task_status(self, task_id: str) -> str:
        task = await self._service.get_task_status(task_id=task_id)
        status = str(task.get("status") or "unknown").strip().lower()
        if status in {"queued", "running", "in_progress"}:
            minutes = self._elapsed_minutes(task)
            return f"任务 {task_id} 正在执行中（已运行 {minutes} 分钟）"
        if status == "completed":
            result = task.get("result") if isinstance(task.get("result"), dict) else {}
            summary = str(result.get("summary") or "暂无摘要").strip() or "暂无摘要"
            changes = self._format_file_changes(result.get("fileChanges"))
            tests = self._format_tests(result.get("testsPassed"))
            return "\n".join(
                [
                    f"任务 {task_id} 已完成",
                    f"摘要：{summary}",
                    f"文件变更：{changes}",
                    f"测试：{tests}",
                ]
            )
        if status == "failed":
            error = str(task.get("error") or task.get("message") or "未知错误").strip() or "未知错误"
            return f"任务 {task_id} 失败：{error}"
        if status == "aborted":
            return f"任务 {task_id} 已中止"
        return f"任务 {task_id} 当前状态：{status or 'unknown'}"

    async def coder_list_tasks(self, *, status: str | None = None) -> str:
        tasks = await self._service.list_tasks(status=status)
        if not tasks:
            return "当前没有编码任务"
        lines = [f"编码任务列表（共 {len(tasks)} 个）"]
        for item in tasks:
            task_id = str(item.get("taskId") or item.get("id") or "-").strip() or "-"
            task_status = str(item.get("status") or "unknown").strip() or "unknown"
            model = str(item.get("model") or "-").strip() or "-"
            lines.append(f"- {task_id} | {task_status} | model={model}")
        return "\n".join(lines)

    async def coder_abort_task(self, task_id: str) -> str:
        payload = await self._service.abort_task(task_id=task_id)
        status = str(payload.get("status") or "aborted").strip() or "aborted"
        return f"任务 {task_id} 已请求中止，当前状态：{status}"

    async def coder_health(self) -> str:
        payload = await self._service.health()
        status = str(payload.get("status") or "unknown").strip() or "unknown"
        return f"Hypo-Coder 状态：{status}"

    def _build_client_from_config(self) -> CoderClient:
        try:
            secrets = load_secrets_config(self.secrets_path)
        except FileNotFoundError as exc:
            raise ValueError(
                "Missing Hypo-Coder config: config/secrets.yaml -> "
                "services.hypo_coder.base_url/agent_token/webhook_secret"
            ) from exc
        services = secrets.services
        coder_cfg = services.hypo_coder if services is not None else None
        base_url = str(coder_cfg.base_url).strip() if coder_cfg is not None else ""
        agent_token = str(coder_cfg.agent_token).strip() if coder_cfg is not None else ""
        webhook_secret = str(coder_cfg.webhook_secret).strip() if coder_cfg is not None else ""
        if not base_url or not agent_token or not webhook_secret:
            raise ValueError(
                "Missing Hypo-Coder config: config/secrets.yaml -> "
                "services.hypo_coder.base_url/agent_token/webhook_secret"
            )
        if not self.webhook_url:
            webhook_url = str(getattr(coder_cfg, "webhook_url", "") or "").strip()
            self.webhook_url = webhook_url or None
        return CoderClient(base_url=base_url, agent_token=agent_token)

    def _elapsed_minutes(self, task: dict[str, Any]) -> int:
        start = self._parse_datetime(task.get("startedAt")) or self._parse_datetime(task.get("createdAt"))
        end = self._parse_datetime(task.get("updatedAt")) or self.now_fn()
        if start is None:
            return 0
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        return max(0, int((end - start).total_seconds() // 60))

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed

    @staticmethod
    def _format_file_changes(payload: Any) -> str:
        if not isinstance(payload, list) or not payload:
            return "无"
        mapping = {"modified": "修改", "added": "新增", "deleted": "删除"}
        items: list[str] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or item.get("file") or "-").strip() or "-"
            change = str(item.get("changeType") or item.get("change") or "").strip().lower()
            change_label = mapping.get(change, change or "未知")
            items.append(f"{path}（{change_label}）")
        return ", ".join(items) if items else "无"

    @staticmethod
    def _format_tests(value: Any) -> str:
        if value is True:
            return "通过"
        if value is False:
            return "失败"
        return "未知"
