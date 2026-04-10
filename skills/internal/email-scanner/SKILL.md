---
name: "email-scanner"
description: "多账号 IMAP 邮件扫描、分类、搜索与详情读取。适合 inbox refresh、mail search、detail inspect 与 proactive email workflows。"
compatibility: "linux"
allowed-tools: "scan_emails search_emails list_emails get_email_detail"
metadata:
  hypo.category: "internal"
  hypo.backend: "email_scanner"
  hypo.exec_profile:
  hypo.triggers: ""
  hypo.risk: "low"
  hypo.dependencies: "imap,structured_store,email_rules.yaml"
---

# Email Scanner 使用指南

## 定位 (Positioning)

`email-scanner` 负责 IMAP 邮箱的扫描、缓存、搜索、详情读取与基于 `rule engine` 的预分类。

## 适用场景 (Use When)

- 用户要查看最近邮件 overview。
- 用户给出关键词、发件人或时间窗口，需要做定向 `mail search`。
- 已锁定某封邮件，需要读取 detail。
- 只有在缓存明显过旧或用户明确要求刷新时，才做 `inbox refresh`。

## 工具与接口 (Tools)

- `list_emails`：列出最近邮件摘要。
- `search_emails`：按关键词、发件人线索或时间范围搜索邮件。
- `get_email_detail`：读取单封邮件的完整详情。
- `scan_emails`：刷新 inbox 状态、补全缓存并触发分类。

## 标准流程 (Workflow)

1. 默认先从 `list_emails` 或 `search_emails` 开始，而不是立刻全量刷新。
2. 只有在缓存不足以回答问题，或用户明确要求 fresh scan 时，再调用 `scan_emails`。
3. 锁定邮件后，用 `get_email_detail` 读取正文与关键 metadata。
4. 输出时以摘要和判断为主，不要直接倾倒大量原文。

## 边界与风险 (Guardrails)

- `scan_emails` 经常由 `scheduler` 或 `heartbeat` 主动触发，不要把它当成每个请求的默认第一步。
- `rule engine` 只是预分类信号，不代表邮件已经被完整理解。
- 邮件内容可能包含敏感信息与附件，展示 detail 时要控制暴露范围。
- 当用户只问狭窄问题时，避免过度抓取不相关邮件。
