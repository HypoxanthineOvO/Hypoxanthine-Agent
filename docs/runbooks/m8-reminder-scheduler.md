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
