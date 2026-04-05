# Codex Slash Command Runbook

## Overview

`/codex` 是零 token 的 slash-command 入口，直接复用 Hypo-Coder 后端，不经过 LLM。它适合把代码任务异步委托给远端 coder，并在当前会话中保留任务绑定、状态查询和 attach/detach 控制。

## Commands

- `/codex <prompt> --dir /path`
- `/codex send <追加指令>`
- `/codex status <task_id|last>`
- `/codex list [status]`
- `/codex abort <task_id|last>`
- `/codex attach <task_id>`
- `/codex detach`
- `/codex done`
- `/codex health`

## Working Directory Resolution

优先级固定为：

1. 显式 `--dir`
2. 当前 session 最近一条 `coder_tasks.working_directory`
3. `/home/heyx/Hypo-Agent`

## Session Binding Semantics

- 新提交的任务默认写入 `coder_tasks` 并 attach 到当前 session。
- `attach` 会把当前 session 其他任务全部解挂，只保留一个 attached task。
- `detach` 后不再推聊天消息，也不再给普通 assistant 回复追加状态栏，但任务状态仍继续写入 DB。
- `done` 只结束当前 session 绑定，不会对远端任务执行 `abort`。

## Realtime Behavior

当前 Hypo-Coder API 只有：

- `create_task`
- `get_task`
- `list_tasks`
- `abort_task`
- `health`

因此当前版本是降级运行：

- `supports_streaming()` = `False`
- `supports_continuation()` = `False`
- `/codex send` 返回明确错误：`Hypo-Coder API 暂不支持 session continuation。`
- `CoderStreamWatcher` 只做状态轮询，不伪造 stdout/stderr 流

## Webhook Routing

- webhook 到达后先按 `taskId` 查询 `coder_tasks`
- 找到 session 后更新本地状态
- 只有 `attached=true` 时才推送主动消息
- 不再硬编码推送到 `main`

## Status Bar

当 session 存在 attached Codex task 时，普通 assistant 回复末尾追加：

```text
─────────────────────────────────
🤖 Codex · task-abc123 | ⏳ RUNNING
   /codex send · /codex status · /codex abort · /codex detach
─────────────────────────────────
```

slash `/codex` 自身回复不追加，避免重复。

## Troubleshooting

- `/codex health` 失败：先检查 `config/secrets.yaml` 中 `services.hypo_coder` 配置是否完整。
- webhook 无推送：检查 `coder_tasks.attached` 是否为 `1`，以及 `task_id -> session_id` 是否已写入。
- `/codex status last` 报错：通常说明当前 session 尚无任务记录。
- `/codex send` 不可用：这是当前后端能力限制，不是前端或 slash bug。
