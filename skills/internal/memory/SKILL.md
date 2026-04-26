---
name: "memory"
description: "L2 structured memory：持久化并读取稳定的用户记忆、偏好与长期上下文。"
compatibility: "linux"
allowed-tools: "save_preference get_preference"
metadata:
  hypo.category: "internal"
  hypo.backend: "memory"
  hypo.exec_profile:
  hypo.triggers: ""
  hypo.risk: "low"
  hypo.dependencies: "structured_store"
---

# Memory 使用指南

## 定位 (Positioning)

`memory` 负责把稳定的用户记忆、偏好和长期上下文写入 `L2 structured memory`。它不是临时 session state 的回收站，而是面向长期复用的结构化记忆层。

## 适用场景 (Use When)

- 用户明确表达了可长期复用的偏好、习惯或环境信息。
- 后续决策可能依赖这些稳定信息，例如 `timezone`、`language`、`reply_style`。

## 工具与接口 (Tools)

- `save_preference`：保存稳定的结构化记忆条目。
- `get_preference`：读取已保存的结构化记忆条目，用于当前回复或执行决策。

## 标准流程 (Workflow)

1. 先判断该信息是不是“长期稳定、可复用、明确表达”的记忆条目。
2. 如果是，再用清晰、稳定的 key 调用 `save_preference`。
3. 在后续回复风格、时间处理或格式选择依赖历史偏好时，用 `get_preference` 查询。
4. 引用偏好时保持克制，不要把历史值当成永远正确的硬约束。
5. 写入成功后，要明确告诉用户写到了哪个数据库文件、它所在的文件夹是什么。

## 边界与风险 (Guardrails)

- 不要存当前回合的临时状态或一次性 task 信息。
- 不要存 `secret`、`credential`、`token` 或其他敏感个人数据。
- 不要根据猜测替用户“补全” preference。
- key 应保持短而稳定，例如 `language`、`timezone`、`reply_style`，避免一次性命名。
