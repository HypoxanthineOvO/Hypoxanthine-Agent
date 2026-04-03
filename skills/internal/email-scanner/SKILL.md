---
name: "email-scanner"
description: "Multi-account email scanning, classification, summarization, caching, and proactive push. IMAP-based with rule engine."
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

# Email Scanner 使用说明

这个 backend 负责一个或多个 IMAP account 的 inbox 扫描、缓存搜索、详情读取和基于规则的分类。

## 推荐流程

- 先用 `list_emails` 获取最近邮件 overview。
- 当用户给出关键词、发件人线索或时间范围时，用 `search_emails`。
- 当你已经锁定某封可能相关的邮件，并且需要完整内容时，用 `get_email_detail`。
- 把 `scan_emails` 视为广义刷新操作，而不是每个邮件问题的第一选择。

## 工具职责

- `scan_emails`：刷新 inbox 状态、分类新邮件并填充缓存。
- `search_emails`：按关键词搜索缓存邮件，以及回退加载的邮件。
- `list_emails`：列出最近邮件摘要。
- `get_email_detail`：读取指定邮件的完整详情。

## 与 Scheduler 的边界

- `scan_emails` 经常由 scheduler 或 heartbeat 流程触发。
- 如果 `list_emails` 或 `search_emails` 的缓存结果已经够用，模型不应滥用 `scan_emails`。
- 优先做定向查询；只有当缓存明显过旧，或用户明确要求 fresh scan 时，再刷新。

## Rule Engine 说明

- Email rules 可以对已知模式预分类，或跳过某些 LLM 分类。
- 这有助于优先处理重要邮件，并减少重复模型开销。
- 要把 rule engine 当成信号来源，而不是对邮件已被完全理解的保证。

## 安全规则

- 当用户只问一个很窄的问题时，避免过度抓取邮件。
- 应总结相关结果，而不是整段转储大量 inbox 内容。
- 展示邮件详情时，要谨慎处理敏感内容和附件。
