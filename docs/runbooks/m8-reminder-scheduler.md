# M8 Reminder & Scheduler Runbook

## Scope

- 定时提醒（once/cron）创建、更新、删除、snooze
- Heartbeat 检查（file/process/http/custom_command）
- 触发链路：Scheduler -> EventQueue -> Pipeline -> SessionMemory -> WebSocket

## Quick Checks

1. 确认 `config/skills.yaml` 中 `reminder.enabled: true`
2. 确认后端依赖含 `apscheduler>=3.10`
3. 启动服务后检查：
   - `SchedulerService.start()` 已执行
   - Pipeline `start_event_consumer()` 已执行

## Reminder Flow

1. `create_reminder(confirm=false)` 仅返回时间解析预览
2. 用户文本确认后 `create_reminder(confirm=true)`：
   - 写入 `reminders` 表（status=active）
   - 注册 APScheduler job
3. 触发后事件入队并写入主会话 `session_id=main`

## Heartbeat Flow

1. 先执行预检项（file_exists / process_running / http_status / custom_command）
2. 将预检结果交给 lightweight 模型判断
3. 判断为 `normal`：静默，仅日志
4. 判断为 `abnormal`：推送 `heartbeat_trigger` 事件

## `config/tasks.yaml` 新配置

`config/tasks.yaml` 是现在推荐的全局调度入口，和旧版 SQLite `reminders` 表里的 heartbeat reminder 不是一回事。

当前支持两个顶层任务：

- `heartbeat`: 全局巡检任务。它会检查数据库、scheduler、过期提醒、已注册事件源，再决定是否主动推送。
- `email_scan`: 定时邮箱扫描任务。它直接调用 `EmailScannerSkill`，不依赖旧 reminder heartbeat。

示例：

```yaml
heartbeat:
  enabled: false
  interval_minutes: 30
  prompt_template: |
    你是 Hypo-Agent 的心跳判定器。
    仅返回 JSON：
    {"should_push": true|false, "summary": "一句话概括"}

    内置检查：
    ${checks}

    过期提醒：
    ${overdue}

    事件源结果：
    ${sources}

email_scan:
  enabled: true
  interval_minutes: 30
```

### Heartbeat Prompt 可用占位符

- `${checks}`: 内置检查结果（db / scheduler / router 等）
- `${overdue}`: 过期提醒列表
- `${sources}`: 事件源结果（例如邮箱新邮件检查）
- `${HYPO_AGENT_ROOT}` / `${HYPO_SERVER_NAME}` / `${HYPO_USERNAME}` / `${HYPO_CONDA_ENV}`: 运行时环境变量占位符

如果 `prompt_template` 没写，系统会使用内置默认 Prompt。

## 旧配置迁移

旧版 `📧 定时邮件推送（Heartbeat）` reminder 属于历史方案。它的语义现在应该迁移到：

```yaml
email_scan:
  enabled: true
  interval_minutes: 30
```

迁移后应停用旧 reminder，避免新旧两套定时器重复执行。

## Troubleshooting

- 现象：提醒未触发
  - 检查 `reminders.status` 是否为 `active`
  - 检查 `schedule_value` 是否合法（cron 可使用 `CRON_TZ=<tz> <expr>`）
  - 检查 scheduler 是否已启动

- 现象：触发了但前端没看到
  - 检查 EventQueue 是否有消费
  - 检查 `memory/sessions/main.jsonl` 是否写入提醒消息
  - 检查 WebSocket 连接状态与 token

- 现象：Heartbeat 噪音过多
  - 调整 `heartbeat_config` 检查项与阈值
  - 检查 lightweight 判断 prompt 与返回结构
