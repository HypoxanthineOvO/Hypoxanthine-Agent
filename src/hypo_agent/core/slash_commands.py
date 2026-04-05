from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from typing import Any, Awaitable, Callable, Protocol

import structlog

from hypo_agent.core.model_connectivity import probe_model
from hypo_agent.models import Message

logger = structlog.get_logger("hypo_agent.slash_commands")


class SlashRouter(Protocol):
    config: Any

    def get_model_for_task(self, task_type: str) -> str: ...

    def get_fallback_chain(self, start_model: str) -> list[str]: ...


class SlashSessionMemory(Protocol):
    def clear_session(self, session_id: str) -> None: ...

    def list_sessions(self) -> list[dict[str, object]]: ...


class SlashStructuredStore(Protocol):
    async def summarize_token_usage(
        self,
        session_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def summarize_latency_by_model(self) -> list[dict[str, Any]]: ...

    async def list_reminders(
        self,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]: ...


@dataclass
class SlashCommandEntry:
    command: str
    description: str
    handler: Any
    aliases: list[str] = field(default_factory=list)


class SlashCommandHandler:
    def __init__(
        self,
        *,
        router: SlashRouter,
        session_memory: SlashSessionMemory,
        structured_store: SlashStructuredStore,
        circuit_breaker: Any | None = None,
        skill_manager: Any | None = None,
        memory_gc: Any | None = None,
        model_probe_fn: Callable[[str, Any], Awaitable[Any]] | None = None,
    ) -> None:
        self.router = router
        self.session_memory = session_memory
        self.structured_store = structured_store
        self.circuit_breaker = circuit_breaker
        self.skill_manager = skill_manager
        self.memory_gc = memory_gc
        self.model_probe_fn = model_probe_fn
        self._registry: list[SlashCommandEntry] = [
            SlashCommandEntry(
                command="/help",
                aliases=["/h", "/帮助"],
                description="显示所有可用斜杠指令",
                handler=self._handle_help,
            ),
            SlashCommandEntry(
                command="/model status",
                aliases=["/model"],
                description="查看模型路由、延迟、Token 消耗",
                handler=self._handle_model_status,
            ),
            SlashCommandEntry(
                command="/token",
                description="当前会话 Token 用量",
                handler=self._handle_session_token,
            ),
            SlashCommandEntry(
                command="/token total",
                description="全局 Token 用量统计",
                handler=self._handle_global_token,
            ),
            SlashCommandEntry(
                command="/kill",
                description="激活全局紧急停止开关",
                handler=self._handle_kill,
            ),
            SlashCommandEntry(
                command="/resume",
                description="解除全局紧急停止开关",
                handler=self._handle_resume,
            ),
            SlashCommandEntry(
                command="/clear",
                aliases=["/cls"],
                description="清空当前会话历史",
                handler=self._handle_clear_session,
            ),
            SlashCommandEntry(
                command="/session list",
                description="列出所有会话",
                handler=self._handle_session_list,
            ),
            SlashCommandEntry(
                command="/skills",
                description="查看已注册技能及熔断状态",
                handler=self._handle_skills_status,
            ),
            SlashCommandEntry(
                command="/reminders",
                description="列出提醒（可选状态：active/paused/completed/missed）",
                handler=self._handle_reminders,
            ),
            SlashCommandEntry(
                command="/gc",
                description="手动触发 Memory GC",
                handler=self._handle_gc,
            ),
        ]

    async def try_handle(self, inbound: Message) -> str | None:
        raw = (inbound.text or "").strip()
        if not raw.startswith("/"):
            return None

        command = " ".join(raw.split())
        command_lower = command.lower()
        if command_lower == "/reminders" or command_lower.startswith("/reminders "):
            return await self._handle_reminders(inbound)

        sorted_entries = sorted(
            self._registry,
            key=lambda entry: len(entry.command),
            reverse=True,
        )
        for entry in sorted_entries:
            if command_lower == entry.command.lower():
                return await self._execute_entry(entry, inbound)
            alias_map = {alias.lower() for alias in entry.aliases}
            if command_lower in alias_map:
                return await self._execute_entry(entry, inbound)

        return f"未知斜杠指令：{command}\n输入 /help 查看可用命令。"

    async def _execute_entry(self, entry: SlashCommandEntry, inbound: Message) -> str:
        result = entry.handler(inbound)
        if inspect.isawaitable(result):
            resolved = await result
        else:
            resolved = result
        return str(resolved)

    def _handle_help(self, _: Message) -> str:
        lines = [
            "📋 可用斜杠指令",
            "",
            "| 指令 | 别名 | 说明 |",
            "|------|------|------|",
        ]
        for entry in self._registry:
            alias_text = ", ".join(entry.aliases) if entry.aliases else "—"
            lines.append(f"| {entry.command} | {alias_text} | {entry.description} |")
        return "\n".join(lines)

    async def _handle_model_status(self, _: Message) -> str:
        token_summary = await self.structured_store.summarize_token_usage()
        latency_summary = await self.structured_store.summarize_latency_by_model()
        token_by_model = {row["resolved_model"]: row for row in token_summary["rows"]}
        latency_by_model = {row["resolved_model"]: row for row in latency_summary}
        probe_by_model = await self._probe_models()

        lines: list[str] = [
            "## 🤖 模型状态",
            "",
            f"**默认模型**: {self.router.config.default_model}",
            "",
            "### 任务路由",
            "",
            "| 任务类型 | 模型 |",
            "|---------|------|",
        ]
        if self.router.config.task_routing:
            for task_type, model_name in sorted(self.router.config.task_routing.items()):
                lines.append(f"| {task_type} | {model_name} |")
        else:
            lines.append("| — | — |")

        lines.extend(
            [
                "",
                "### 模型详情",
                "",
                "| 模型 | Provider | Fallback | 最近探测 | 历史延迟 | Token (入/出/总) |",
                "|------|----------|----------|----------|----------|-------------------|",
            ]
        )
        for model_name, cfg in sorted(self.router.config.models.items()):
            provider = str(cfg.provider or "—")
            fallback = f"→ {cfg.fallback}" if cfg.fallback else "（无）"
            probe_row = probe_by_model.get(model_name, {"status_text": "➖ 未探测"})
            token_row = token_by_model.get(model_name)
            latency_row = latency_by_model.get(model_name)

            token_cell = "—"
            if token_row is not None:
                token_cell = "/".join(
                    [
                        self._format_token(token_row.get("input_tokens")),
                        self._format_token(token_row.get("output_tokens")),
                        self._format_token(token_row.get("total_tokens")),
                    ]
                )

            latency_cell = "—"
            if latency_row is not None and latency_row.get("avg_latency_ms") is not None:
                latency_cell = f"{int(round(float(latency_row['avg_latency_ms'])))}ms"

            lines.append(
                f"| {model_name} | {provider} | {fallback} | "
                f"{probe_row['status_text']} | {latency_cell} | {token_cell} |"
            )

        return "\n".join(lines)

    async def _handle_session_token(self, inbound: Message) -> str:
        summary = await self.structured_store.summarize_token_usage(session_id=inbound.session_id)
        rows = summary["rows"]
        totals = summary["totals"]
        if not rows:
            return f"当前会话（{inbound.session_id}）暂无 Token 用量记录。"

        lines = [f"当前会话（{inbound.session_id}）Token 用量："]
        for row in rows:
            lines.append(
                f"- {row['resolved_model']}: "
                f"入={row['input_tokens']} 出={row['output_tokens']} 总={row['total_tokens']}"
            )
        lines.append(
            f"合计：入={totals['input_tokens']} 出={totals['output_tokens']} 总={totals['total_tokens']}"
        )
        return "\n".join(lines)

    async def _handle_global_token(self, _: Message) -> str:
        summary = await self.structured_store.summarize_token_usage(session_id=None)
        rows = summary["rows"]
        totals = summary["totals"]
        if not rows:
            return "暂无全局 Token 用量记录。"

        lines = ["全局 Token 用量统计："]
        for row in rows:
            lines.append(
                f"- {row['resolved_model']}: "
                f"入={row['input_tokens']} 出={row['output_tokens']} 总={row['total_tokens']}"
            )
        lines.append(
            f"合计：入={totals['input_tokens']} 出={totals['output_tokens']} 总={totals['total_tokens']}"
        )
        return "\n".join(lines)

    def _handle_kill(self, _: Message) -> str:
        if self.circuit_breaker is None:
            return "Kill Switch 不可用。"

        if not bool(self.circuit_breaker.get_global_kill_switch()):
            self.circuit_breaker.set_global_kill_switch(True)
        logger.warning("slash.kill_switch.toggled", enabled=True)
        return "⚠️ Kill Switch 已激活。所有执行已停止。发送 /resume 恢复。"

    def _handle_resume(self, _: Message) -> str:
        if self.circuit_breaker is None:
            return "Kill Switch 不可用。"

        if not bool(self.circuit_breaker.get_global_kill_switch()):
            return "当前未处于 Kill 状态。"
        self.circuit_breaker.set_global_kill_switch(False)
        logger.warning("slash.kill_switch.toggled", enabled=False)
        return "✅ Kill Switch 已解除，恢复正常执行。"

    def _handle_clear_session(self, inbound: Message) -> str:
        self.session_memory.clear_session(inbound.session_id)
        return f"会话 {inbound.session_id} 已清空。"

    def _handle_session_list(self, _: Message) -> str:
        sessions = self.session_memory.list_sessions()
        if not sessions:
            return "暂无会话记录。"

        lines = ["会话列表："]
        for item in sessions:
            lines.append(
                f"- session_id={item['session_id']} "
                f"created_at={item['created_at']} "
                f"messages={item['message_count']}"
            )
        return "\n".join(lines)

    def _handle_skills_status(self, inbound: Message) -> str:
        if self.skill_manager is None:
            return "## 🔧 已注册技能\n\n暂无技能管理器。"

        skills = self.skill_manager.list_skills()
        global_kill = bool(
            self.circuit_breaker.get_global_kill_switch()
            if self.circuit_breaker is not None
            else False
        )
        lines = [
            "## 🔧 已注册技能",
            "",
            "| 技能 | 状态 | 熔断器 | 工具 | 说明 |",
            "|------|------|--------|------|------|",
        ]
        for item in skills:
            tool_names = [name for name in item.get("tools", []) if isinstance(name, str) and name]
            status_text = "✅ 启用" if bool(item.get("enabled", True)) else "❌ 禁用"
            breaker_text = "🟢 正常"
            if global_kill:
                breaker_text = "🔴 熔断"
            elif self.circuit_breaker is not None:
                for tool_name in tool_names:
                    allowed, _ = self.circuit_breaker.can_execute(tool_name, inbound.session_id)
                    if not allowed:
                        breaker_text = "🔴 熔断"
                        break

            tool_cell = ", ".join(tool_names) if tool_names else "—"
            desc = self._to_short_chinese_description(
                item.get("description"),
                item.get("name"),
                tool_names,
            )
            lines.append(
                f"| {item.get('name', '未知技能')} | {status_text} | "
                f"{breaker_text} | {tool_cell} | {desc} |"
            )

        lines.extend(["", f"⚡ Kill Switch: {'开启' if global_kill else '关闭'}"])
        return "\n".join(lines)

    async def _handle_reminders(self, inbound: Message) -> str:
        status_filter: str | None = None
        parts = (inbound.text or "").strip().split()
        if len(parts) >= 2:
            requested = parts[1].strip().lower()
            if requested not in {"all", "*"}:
                status_filter = requested

        rows = await self.structured_store.list_reminders(status=status_filter)
        if not rows:
            if status_filter:
                return f"暂无状态为 {status_filter} 的提醒。"
            return "暂无提醒。"

        header = "提醒列表："
        if status_filter:
            header = f"提醒列表（{status_filter}）："
        lines = [header]
        for row in rows:
            status = str(row.get("status") or "").strip().lower()
            badge = self._reminder_status_badge(status)
            lines.append(
                f"- #{row.get('id')} {badge} {row.get('title', '')} "
                f"({row.get('schedule_type', '')}: {row.get('schedule_value', '')})"
            )
        return "\n".join(lines)

    async def _handle_gc(self, _: Message) -> str:
        if self.memory_gc is None or not callable(getattr(self.memory_gc, "run", None)):
            return "Memory GC 不可用。"

        result = self.memory_gc.run()
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            return "Memory GC 已触发。"
        processed = int(result.get("processed_count") or 0)
        skipped = int(result.get("skipped_count") or 0)
        errors = int(result.get("error_count") or 0)
        return f"Memory GC 完成：processed={processed} skipped={skipped} errors={errors}"

    def _reminder_status_badge(self, status: str) -> str:
        mapping = {
            "active": "🟢 active",
            "completed": "✅ completed",
            "missed": "⏰ missed",
            "paused": "⏸️ paused",
            "deleted": "🗑️ deleted",
        }
        return mapping.get(status, f"❔ {status or 'unknown'}")

    def _format_token(self, value: Any) -> str:
        if value is None:
            return "—"
        number = float(value)
        if number >= 1000:
            return f"{number / 1000:.1f}K"
        if number.is_integer():
            return str(int(number))
        return f"{number:.1f}"

    def _to_short_chinese_description(
        self,
        description: Any,
        skill_name: Any,
        tool_names: list[str],
    ) -> str:
        text = str(description or "").strip()
        combined = " ".join([str(skill_name or ""), text, ",".join(tool_names)]).lower()
        mapping = [
            ("exec", "命令执行"),
            ("exec_command", "命令执行"),
            ("tmux", "终端会话操控"),
            ("code_run", "沙箱代码执行"),
            ("run_code", "沙箱代码执行"),
            ("filesystem", "文件系统操作"),
            ("read_file", "文件系统操作"),
            ("write_file", "文件系统操作"),
            ("echo", "回显测试工具"),
        ]
        for keyword, translated in mapping:
            if keyword in combined:
                return translated

        if any("\u4e00" <= ch <= "\u9fff" for ch in text):
            return text[:10]
        return "技能能力"

    async def _probe_models(self) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for model_name, cfg in sorted(self.router.config.models.items()):
            raw_result = await self._probe_single_model(model_name, cfg)
            results[model_name] = self._normalize_probe_result(raw_result)
        return results

    async def _probe_single_model(self, model_name: str, cfg: Any) -> Any:
        if self.model_probe_fn is not None:
            try:
                return await self.model_probe_fn(model_name, cfg)
            except Exception as exc:
                logger.warning(
                    "slash.model_probe_failed",
                    model_name=model_name,
                    error=str(exc),
                )
                return {
                    "ok": False,
                    "latency_ms": 0.0,
                    "status_text": f"❌ 失败: {self._short_probe_error(str(exc))}",
                }

        acompletion_fn = getattr(self.router, "_acompletion", None)
        if not callable(acompletion_fn):
            return {"ok": False, "latency_ms": 0.0, "status_text": "➖ 未配置"}

        try:
            result = await probe_model(
                model_name,
                cfg,
                acompletion_fn=acompletion_fn,
                timeout_seconds=5.0,
            )
        except Exception as exc:
            logger.warning(
                "slash.model_probe_failed",
                model_name=model_name,
                error=str(exc),
            )
            return {
                "ok": False,
                "latency_ms": 0.0,
                "status_text": f"❌ 失败: {self._short_probe_error(str(exc))}",
            }
        return result

    def _normalize_probe_result(self, raw_result: Any) -> dict[str, Any]:
        if isinstance(raw_result, dict):
            status_text = str(raw_result.get("status_text") or "").strip()
            ok = bool(raw_result.get("ok"))
            latency_ms = float(raw_result.get("latency_ms") or 0.0)
            if status_text:
                return {
                    "ok": ok,
                    "latency_ms": latency_ms,
                    "status_text": status_text,
                }

        error = str(getattr(raw_result, "error", "") or "").strip()
        connectivity_ok = bool(getattr(raw_result, "connectivity_ok", False))
        if not getattr(raw_result, "litellm_model", None):
            return {"ok": False, "latency_ms": 0.0, "status_text": "➖ 未配置"}
        if connectivity_ok:
            return {
                "ok": True,
                "latency_ms": float(getattr(raw_result, "latency_ms", 0.0) or 0.0),
                "status_text": "✅ 成功",
            }
        return {
            "ok": False,
            "latency_ms": float(getattr(raw_result, "latency_ms", 0.0) or 0.0),
            "status_text": f"❌ 失败: {self._short_probe_error(error)}",
        }

    def _short_probe_error(self, error: str) -> str:
        text = " ".join(error.split())
        if not text:
            return "unknown"
        lowered = text.lower()
        if "timeout" in lowered:
            return "timeout"
        if "invalidsubscription" in lowered:
            return "InvalidSubscription"
        if len(text) <= 40:
            return text
        return text[:37] + "..."
