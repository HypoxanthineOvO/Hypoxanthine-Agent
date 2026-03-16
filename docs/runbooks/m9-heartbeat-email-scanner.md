# M9 Heartbeat + Email Scanner Runbook

## 1. Scope

本 runbook 适用于 M9 交付后的运行与验收：

- HeartbeatService 定时巡检与主动推送
- EmailScannerSkill 定时扫描与主动推送
- proactive `message_tag` 链路验收（后端 -> WS -> 前端）

## 2. Runtime Config

编辑 `config/tasks.yaml`：

```yaml
heartbeat:
  enabled: true
  interval_minutes: 1   # smoke/调试建议 1；生产建议 15~30
email_scan:
  enabled: true
  interval_minutes: 1   # smoke/调试建议 1；生产按成本调高
```

编辑 `config/skills.yaml`：

```yaml
skills:
  email_scanner:
    enabled: true
```

邮箱账号配置写入 `config/secrets.yaml`：

```yaml
services:
  email:
    accounts:
      - name: main
        host: imap.example.com
        port: 993
        username: user@example.com
        password: xxx
        folder: INBOX
```

附件目录需在 `config/security.yaml` 白名单中启用读写：

- `./memory/email_attachments` with `read, write`

## 3. Verification Steps

1. 后端测试：`uv run pytest -q`
2. 前端测试：`cd web && npm run test`
3. 默认启动测试模式 Agent：`bash test_run.sh`
4. 默认运行测试模式 smoke：`HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke`

默认原则：

- 不要默认对部署中的 `8765` 实例直接跑 smoke
- 测试模式使用 `test/sandbox/`，避免污染生产记忆与数据库
- 测试模式下 QQ adapter 不注册，不会发送 QQ 消息

期望 smoke case：

- `send "你好"` PASS
- `send "/reminders"` PASS
- heartbeat push PASS（`message_tag="heartbeat"`）
- proactive `message_tag` 完整性 PASS
- email_scan push PASS（`message_tag="email_scan"`）

## 4. Troubleshooting

### heartbeat 长时间无推送

- 检查 `tasks.heartbeat.enabled/interval_minutes`
- 检查 `HeartbeatService` 是否被 app startup 注册 interval job
- 检查 `memory/hypo.db` 可访问性与 `list_overdue_pending_reminders` 查询结果

### email_scan 无推送

- 检查 `skills.email_scanner.enabled` 与 `tasks.email_scan.enabled`
- 检查 `config/secrets.yaml` 邮箱账号配置格式
- 检查 `config/email_rules.yaml` 是否为空；为空时先走 bootstrap 草稿确认流程
- 检查 `logs` 中是否有 IMAP 连接异常

### 前端未显示标签

- 检查 WS payload 中 `message_tag` 是否存在
- 检查前端 `Message` 类型与 `MessageBubble` 分支渲染
