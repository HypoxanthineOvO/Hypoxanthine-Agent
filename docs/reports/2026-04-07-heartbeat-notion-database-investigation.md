# Heartbeat 无法访问 Notion Database 排查报告

日期：2026-04-07

## 结论摘要

- 当前问题的直接根因不是 Notion API 整体不可用，也不是 `NotionSkill` 主体鉴权失效。
- 心跳链路读取 Notion 待办时依赖 `config/secrets.yaml -> services.notion.todo_database_id`。
- 当前仓库配置中该字段为空字符串，因此 heartbeat 每次都会在 `HeartbeatSnapshotSkill._get_notion_todo_snapshot()` 内被短路，稳定返回 `notion todo database is unavailable`。
- 用户怀疑 “Skill 有问题” 并非完全没有根据：`HeartbeatSnapshotSkill` 目前直接依赖 `NotionSkill` 的私有字段 `_todo_database_id` 和 `_client`，这是脆弱的跨-skill 隐式契约，虽然不是这次故障的主因，但确实是实现层面的隐患。

## 现象

- 2026-04-05 的多次 heartbeat 输出都包含：
  - `Notion 待办：当前不可用`
  - 错误：`notion todo database is unavailable`
- 对应样本可见：
  - `test/sandbox/memory/sessions/cli-heartbeat.jsonl`
  - `test/sandbox/memory/sessions/cli-heartbeat-richness.jsonl`
  - `test/sandbox/memory/sessions/main.jsonl`

## 调用链路

1. `HeartbeatService.run()` 构造 heartbeat prompt，并优先使用 snapshot provider。
2. `gateway/app.py` 在启用 `heartbeat_snapshot` 时注册 `HeartbeatSnapshotSkill`。
3. `HeartbeatSnapshotSkill.execute("get_heartbeat_snapshot", {})` 聚合四个 section。
4. Notion section 进入 `HeartbeatSnapshotSkill._get_notion_todo_snapshot()`。
5. 该方法直接读取：
   - `notion_skill._todo_database_id`
   - `notion_skill._client`
6. 若 `notion_skill` 不存在、client 不存在、或 `database_id` 为空，则直接返回：
   - `{"available": False, "error": "notion todo database is unavailable"}`

关键代码：

- `src/hypo_agent/gateway/app.py`
- `src/hypo_agent/core/heartbeat.py`
- `src/hypo_agent/skills/heartbeat_snapshot_skill.py`
- `src/hypo_agent/skills/notion_skill.py`

## 证据

### 1. 运行配置证据

`config/secrets.yaml` 当前配置：

- `services.notion.integration_secret` 已配置
- `services.notion.todo_database_id` 为空字符串

这意味着：

- 普通 Notion API 鉴权可能正常
- heartbeat 专用的 todo database 查询一定没有目标库 ID

### 2. 代码证据

`src/hypo_agent/skills/heartbeat_snapshot_skill.py` 中：

- 读取 `database_id = str(getattr(notion_skill, "_todo_database_id", "") or "").strip()`
- 若为空，直接返回 unavailable

这不是“查询失败后报错”，而是“进入查询前就被配置检查拦截”。

### 3. 联网验证证据

已直接验证 Notion API 可访问：

- `users.me()` 成功
- `search()` 成功
- Bot 名称返回 `Hypo-Agent`

说明：

- integration secret 当前有效
- Notion 连接与基础鉴权正常
- 故障集中在 heartbeat 所需的 todo database 定位配置，而不是整个 Notion service 不可用

## 根因判断

### 主根因

`services.notion.todo_database_id` 缺失，导致 heartbeat 的 Notion 待办快照固定失败。

### 次级隐患

`HeartbeatSnapshotSkill` 与 `NotionSkill` 的集成方式过于脆弱：

- 依赖私有字段 `_todo_database_id`
- 依赖私有字段 `_client`
- 没有显式接口契约
- 启动期没有对 “Notion 已启用但 heartbeat 所需 todo DB 未配置” 做明确告警

因此：

- 这次故障是配置触发
- 但代码结构确实放大了问题的可见性和排查成本

## 为什么会表现为“每次心跳都失败”

因为 heartbeat prompt 明确要求优先只调用一次 `get_heartbeat_snapshot`，而该聚合工具会固定包含 notion section。只要 `todo_database_id` 为空，每次 heartbeat 都会得到同一个 unavailable 结果。

这也是为什么问题是“稳定复现”，而不是偶发超时或权限波动。

## 修复建议

### P0：先修运行配置

在实际运行实例的 `config/secrets.yaml` 中补齐：

- `services.notion.todo_database_id: <真实 Notion Database ID>`

同时确认该数据库已经对当前 integration 执行过 Notion 侧的 `Add connection` 授权。

这是最快、最直接、最可能立即恢复 heartbeat Notion section 的修复。

### P1：补启动期显式告警

建议在应用启动注册技能时增加明确日志或状态告警：

- 若 `skills.notion.enabled: true` 且 `skills.heartbeat_snapshot.enabled: true`
- 但 `services.notion.todo_database_id` 为空
- 则输出结构化 warning，例如：
  - `notion_todo_snapshot.disabled`
  - reason: `services.notion.todo_database_id is empty`

这样可以避免“运行中每小时重复失败，启动时却没有任何前置提示”。

### P1：去掉跨-skill 私有字段依赖

建议把以下隐式契约改成显式接口：

- 方案 A：在 `NotionSkill` 增加公开方法，例如 `get_todo_snapshot_rows()` / `get_todo_database_id()`
- 方案 B：把 heartbeat 所需的 Notion 查询逻辑沉到单独 service，不再让 `HeartbeatSnapshotSkill` 直接摸 `NotionSkill` 私有成员

推荐方案 B，更清晰，也更便于单测。

### P2：补测试覆盖

当前测试覆盖了“正常有 todo DB”的路径，但缺少以下关键场景：

- `todo_database_id` 为空时，heartbeat snapshot 的行为测试
- 启动注册时对缺失 `todo_database_id` 的 warning 测试
- notion enabled + heartbeat enabled 但 todo DB 未配置时的集成测试

建议新增：

- `tests/skills/test_heartbeat_snapshot_skill.py`
- `tests/gateway/test_heartbeat_snapshot_registration.py`

### P2：优化错误文案

当前错误文案：

- `notion todo database is unavailable`

对运维排查不够直接。建议改成更可执行的文案，例如：

- `Notion todo database is unavailable: services.notion.todo_database_id is empty`

或中文化并附配置路径。

## 建议验证步骤

1. 在目标实例填写 `services.notion.todo_database_id`
2. 重启服务
3. 手动触发一次 heartbeat
4. 确认 Notion section 不再返回 `notion todo database is unavailable`
5. 若仍失败，再继续区分：
   - 404 / object not found：数据库 ID 错误
   - 401 / 403：integration secret 或页面授权问题
   - timeout / HTTPError：网络或 Notion API 波动

## 最终判断

本次问题以配置缺失为主，不是 Notion API 整体故障。

但从工程角度看，heartbeat snapshot 对 `NotionSkill` 私有字段的直接依赖属于真实设计缺陷，建议在补齐 `todo_database_id` 后继续做一次小范围重构，把这条隐式契约改成显式接口，并补上启动告警与测试。

## 二次排查更新：空卡片 / WebUI 无回复

日期：2026-04-07

### 新现象

- 用户在飞书中发送“查看一下今天的计划通待办事项”后，收到空卡片。
- 同一问句在 WebUI 中没有任何回复。
- 其他渠道也出现空 assistant 消息。

### 新证据

- `memory/sessions/main.jsonl` 中存在多条最近的空 assistant 消息。
- 对这些用户消息，`memory/hypo.db` 中没有对应的工具调用记录。
- 用真实默认依赖复现实例链路时：
  - 候选技能命中为 `info-portal`
  - `call_with_tools()` 返回 `{"text": "", "tool_calls": []}`
  - `stream()` 也没有返回任何 chunk

### 新根因

- 这不是 “Notion 数据为空”。
- 这也不是 “Notion tool 执行后返回空结果”。
- 实际根因是：
  - “查看一下今天的计划通待办事项” 被错误匹配到 `info-portal`
  - 当前模型在该上下文下返回了空文本且没有 tool call
  - pipeline 又把这个空 assistant 消息持久化并广播出去，最终表现为飞书空卡片、WebUI 无回复

### 已修复项

- 在 `ChatPipeline` 增加了 pre-LLM 快捷路径：
  - 对“计划通 / Notion 待办 / 今日计划通事项”这类明显查询，直接调用 `get_notion_todo_snapshot`
  - 不再依赖模型先决定是否调用工具
- 补强 `skills/hybrid/notion/SKILL.md` 触发词：
  - 新增 `计划通`、`待办`、`事项`、`任务`
  - 使这类问句的技能排序优先落到 Notion
- 保留之前已加的兜底：
  - 若模型在 tool call 之后仍返回空文本，则回退到 tool 的 `human_summary`

### 修复后验证

- 真实默认依赖下重新执行“查看一下今天的计划通待办事项”：
  - 不再返回空消息
  - 当前会直接回复 Notion 候选数据库确认文案：
    - `HYX 的计划通`
    - `a19e5a0d-fd23-441e-9d55-9c5fc4a6206c`
- 说明：
  - “空卡片 / 无回复”问题已修复
  - 运行实例当前仍未完成 L2 绑定确认，所以返回的是“请确认绑定”，而不是直接列出待办

### 剩余动作

- 让用户在对话中回复：
  - `确认绑定 HYX 的计划通`
- 绑定完成后，再次发送：
  - `查看一下今天的计划通待办事项`
- 预期将直接返回真实 Notion 待办摘要，而不是空消息或确认提示
