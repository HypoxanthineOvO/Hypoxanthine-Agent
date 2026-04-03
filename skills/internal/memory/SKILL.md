---
name: "memory"
description: "L2 preference memory: persist and retrieve user preferences and long-term context."
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

# Memory 使用说明

这个 backend 把结构化用户偏好和长期上下文存入 L2 memory。它应该被审慎使用，而不是随手乱存。

## 工具选择

- `save_preference`：持久化稳定的用户偏好或长期细节。
- `get_preference`：当决策可能依赖用户先前偏好时，读取已保存的值。

## 什么时候保存

- 只有当用户明确表达了持久偏好、习惯、timezone、language choice、style preference 或类似稳定细节时才保存。
- 例子：preferred language、reply tone、timezone、formatting preference、重复性 workflow 习惯。
- 不要凭空发明或推断用户未明确表达的偏好。

## 什么时候读取

- 当回复风格或执行选择可能依赖用户过往指令时，读取偏好。
- 常见例子包括 tone、language、time handling 或 formatting preference。

## Key 命名

- 使用简短、稳定的 key，例如 `language`、`timezone`、`reply_style`，或类似清晰名称。
- key 应保持通用、可复用，不要做成一次性 task 专用名称。

## 不要存什么

- 当前回合的临时状态。
- secrets、credentials、tokens 或敏感个人数据。
- 只属于普通 session context、但并非长期偏好的事实。
- 对用户的猜测。
