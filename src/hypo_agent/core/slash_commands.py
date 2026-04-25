from __future__ import annotations

from datetime import UTC, datetime, timedelta
from dataclasses import dataclass, field
import inspect
import json
from pathlib import Path
import shlex
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
class SlashCommandHelp:
    name: str
    brief: str
    usage: str
    description: str
    examples: list[str]
    category: str


@dataclass
class SlashCommandEntry:
    command: str
    description: str
    handler: Any
    aliases: list[str] = field(default_factory=list)
    help: SlashCommandHelp | None = None


_HELP_CATEGORY_TITLES: dict[str, str] = {
    "system": "System",
    "session": "Session",
    "debug": "Debug",
    "dev": "Dev",
    "other": "Other",
}


class SlashCommandHandler:
    def __init__(
        self,
        *,
        router: SlashRouter,
        session_memory: SlashSessionMemory,
        structured_store: SlashStructuredStore,
        circuit_breaker: Any | None = None,
        skill_manager: Any | None = None,
        coder_task_service: Any | None = None,
        repair_service: Any | None = None,
        memory_gc: Any | None = None,
        model_probe_fn: Callable[[str, Any], Awaitable[Any]] | None = None,
        restart_handler: Callable[..., Awaitable[str] | str] | None = None,
        repo_root: Path | str = ".",
    ) -> None:
        self.router = router
        self.session_memory = session_memory
        self.structured_store = structured_store
        self.circuit_breaker = circuit_breaker
        self.skill_manager = skill_manager
        self.coder_task_service = coder_task_service
        self.repair_service = repair_service
        self.memory_gc = memory_gc
        self.model_probe_fn = model_probe_fn
        self.restart_handler = restart_handler
        self.repo_root = Path(repo_root).resolve(strict=False)
        self._registry: list[SlashCommandEntry] = [
            SlashCommandEntry(
                command="/help",
                aliases=["/h", "/帮助"],
                description="显示所有可用斜杠指令",
                handler=self._handle_help,
                help=SlashCommandHelp(
                    name="/help",
                    brief="查看全部指令，或查询单条指令帮助",
                    usage="/help [<command>|<category>]",
                    description=(
                        "无参数时按分组列出所有斜杠指令。\n"
                        "传入指令名时显示该指令的完整帮助。\n"
                        "传入分类名时只显示该分类下的指令。"
                    ),
                    examples=[
                        "/help",
                        "/help codex",
                        "/help dev",
                    ],
                    category="system",
                ),
            ),
            SlashCommandEntry(
                command="/model status",
                aliases=["/model"],
                description="查看模型路由、延迟、Token 消耗",
                handler=self._handle_model_status,
                help=SlashCommandHelp(
                    name="/model status",
                    brief="查看当前模型路由、探测状态和用量",
                    usage="/model status",
                    description=(
                        "显示默认模型、任务路由、fallback 链路、最近探测结果、历史延迟和 token 用量。\n"
                        "适合在怀疑模型不可用、fallback 频繁或成本异常时排查。\n"
                        "别名 `/model` 会映射到同一条帮助。"
                    ),
                    examples=[
                        "/model status",
                        "/model",
                    ],
                    category="system",
                ),
            ),
            SlashCommandEntry(
                command="/token",
                description="当前会话 Token 用量",
                handler=self._handle_session_token,
                help=SlashCommandHelp(
                    name="/token",
                    brief="查看当前会话的 token 用量",
                    usage="/token",
                    description=(
                        "统计当前 session 内各模型的输入、输出和总 token。\n"
                        "适合排查单个会话是否过长或某次对话消耗异常。"
                    ),
                    examples=["/token"],
                    category="session",
                ),
            ),
            SlashCommandEntry(
                command="/token total",
                description="全局 Token 用量统计",
                handler=self._handle_global_token,
                help=SlashCommandHelp(
                    name="/token total",
                    brief="查看全局 token 用量统计",
                    usage="/token total",
                    description=(
                        "聚合整个实例上所有 session 的模型 token 用量。\n"
                        "适合排查总体成本、模型分布和全局热点。"
                    ),
                    examples=["/token total"],
                    category="session",
                ),
            ),
            SlashCommandEntry(
                command="/kill",
                description="激活全局紧急停止开关",
                handler=self._handle_kill,
                help=SlashCommandHelp(
                    name="/kill",
                    brief="立刻打开全局 Kill Switch",
                    usage="/kill",
                    description=(
                        "激活后，工具执行会被全局阻断，系统进入紧急停止状态。\n"
                        "适合发现错误循环、异常调用或需要手动止损时使用。\n"
                        "恢复需要显式执行 `/resume`。"
                    ),
                    examples=["/kill"],
                    category="system",
                ),
            ),
            SlashCommandEntry(
                command="/resume",
                description="解除全局紧急停止开关",
                handler=self._handle_resume,
                help=SlashCommandHelp(
                    name="/resume",
                    brief="解除全局 Kill Switch",
                    usage="/resume",
                    description=(
                        "当系统被 `/kill` 暂停后，用这条命令恢复正常执行。\n"
                        "如果当前并未处于 kill 状态，会返回提示而不会报错。"
                    ),
                    examples=["/resume"],
                    category="system",
                ),
            ),
            SlashCommandEntry(
                command="/clear",
                aliases=["/cls"],
                description="清空当前会话历史",
                handler=self._handle_clear_session,
                help=SlashCommandHelp(
                    name="/clear",
                    brief="清空当前会话历史记录",
                    usage="/clear",
                    description=(
                        "只清除当前 session 的历史消息，不影响其它会话。\n"
                        "适合上下文污染、需要重新开始一轮对话时使用。\n"
                        "别名 `/cls`。"
                    ),
                    examples=[
                        "/clear",
                        "/cls",
                    ],
                    category="session",
                ),
            ),
            SlashCommandEntry(
                command="/session list",
                description="列出所有会话",
                handler=self._handle_session_list,
                help=SlashCommandHelp(
                    name="/session list",
                    brief="列出当前已有的全部会话",
                    usage="/session list",
                    description=(
                        "显示 session_id、创建时间和消息数。\n"
                        "适合排查历史会话、切换调试对象或确认是否存在旧上下文。"
                    ),
                    examples=["/session list"],
                    category="session",
                ),
            ),
            SlashCommandEntry(
                command="/skills",
                description="查看已注册技能及熔断状态",
                handler=self._handle_skills_status,
                help=SlashCommandHelp(
                    name="/skills",
                    brief="查看技能注册状态和熔断状态",
                    usage="/skills",
                    description=(
                        "显示当前已注册技能、工具列表、是否启用以及熔断器状态。\n"
                        "适合排查某个 skill 没有暴露、被禁用或被熔断的情况。"
                    ),
                    examples=["/skills"],
                    category="debug",
                ),
            ),
            SlashCommandEntry(
                command="/reminders",
                description="列出提醒（可选状态：active/paused/completed/missed）",
                handler=self._handle_reminders,
                help=SlashCommandHelp(
                    name="/reminders",
                    brief="列出提醒，可按状态过滤",
                    usage="/reminders [active|paused|completed|missed|all]",
                    description=(
                        "默认显示非删除提醒；也可追加状态过滤。\n"
                        "适合快速查看当前提醒系统里有哪些待办或已错过提醒。"
                    ),
                    examples=[
                        "/reminders",
                        "/reminders active",
                    ],
                    category="session",
                ),
            ),
            SlashCommandEntry(
                command="/gc",
                description="手动触发 Memory GC",
                handler=self._handle_gc,
                help=SlashCommandHelp(
                    name="/gc",
                    brief="手动触发记忆垃圾回收",
                    usage="/gc",
                    description=(
                        "执行 memory GC 并返回处理结果统计。\n"
                        "适合在排查记忆堆积、旧数据未清理或测试环境污染时使用。"
                    ),
                    examples=["/gc"],
                    category="debug",
                ),
            ),
            SlashCommandEntry(
                command="/repair",
                description="repair 工作流入口：报告、修复、状态、日志、重试",
                handler=self._handle_repair,
                help=SlashCommandHelp(
                    name="/repair",
                    brief="查看报告、发起修复、查看状态与重试",
                    usage=(
                        "/repair help\n"
                        "/repair report [session] [--hours N]\n"
                        "/repair do <issue>\n"
                        "/repair do --from <finding_id> [--verify \"<cmd>\"]\n"
                        "/repair status | /repair logs | /repair abort | /repair retry"
                    ),
                    description=(
                        "这是面向用户的 repair workflow 入口，而不是旧的一次性诊断提交。\n"
                        "支持报告、发起修复、查看状态、查看日志、中止和重试。\n"
                        "默认采用单 repo 单 active repair run 策略；自动重启只会在验证通过且报告明确要求时触发。"
                    ),
                    examples=[
                        "/repair help",
                        "/repair report",
                        '/repair do "Genesis QWen 工具调用后误报无法访问"',
                        "/repair do --from F1",
                        "/repair retry repair-123",
                    ],
                    category="debug",
                ),
            ),
            SlashCommandEntry(
                command="/restart",
                description="确认后执行有限自重启（支持 force 跳过冷却期）",
                handler=self._handle_restart,
                help=SlashCommandHelp(
                    name="/restart",
                    brief="执行有限自重启，支持确认和强制模式",
                    usage="/restart [confirm|force]",
                    description=(
                        "直接输入 `/restart` 只会显示确认说明，不会立即重启。\n"
                        "`/restart confirm` 在冷却期允许时执行重启；`/restart force` 会跳过冷却期。\n"
                        "适合部署后异常、自愈验证或需要手动拉起新进程时使用。"
                    ),
                    examples=[
                        "/restart",
                        "/restart confirm",
                        "/restart force",
                    ],
                    category="system",
                ),
            ),
            SlashCommandEntry(
                command="/codex",
                description="调用 Hypo-Coder 提交、查询、挂载和管理编码任务",
                handler=self._handle_codex,
                help=SlashCommandHelp(
                    name="/codex",
                    brief="提交、查看、挂载和管理 Codex 编码任务",
                    usage=(
                        "/codex <prompt> [--dir /path]\n"
                        "/codex send <instruction>\n"
                        "/codex status <task_id|last>\n"
                        "/codex list [status]\n"
                        "/codex abort <task_id|last>\n"
                        "/codex attach <task_id> [-n N]\n"
                        "/codex logs [task_id|last] [-n N]\n"
                        "/codex detach | /codex done | /codex health"
                    ),
                    description=(
                        "这是面向用户的 Codex 任务入口，用来提交和管理长时编码任务。\n"
                        "最常见用法是直接提交 prompt，也可以追加指令、查看状态、挂载输出或中止任务。\n"
                        "`/codex` 是用户侧命令入口；`coder_submit_task` 是系统内部工具，两者目标一致但使用场景不同。\n"
                        "当你记不住子命令时，优先用 `/help codex` 查看完整说明。"
                    ),
                    examples=[
                        "/codex 修复登录页二维码失效 --dir /tmp/repo",
                        "/codex status last",
                        "/codex attach task-456 -n 20",
                        "/codex logs task-456",
                        "/codex abort last",
                    ],
                    category="dev",
                ),
            ),
        ]

    async def try_handle(self, inbound: Message) -> str | None:
        raw = (inbound.text or "").strip()
        if not raw.startswith("/"):
            return None

        command = " ".join(raw.split())
        command_lower = command.lower()
        if self._is_help_invocation(command_lower):
            return await self._handle_help(inbound)
        if command_lower == "/codex" or command_lower.startswith("/codex "):
            return await self._handle_codex(inbound)
        if command_lower == "/reminders" or command_lower.startswith("/reminders "):
            return await self._handle_reminders(inbound)
        if command_lower == "/repair" or command_lower.startswith("/repair "):
            return await self._handle_repair(inbound)
        if command_lower == "/restart" or command_lower.startswith("/restart "):
            return await self._handle_restart(inbound)

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

    async def _handle_help(self, inbound: Message) -> str:
        raw = (inbound.text or "").strip()
        _, _, remainder = raw.partition(" ")
        query = remainder.strip()
        if not query:
            return self._render_help_index()

        category = self._match_help_category(query)
        if category is not None:
            return self._render_help_index(category=category)

        entry = self._find_help_entry(query)
        if entry is not None:
            return self._render_help_detail(entry)

        return f"未找到帮助主题：{query}\n输入 /help 查看全部命令。"

    def _render_help_index(self, *, category: str | None = None) -> str:
        grouped: dict[str, list[SlashCommandEntry]] = {}
        for entry in self._registry:
            normalized_help = self._normalized_entry_help(entry)
            entry_category = normalized_help.category
            if category is not None and entry_category != category:
                continue
            grouped.setdefault(entry_category, []).append(entry)

        if not grouped:
            title = _HELP_CATEGORY_TITLES.get(category or "", category or "Unknown")
            return f"未找到分类：{title}\n输入 /help 查看全部命令。"

        lines = ["# 斜杠指令帮助"]
        if category is None:
            lines.extend(
                [
                    "",
                    "可用分组：`system`、`session`、`debug`、`dev`",
                    "也可以用 `/help <指令名>` 查看单条指令详情。",
                ]
            )
        for key in self._ordered_help_categories(grouped.keys()):
            lines.extend(["", f"## {_HELP_CATEGORY_TITLES.get(key, key.title())}"])
            for entry in grouped[key]:
                help_meta = self._normalized_entry_help(entry)
                alias_text = f"（别名：{', '.join(entry.aliases)}）" if entry.aliases else ""
                lines.append(f"- `{entry.command}` — {help_meta.brief}{alias_text}")
        return "\n".join(lines)

    def _render_help_detail(self, entry: SlashCommandEntry) -> str:
        help_meta = self._normalized_entry_help(entry)
        lines = [
            f"# {help_meta.name}",
            "",
            f"**分类**：{_HELP_CATEGORY_TITLES.get(help_meta.category, help_meta.category.title())}",
            f"**简介**：{help_meta.brief}",
            "",
            "## 用法",
            "```text",
            help_meta.usage,
            "```",
            "",
            "## 说明",
        ]
        lines.extend(str(help_meta.description or "").strip().splitlines())
        lines.extend(["", "## 示例"])
        for example in help_meta.examples:
            lines.append(f"- `{example}`")
        return "\n".join(lines)

    def _normalized_entry_help(self, entry: SlashCommandEntry) -> SlashCommandHelp:
        if entry.help is not None:
            return entry.help
        return SlashCommandHelp(
            name=entry.command,
            brief=str(entry.description or "").strip() or "查看该指令的帮助",
            usage=entry.command,
            description=str(entry.description or "").strip() or "暂无详细说明。",
            examples=[entry.command],
            category="other",
        )

    def _ordered_help_categories(self, categories: Any) -> list[str]:
        order = {key: index for index, key in enumerate(_HELP_CATEGORY_TITLES.keys())}
        return sorted(categories, key=lambda item: (order.get(str(item), 999), str(item)))

    def _is_help_invocation(self, command_lower: str) -> bool:
        return (
            command_lower == "/help"
            or command_lower == "/h"
            or command_lower == "/帮助"
            or command_lower.startswith("/help ")
            or command_lower.startswith("/h ")
            or command_lower.startswith("/帮助 ")
        )

    def _normalize_help_key(self, raw: str) -> str:
        normalized = " ".join(str(raw or "").strip().lower().split())
        if normalized.startswith("/"):
            normalized = normalized[1:]
        return normalized

    def _find_help_entry(self, raw: str) -> SlashCommandEntry | None:
        normalized = self._normalize_help_key(raw)
        if not normalized:
            return None
        for entry in self._registry:
            candidates = [entry.command, *entry.aliases]
            if any(self._normalize_help_key(candidate) == normalized for candidate in candidates):
                return entry
        return None

    def _match_help_category(self, raw: str) -> str | None:
        normalized = self._normalize_help_key(raw)
        for key, title in _HELP_CATEGORY_TITLES.items():
            if normalized == key or normalized == title.lower():
                return key
        return None

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

    async def _handle_repair(self, inbound: Message) -> str:
        if self.repair_service is None:
            return "Repair 不可用。"

        payload = (inbound.text or "").strip()[len("/repair") :].strip()
        if not payload:
            return str(self.repair_service.render_help())

        try:
            tokens = shlex.split(payload)
        except ValueError:
            return "用法：/repair help | /repair report | /repair do <issue> | /repair status | /repair logs | /repair abort | /repair retry"
        if not tokens:
            return str(self.repair_service.render_help())

        command = tokens[0].strip().lower()
        args = tokens[1:]

        if command == "help":
            return str(self.repair_service.render_help())

        if command == "report":
            scope = "global"
            hours = 24
            idx = 0
            while idx < len(args):
                token = args[idx].strip().lower()
                if token == "session":
                    scope = "session"
                elif token == "--hours":
                    idx += 1
                    if idx >= len(args):
                        return "用法：/repair report [session] [--hours N]"
                    try:
                        hours = int(args[idx])
                    except ValueError:
                        return "用法：/repair report [session] [--hours N]"
                else:
                    return "用法：/repair report [session] [--hours N]"
                idx += 1
            return await self.repair_service.render_report(
                session_id=inbound.session_id,
                scope=scope,
                hours=hours,
            )

        if command == "do":
            finding_id: str | None = None
            verify_commands: list[str] = []
            issue_tokens: list[str] = []
            idx = 0
            while idx < len(args):
                token = args[idx].strip()
                lowered = token.lower()
                if lowered == "--from":
                    idx += 1
                    if idx >= len(args):
                        return "用法：/repair do <issue> | /repair do --from <finding_id> [--verify \"<cmd>\"]"
                    finding_id = args[idx].strip() or None
                elif lowered == "--verify":
                    idx += 1
                    if idx >= len(args):
                        return "用法：/repair do <issue> | /repair do --from <finding_id> [--verify \"<cmd>\"]"
                    verify_commands.append(args[idx].strip())
                else:
                    issue_tokens.append(token)
                idx += 1
            issue = " ".join(token for token in issue_tokens if token).strip()
            if not issue and not finding_id:
                return "用法：/repair do <issue> | /repair do --from <finding_id> [--verify \"<cmd>\"]"
            result = await self.repair_service.start_run(
                session_id=inbound.session_id,
                issue=issue,
                finding_id=finding_id,
                verify_commands=verify_commands,
            )
            if not isinstance(result, dict):
                return str(result)
            status = str(result.get("status") or "unknown")
            run_id = str(result.get("run_id") or "unknown")
            if status == "blocked":
                return f"已有 active repair run：{run_id}\n输入 /repair status 查看当前状态。"
            if status == "error":
                return str(result.get("message") or "Repair 启动失败。")
            return "\n".join(
                [
                    "Repair 已提交",
                    f"run_id={run_id}",
                    f"status={status}",
                ]
            )

        if command == "status":
            run_id = args[0].strip() if args else None
            return await self.repair_service.render_status(
                session_id=inbound.session_id,
                run_id=run_id or None,
            )

        if command == "logs":
            run_id: str | None = None
            line_count = 30
            follow = False
            idx = 0
            while idx < len(args):
                token = args[idx].strip()
                lowered = token.lower()
                if lowered == "--run":
                    idx += 1
                    if idx >= len(args):
                        return "用法：/repair logs [--run <id>] [-n N] [--follow]"
                    run_id = args[idx].strip() or None
                elif lowered == "-n":
                    idx += 1
                    if idx >= len(args):
                        return "用法：/repair logs [--run <id>] [-n N] [--follow]"
                    try:
                        line_count = int(args[idx])
                    except ValueError:
                        return "用法：/repair logs [--run <id>] [-n N] [--follow]"
                elif lowered == "--follow":
                    follow = True
                else:
                    return "用法：/repair logs [--run <id>] [-n N] [--follow]"
                idx += 1
            return await self.repair_service.render_logs(
                session_id=inbound.session_id,
                run_id=run_id,
                line_count=line_count,
                follow=follow,
            )

        if command == "abort":
            run_id: str | None = None
            idx = 0
            while idx < len(args):
                token = args[idx].strip().lower()
                if token == "--run":
                    idx += 1
                    if idx >= len(args):
                        return "用法：/repair abort [--run <id>]"
                    run_id = args[idx].strip() or None
                else:
                    return "用法：/repair abort [--run <id>]"
                idx += 1
            result = await self.repair_service.abort_run(
                session_id=inbound.session_id,
                run_id=run_id,
            )
            if not isinstance(result, dict):
                return str(result)
            if str(result.get("status") or "") == "error":
                return str(result.get("message") or "Repair 中止失败。")
            return f"Repair 已中止：{result.get('run_id', 'unknown')}"

        if command == "retry":
            run_id = args[0].strip() if args else None
            result = await self.repair_service.retry_run(
                session_id=inbound.session_id,
                run_id=run_id or None,
            )
            if not isinstance(result, dict):
                return str(result)
            status = str(result.get("status") or "unknown")
            if status == "blocked":
                return f"已有 active repair run：{result.get('run_id', 'unknown')}\n输入 /repair status 查看当前状态。"
            if status == "error":
                return str(result.get("message") or "Repair 重试失败。")
            return "\n".join(
                [
                    "Repair 已重试提交",
                    f"run_id={result.get('run_id', 'unknown')}",
                    f"status={status}",
                ]
            )

        return "用法：/repair help | /repair report | /repair do <issue> | /repair status | /repair logs | /repair abort | /repair retry"

    async def _handle_restart(self, inbound: Message) -> str:
        payload = (inbound.text or "").strip()[len("/restart") :].strip().lower()
        if not payload:
            return "\n".join(
                [
                    "重启前请先确认。",
                    "确认执行：/restart confirm",
                    "如需跳过冷却期：/restart force",
                ]
            )
        if payload not in {"confirm", "force"}:
            return "用法：/restart | /restart confirm | /restart force"
        if self.restart_handler is None:
            return "重启能力不可用。"
        result = self.restart_handler(
            reason="manual slash command restart",
            force=payload == "force",
        )
        if inspect.isawaitable(result):
            result = await result
        return str(result)

    async def _handle_codex(self, inbound: Message) -> str:
        if self.coder_task_service is None:
            return "Codex 不可用。"

        raw = (inbound.text or "").strip()
        payload = raw[len("/codex") :].strip()
        if not payload:
            return self._codex_help_text()

        lowered = payload.lower()
        if lowered.startswith("send "):
            instruction = payload[5:].strip()
            if not instruction:
                return "用法：/codex send <追加指令>"
            result = await self.coder_task_service.send_to_task(
                session_id=inbound.session_id,
                instruction=instruction,
                task_id="last",
            )
            return f"当前后端暂不支持 /codex send：{result}"

        if lowered.startswith("status"):
            parts = payload.split(maxsplit=1)
            task_id = parts[1].strip() if len(parts) > 1 else "last"
            result = await self.coder_task_service.get_task_status(
                task_id=task_id,
                session_id=inbound.session_id,
            )
            return (
                f"Codex 任务状态：task_id={result.get('task_id', task_id)} "
                f"status={result.get('status', 'unknown')}"
            )

        if lowered.startswith("list"):
            parts = payload.split(maxsplit=1)
            status = parts[1].strip() if len(parts) > 1 else None
            rows = await self.coder_task_service.list_tasks(status=status)
            if not rows:
                return "当前没有 Codex 任务。"
            lines = ["Codex 任务列表："]
            for row in rows:
                task_id = str(row.get("task_id") or row.get("taskId") or "-")
                task_status = str(row.get("status") or "unknown")
                model = str(row.get("model") or "-")
                lines.append(f"- {task_id} | {task_status} | model={model}")
            return "\n".join(lines)

        if lowered.startswith("abort"):
            parts = payload.split(maxsplit=1)
            task_id = parts[1].strip() if len(parts) > 1 else "last"
            result = await self.coder_task_service.abort_task(
                task_id=task_id,
                session_id=inbound.session_id,
            )
            return (
                f"Codex 任务已请求中止：task_id={result.get('task_id', task_id)} "
                f"status={result.get('status', 'unknown')}"
            )

        if lowered == "done":
            await self.coder_task_service.mark_done(inbound.session_id)
            return "已结束当前 Codex 会话绑定。"

        if lowered.startswith("attach "):
            parsed = self._parse_codex_history_args(
                payload[len("attach") :].strip(),
                require_task_id=True,
            )
            if parsed is None:
                return "用法：/codex attach <task_id> [-n N]"
            task_id = str(parsed["task_id"])
            line_count = int(parsed["line_count"])
            status_result = await self.coder_task_service.get_task_status(
                task_id=task_id,
                session_id=inbound.session_id,
            )
            resolved_task_id = (
                str(status_result.get("task_id") or task_id).strip() or task_id
                if task_id == "last"
                else task_id
            )
            status_text = str(status_result.get("status") or "unknown").strip().upper() or "UNKNOWN"
            output = await self.coder_task_service.get_task_output(task_id=resolved_task_id, after=None)
            lines = output.get("lines") if isinstance(output.get("lines"), list) else []
            normalized_lines = [str(line) for line in lines if line is not None]
            cursor = str(output.get("cursor") or "").strip() or None
            await self.coder_task_service.attach_task(
                session_id=inbound.session_id,
                task_id=resolved_task_id,
                initial_cursor=cursor,
            )
            return self._format_codex_attach_reply(
                task_id=resolved_task_id,
                status=status_text,
                lines=normalized_lines,
                line_count=line_count,
            )

        if lowered == "detach":
            await self.coder_task_service.detach_task(inbound.session_id)
            return "已解除当前 Codex 任务挂载。"

        if lowered.startswith("logs"):
            parsed = self._parse_codex_history_args(
                payload[len("logs") :].strip(),
                require_task_id=False,
            )
            if parsed is None:
                return "用法：/codex logs [task_id|last] [-n N]"
            task_id = str(parsed["task_id"])
            line_count = int(parsed["line_count"])
            status_result = await self.coder_task_service.get_task_status(
                task_id=task_id,
                session_id=inbound.session_id,
            )
            resolved_task_id = (
                str(status_result.get("task_id") or task_id).strip() or task_id
                if task_id == "last"
                else task_id
            )
            output = await self.coder_task_service.get_task_output(task_id=resolved_task_id, after=None)
            lines = output.get("lines") if isinstance(output.get("lines"), list) else []
            normalized_lines = [str(line) for line in lines if line is not None]
            return self._format_codex_logs_reply(
                task_id=resolved_task_id,
                lines=normalized_lines,
                line_count=line_count,
            )

        if lowered == "health":
            result = await self.coder_task_service.health()
            status = str(result.get("status") or "unknown").strip() or "unknown"
            return f"Hypo-Coder 状态：{status}"

        submit = self._parse_codex_submit(payload)
        if submit is None:
            return self._codex_help_text()
        result = await self.coder_task_service.submit_task(
            session_id=inbound.session_id,
            prompt=submit["prompt"],
            working_directory=submit["working_directory"],
        )
        return "\n".join(
            [
                "Codex 任务已提交",
                f"task_id={result.get('task_id', 'unknown')}",
                f"status={result.get('status', 'unknown')}",
                f"目录：{result.get('working_directory', 'unknown')}",
            ]
        )

    def _parse_codex_submit(self, payload: str) -> dict[str, str | None] | None:
        try:
            tokens = shlex.split(payload)
        except ValueError:
            return None
        if not tokens:
            return None

        working_directory: str | None = None
        prompt_tokens: list[str] = []
        idx = 0
        while idx < len(tokens):
            token = tokens[idx]
            if token == "--dir":
                idx += 1
                if idx >= len(tokens):
                    return None
                working_directory = tokens[idx].strip() or None
            else:
                prompt_tokens.append(token)
            idx += 1

        prompt = " ".join(token for token in prompt_tokens if token.strip()).strip()
        if not prompt:
            return None
        return {"prompt": prompt, "working_directory": working_directory}

    def _codex_help_text(self) -> str:
        entry = next((item for item in self._registry if item.command == "/codex"), None)
        if entry is None:
            return "Codex 帮助不可用。"
        return self._render_help_detail(entry)

    def _parse_codex_history_args(
        self,
        raw_args: str,
        *,
        require_task_id: bool,
    ) -> dict[str, str | int] | None:
        try:
            tokens = shlex.split(raw_args)
        except ValueError:
            return None

        task_id: str | None = None
        line_count = 30
        idx = 0
        while idx < len(tokens):
            token = tokens[idx].strip()
            if not token:
                idx += 1
                continue
            if token == "-n":
                idx += 1
                if idx >= len(tokens):
                    return None
                try:
                    line_count = int(tokens[idx])
                except ValueError:
                    return None
                if line_count < 0:
                    return None
            elif task_id is None:
                task_id = token
            else:
                return None
            idx += 1

        if task_id is None:
            if require_task_id:
                return None
            task_id = "last"

        return {"task_id": task_id, "line_count": line_count}

    def _format_codex_attach_reply(
        self,
        *,
        task_id: str,
        status: str,
        lines: list[str],
        line_count: int,
    ) -> str:
        total_lines = len(lines)
        if line_count <= 0 or total_lines == 0:
            return "\n".join(
                [
                    f"📜 已挂载 {task_id} | 状态: {status} | 已产出 {total_lines} 行",
                    f"/codex logs {task_id} 查看历史",
                ]
            )

        recent_lines = lines[-line_count:]
        if total_lines <= line_count:
            header = f"📜 {task_id} 已产出 {total_lines} 行输出："
        else:
            header = f"📜 {task_id} 已产出 {total_lines} 行输出，以下是最近 {line_count} 行："
        return "\n".join(
            [
                header,
                f"[Codex | {task_id}]",
                "\n".join(recent_lines),
                f"输入 /codex logs {task_id} 查看完整历史",
            ]
        )

    def _format_codex_logs_reply(
        self,
        *,
        task_id: str,
        lines: list[str],
        line_count: int,
    ) -> str:
        total_lines = len(lines)
        if total_lines == 0:
            return f"📜 {task_id} 暂无输出。"
        if line_count <= 0 or total_lines <= line_count:
            selected_lines = lines
            header = f"📜 {task_id} 已产出 {total_lines} 行输出："
        else:
            selected_lines = lines[-line_count:]
            header = f"📜 {task_id} 已产出 {total_lines} 行输出，以下是最近 {line_count} 行："
        return "\n".join(
            [
                header,
                f"[Codex | {task_id}]",
                "\n".join(selected_lines),
            ]
        )

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

    def _tool_available(self, tool_name: str) -> bool:
        if self.skill_manager is None:
            return False
        for schema in self.skill_manager.get_tools_schema(tool_names={tool_name}):
            if self._read_tool_name(schema) == tool_name:
                return True
        return False

    async def _invoke_tool(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        session_id: str,
    ) -> dict[str, Any] | None:
        if self.skill_manager is None or not self._tool_available(tool_name):
            return None
        result = await self.skill_manager.invoke(tool_name, params, session_id=session_id)
        if result.status != "success" or not isinstance(result.result, dict):
            return None
        return result.result

    async def _load_error_summary(self, *, hours: int, session_id: str) -> dict[str, Any]:
        loaded = await self._invoke_tool("get_error_summary", {"hours": hours}, session_id=session_id)
        if loaded is not None:
            return loaded
        recent_failures = await self._manual_failed_tool_history(hours=hours, limit=10)
        error_types: dict[str, int] = {}
        recent_errors: list[dict[str, Any]] = []
        for item in recent_failures:
            key = f"tool:{self._tool_display_name(item)}"
            error_types[key] = error_types.get(key, 0) + 1
            recent_errors.append(
                {
                    "source": "tool",
                    "timestamp": item.get("created_at"),
                    "type": key,
                    "summary": item.get("tool_name"),
                    "detail": item.get("error_info") or item.get("input_summary") or "",
                }
            )
        return {
            "hours": hours,
            "counts": {"logs": 0, "tool_failures": len(recent_failures), "total": len(recent_failures)},
            "error_types": error_types,
            "recent_errors": recent_errors[:5],
            "log_source_available": False,
            "log_warning": "log inspector unavailable; using structured store fallback",
        }

    async def _load_failed_tool_history(self, *, hours: int, session_id: str) -> list[dict[str, Any]]:
        loaded = await self._invoke_tool(
            "get_tool_history",
            {"success": False, "hours": hours, "limit": 10},
            session_id=session_id,
        )
        if isinstance(loaded, dict):
            items = loaded.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return await self._manual_failed_tool_history(hours=hours, limit=10)

    async def _load_recent_logs(self, *, hours: int, session_id: str) -> list[dict[str, Any]]:
        loaded = await self._invoke_tool(
            "get_recent_logs",
            {"minutes": hours * 60, "level": "error", "limit": 50},
            session_id=session_id,
        )
        if isinstance(loaded, dict):
            items = loaded.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    async def _manual_failed_tool_history(self, *, hours: int, limit: int) -> list[dict[str, Any]]:
        loader = getattr(self.structured_store, "list_tool_invocations", None)
        if not callable(loader):
            return []
        since_iso = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        rows = await loader(limit=limit, since_iso=since_iso)
        failed_rows: list[dict[str, Any]] = []
        for row in rows:
            status = str(row.get("status") or "").strip().lower()
            if status == "success":
                continue
            failed_rows.append(
                {
                    "tool_name": str(row.get("tool_name") or ""),
                    "skill_name": str(row.get("skill_name") or ""),
                    "error_info": str(row.get("error_info") or ""),
                    "input_summary": str(row.get("params_json") or row.get("result_summary") or ""),
                    "created_at": str(row.get("created_at") or ""),
                }
            )
        return failed_rows

    def _format_repair_summary(
        self,
        *,
        hours: int,
        error_summary: dict[str, Any],
        failed_tools: list[dict[str, Any]],
    ) -> str:
        counts = error_summary.get("counts") if isinstance(error_summary.get("counts"), dict) else {}
        lines = [
            "## 修复诊断",
            "",
            f"过去 {hours}h 诊断摘要："
            f" logs={int(counts.get('logs', 0) or 0)}"
            f" tool_failures={int(counts.get('tool_failures', 0) or 0)}"
            f" total={int(counts.get('total', 0) or 0)}",
            "",
            "最近失败工具：",
        ]
        if failed_tools:
            for item in failed_tools[:5]:
                lines.append(
                    f"- {self._tool_display_name(item)} | {self._tool_error_text(item)} | "
                    f"{item.get('created_at') or 'unknown'}"
                )
        else:
            lines.append("- 最近 24h 没有失败工具记录。")
        return "\n".join(lines)

    def _filter_repair_tool_matches(
        self,
        items: list[dict[str, Any]],
        query: str,
    ) -> list[dict[str, Any]]:
        lowered = query.casefold()
        matches: list[dict[str, Any]] = []
        for item in items:
            haystack = " ".join(
                [
                    str(item.get("tool_name") or ""),
                    str(item.get("skill_name") or ""),
                    str(item.get("error_info") or ""),
                    str(item.get("input_summary") or ""),
                    str(item.get("output_summary") or ""),
                ]
            ).casefold()
            if lowered in haystack:
                matches.append(item)
        return matches

    def _filter_repair_log_matches(
        self,
        items: list[dict[str, Any]],
        query: str,
    ) -> list[dict[str, Any]]:
        lowered = query.casefold()
        matches: list[dict[str, Any]] = []
        for item in items:
            context = item.get("context")
            haystack = " ".join(
                [
                    str(item.get("event") or ""),
                    str(item.get("logger") or ""),
                    str(item.get("raw") or ""),
                    json.dumps(context, ensure_ascii=False, sort_keys=True) if isinstance(context, dict) else str(context or ""),
                ]
            ).casefold()
            if lowered in haystack:
                matches.append(item)
        return matches

    def _build_repair_task_prompt(
        self,
        *,
        issue: str,
        error_summary: dict[str, Any],
        failed_tools: list[dict[str, Any]],
        recent_logs: list[dict[str, Any]],
    ) -> str:
        lines = [
            "Investigate and fix a Hypo-Agent regression.",
            f"Issue report: {issue}",
            f"Working directory: {self.repo_root}",
            "",
            "Diagnostic summary:",
            json.dumps(error_summary, ensure_ascii=False, sort_keys=True),
            "",
            "Related failed tools:",
        ]
        if failed_tools:
            for item in failed_tools[:5]:
                lines.append(
                    f"- {self._tool_display_name(item)} | "
                    f"{self._tool_error_text(item)} | {item.get('created_at') or 'unknown'}"
                )
        else:
            lines.append("- none")
        lines.extend(["", "Related logs:"])
        if recent_logs:
            for item in recent_logs[:5]:
                lines.append(f"- {item.get('timestamp') or 'unknown'} | {item.get('event') or item.get('raw') or 'unknown'}")
        else:
            lines.append("- none")
        lines.extend(
            [
                "",
                "Requirements:",
                "- diagnose the root cause first",
                "- implement the minimal fix",
                "- add or update tests",
                "- run targeted verification",
                "- summarize changed files and verification results",
            ]
        )
        return "\n".join(lines)

    def _build_repair_suggestion(
        self,
        *,
        issue: str,
        failed_tools: list[dict[str, Any]],
        recent_logs: list[dict[str, Any]],
    ) -> str:
        if failed_tools:
            top_tool = self._tool_display_name(failed_tools[0])
            top_error = self._tool_error_text(failed_tools[0])
            return f"- 先复现“{issue}”，重点检查 {top_tool}，最近错误为：{top_error}"
        if recent_logs:
            top_log = recent_logs[0]
            return f"- 先检查错误日志事件 {top_log.get('event') or top_log.get('raw') or 'unknown'}，确认是否为稳定代码/配置问题"
        return f"- 先复现“{issue}”，再检查最近 24h 的失败工具与错误日志"

    def _tool_display_name(self, item: dict[str, Any]) -> str:
        skill_name = str(item.get("skill_name") or "").strip()
        tool_name = str(item.get("tool_name") or "").strip() or "unknown"
        if skill_name:
            return f"{skill_name}.{tool_name}"
        return tool_name

    def _tool_error_text(self, item: dict[str, Any]) -> str:
        return str(item.get("error_info") or item.get("detail") or item.get("input_summary") or "unknown").strip()

    def _read_tool_name(self, schema: dict[str, Any]) -> str:
        function_payload = schema.get("function")
        if isinstance(function_payload, dict):
            return str(function_payload.get("name") or "").strip()
        return ""
