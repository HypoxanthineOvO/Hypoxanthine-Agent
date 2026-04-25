# Repair Workflow Design

**Date:** 2026-04-22

**Goal**

把 Hypo-Agent 的 repair 能力从“诊断后直接丢给 Codex 一次性修复任务”升级成一套一等工作流：
- `/repair report` 默认汇报最近全局问题，也支持切到当前会话
- `/repair do ...` 在 `/home/heyx/Hypo-Agent` 下发起受控修复
- 修复完成后自动生成结构化报告并回推到原会话
- 满足条件时自动触发有限自重启
- 尽量复用既有 Codex session / working directory / 诊断上下文

## 2026-04-22 Backend Update

原始设计把 repair 执行后端建立在 Hypo-Coder 之上。该方案在真实验证中暴露出：
- PTY 套壳脆弱
- 终态回灌依赖 webhook，测试模式下容易断裂
- task/session 状态不可靠
- continuation 不可用

当前设计已更新为：

```text
SlashCommand -> RepairService -> CodexBridge -> Codex Python SDK -> codex app-server -> codex CLI
```

也就是说，repair 路径不再依赖 Hypo-Coder 的 HTTP / webhook / watcher 链路。

## Current Context

- 当前 `/repair` 在 [`src/hypo_agent/core/slash_commands.py`](/home/heyx/Hypo-Agent/src/hypo_agent/core/slash_commands.py) 内是单入口实现：
  - 无参数返回最近 24 小时诊断摘要
  - 带问题描述时拼一个 prompt 并直接调用 `coder_submit_task`
- 现有 `CoderTaskService`、`coder_tasks` 表、watcher 和 webhook 已能管理 Codex 任务生命周期，但它们只面向“任务”，而不是“repair run”。
- `graceful_restart()` 与 `/restart` 已经存在，带有限制冷却期的有限自重启能力。
- 当前 Hypo-Coder API 不支持 continuation，因此“复用 session”在 V1 中只能退化为：
  - 复用 working directory
  - 复用上一轮 repair 摘要
  - 创建新的 task

## Non-Goals

- V1 不支持同 repo 下并行 repair run
- V1 不做自动 rollback
- V1 不新增 repair 专用 WebUI
- V1 不修复 Genesis QWen 工具调用问题本身；只把它做成 repair 的已知样例与回归检测对象

## Approach

### 1. 引入一等 Repair Workflow

新增 `RepairService` 作为 repair 工作流编排层，职责包括：
- 生成诊断快照
- 创建 / 查询 / 重试 / 中止 repair run
- 构造 repair prompt
- 汇总 Codex 输出并写结构化报告
- 决定是否允许自动重启
- 回推 repair 结果到原会话

`/codex` 斜杠命令仍可继续走现有 Hypo-Coder 路径；repair 则改由内置 `CodexBridge` 执行。

### 2. 新增持久化 repair_runs / repair_run_events

用 SQLite 增加 repair 专属状态，避免把 workflow 状态塞进 `coder_tasks`：

- `repair_runs`
  - repair 的主记录
  - 绑定 `session_id`、`coder_task_id`、issue 文本、诊断快照、报告、验证状态、重启状态
- `repair_run_events`
  - 记录重要事件和输出片段
  - 支撑 `/repair logs`、历史审计、JSON 解析失败后的人工回看

`coder_tasks` 继续服务于 `/codex` 与 Hypo-Coder；`repair_runs` 是 repair workflow 的唯一事实来源。

### 3. Slash Commands 改成 repair 子命令家族

V1 命令面：

- `/repair help`
- `/repair report [session] [--hours N]`
- `/repair do <issue>`
- `/repair do --from <finding_id> [<override issue>]`
- `/repair do ... --verify "<cmd>"`，可重复
- `/repair status`
- `/repair logs [--run <id>] [-n N] [--follow]`
- `/repair abort [--run <id>]`
- `/repair retry [run-id]`

说明：
- `finding_id` 为 report 生成的稳定短编号，不直接暴露底层 log id
- V1 的 `finding_id` 采用“临时编号 + TTL 缓存”：
  - 默认 10 分钟有效
  - report 输出中显式提示会过期
- `status` 做轻量摘要
- `logs` 做详细输出

### 4. 单 repo 单 active repair run

V1 明确限制同一 repo 同时最多只能有一个 active repair run：
- `queued`
- `running`

如果已有 active run：
- 新的 `/repair do`
- 新的 `/repair retry`

都会被拒绝，并提示当前 `run_id` 与 `/repair status`。

这能避免在单一真实工作目录里引入脏状态并发。

### 5. 修复成功判定与自动重启

自动重启不是看 Codex task `completed` 就触发。

自动重启条件：
- `repair_runs.status = completed`
- `verification_state = passed`
- `report_json.needs_restart = true`

验证语义：
- 默认由 Codex 在修复流程内自行执行验证命令
- 若用户传入 `--verify "<cmd>"`，这些命令会动态注入 repair prompt，由 Codex 在收尾阶段执行
- 如果 Codex 完成了，但验证失败：
  - `repair_runs.status = completed`
  - `verification_state = failed`
  - 不自动重启
- 如果验证缺失、报告无法解析、结论含糊：
  - `repair_runs.status = needs_review`
  - `verification_state = unknown`

自动重启安全阀：
- 自动重启永不绕过冷却期
- 单个 repair run 最多自动重启一次
- 最近 30 分钟最多允许 2 次 repair 触发的自动重启
- 超出预算时标记 `restart_state = blocked_budget`
- 命中冷却时标记 `restart_state = blocked_cooldown`
- `/repair restart force` 后续可绕过冷却，但不绕过预算上限

### 6. 执行后端：CodexBridge

新增 `src/hypo_agent/channels/codex_bridge.py`，职责：
- 启动和关闭 `AsyncCodex`
- 创建 thread 并异步执行 turn
- 在进程内通过 callback 回传 `completed / failed / aborted`
- 使用 `thread_resume(...)` 提供原生 continuation
- 在 Agent 重启后尝试恢复或判定丢失中的 repair run

`CodexBridge` 的启动失败不会再造成“底层 task 已失败但 repair run 仍 stuck in running”的情况；失败状态会直接以 callback 形式回传给 `RepairService`。

### 7. Session 复用策略

V1 明确三层复用逻辑：

1. 若当前 repair run 绑定的 Codex session 还能 continuation，则继续同一 session
2. 若不支持 continuation，则创建新 task，但复用：
   - 同一 repo
   - 上一轮 repair 摘要
   - 同一 issue / retry 上下文
3. 若不存在可复用上下文，则创建全新 task

由于当前 Hypo-Coder 不支持 continuation，V1 实际落在第 2 层。

脏状态处理：
- 启动 repair 前记录 `git status --porcelain`
- prompt 第一条固定要求 Codex 先检查 `git status`
- 不得回滚或覆盖与本次 repair 无关的用户改动
- 若工作区脏到无法安全继续，应输出 `needs_review`

### 8. `/repair report` 输出结构

`/repair report` 默认输出三段：

1. 当前状态
   - 是否有 active run
   - 当前 run/task/status/restart/verification

2. 错误摘要
   - 最近 N 条 findings
   - 按严重度与频率排序
   - 每条生成短 `finding_id`
   - 来源可以是：
     - error logs
     - failed tools
     - known pattern 命中

3. repair 历史
   - 最近若干 repair runs
   - 展示 issue、状态、耗时、验证状态、重启状态、摘要

另补一个 `已知模式` 分区，专门显示 pattern detector 命中。

### 9. Genesis QWen 作为内置 repair 样例

新增内置 known pattern：
- `genesis_qwen_tool_access_false_negative`

判定线索：
- 同一会话内，先出现成功的工具执行结果
- 随后 assistant 文本仍声称“无法访问 / 无法读取 / 没有权限 / access denied”之类语句
- 结合当前模型 provider / model name / session metadata 中的 Genesis/QWen 线索提高置信度

它会出现在三处：
- `/repair help` 示例
- `/repair report` 的 known pattern findings
- 回归测试，覆盖 detector 与 prompt 构建

## Data Model

### `repair_runs`

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `run_id TEXT UNIQUE NOT NULL`
- `session_id TEXT NOT NULL`
- `coder_task_id TEXT`
- `codex_thread_id TEXT`
- `retry_of_run_id TEXT`
- `issue_text TEXT NOT NULL`
- `finding_id TEXT`
- `working_directory TEXT NOT NULL`
- `status TEXT NOT NULL`
- `verification_state TEXT NOT NULL DEFAULT 'pending'`
- `restart_state TEXT NOT NULL DEFAULT 'not_requested'`
- `diagnostic_snapshot_json TEXT NOT NULL`
- `verify_commands_json TEXT NOT NULL DEFAULT '[]'`
- `git_status_before TEXT NOT NULL DEFAULT ''`
- `git_status_after TEXT NOT NULL DEFAULT ''`
- `report_markdown TEXT NOT NULL DEFAULT ''`
- `report_json TEXT NOT NULL DEFAULT '{}'`
- `last_error TEXT NOT NULL DEFAULT ''`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`
- `completed_at TEXT`

### `repair_run_events`

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `run_id TEXT NOT NULL`
- `event_type TEXT NOT NULL`
- `source TEXT NOT NULL`
- `summary TEXT NOT NULL DEFAULT ''`
- `payload_json TEXT NOT NULL DEFAULT '{}'`
- `created_at TEXT NOT NULL`

说明：
- 原始 Codex 输出无法结构化解析时，仍要把全文或摘要事件存进 `repair_run_events`
- 这样 `/repair logs` 能回看完整现场

## State Machine

### `repair_runs.status`

- `queued`
- `running`
- `completed`
- `needs_review`
- `failed`
- `aborted`

合法关键边：
- `queued -> running`
- `queued -> aborted`
- `running -> completed`
- `running -> needs_review`
- `running -> failed`
- `running -> aborted`

### `verification_state`

- `pending`
- `passed`
- `failed`
- `unknown`

### `restart_state`

- `not_requested`
- `requested`
- `executed`
- `blocked_cooldown`
- `blocked_budget`
- `skipped`
- `failed`

`skipped` 语义：
- 修复报告明确 `needs_restart = false`
- 因此不请求重启

## Prompt Contract

repair prompt 模板固定包含 6 段：

1. 任务角色
   - “You are the self-repair agent for Hypo-Agent.”
2. issue 描述
3. 诊断快照
4. 工作区安全规则
5. 验证要求
6. 最终输出格式

安全规则固定要求：
- 只能修改 `/home/heyx/Hypo-Agent`
- 先检查 `git status`
- 不得覆盖无关用户改动
- 工作区不安全时返回 `needs_review`

最终输出要求：
- 先给人类可读摘要
- 再给结构化 JSON 块

JSON 至少包含：
- `status`
- `root_cause`
- `changed_files`
- `verification`
- `needs_restart`
- `confidence`
- `followups`

## Delivery Rules

- repair 结果默认通过现有 proactive message 机制回推到发起 `/repair do` 的 `session_id`
- 若原会话无法主动推送：
  - 结果仍写入 `repair_runs`
  - 用户后续可通过 `/repair status` 或 `/repair report` 看到

## Restart Recovery

Agent 启动后，`RepairService` 会扫描状态为 `queued/running` 的 repair run：
- 若没有 `codex_thread_id`，直接标记 `failed + task.lost_on_restart`
- 若有 `codex_thread_id`，尝试通过 `CodexBridge` 恢复 thread
- 如果能从 thread 读到终态，则补写 completion
- 如果无法恢复，则标记 `failed + task.lost_on_restart`

## Testing Strategy

新增或扩展测试覆盖：

- `StructuredStore`
  - repair run CRUD / active run / retry linkage / events
- `RepairService`
  - report 生成
  - known pattern 检测
  - prompt 构建
  - retry
  - 完成态解析
  - 自动重启判定
- `SlashCommandHandler`
  - `/repair help|report|do|status|logs|abort|retry`
- `coder_webhook`
  - 终态回写 repair run
  - 主动回报
- `CoderStreamWatcher`
  - repair 运行中输出事件写入 store

## Implementation Notes

- `SlashCommandHandler` 只做参数解析和路由，不再自己拼 repair prompt
- `RepairService` 作为 repair 工作流核心编排器
- `gateway.app` 负责把 `repair_service`、`codex_bridge`、`restart_handler`、`on_proactive_message` 串起来
- repair 不再从 `coder_webhook` / `CoderStreamWatcher` 接收终态
