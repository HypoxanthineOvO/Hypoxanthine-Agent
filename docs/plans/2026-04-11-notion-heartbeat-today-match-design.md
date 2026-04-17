# Notion Heartbeat Today-Match Design

## Background

当前 heartbeat 使用 Notion “计划通”数据库时有两个明显问题：

1. 事件源链路只依赖 Notion API 的“今天”日期过滤，遇到日期区间任务时会漏读，而且这条查询偶发失败。
2. 子任务在推送中只显示自身标题，缺少父任务上下文，导致提醒信息不可执行。

用户已经确认本次行为选择：

- 默认读取“今天到期 + 日期区间覆盖今天”的任务。
- 该匹配模式需要保留可配置能力。
- 子任务展示固定为 `父任务 / 子任务`。

## Goals

- 统一 heartbeat 主快照链路与 Notion 事件源链路的今日任务匹配逻辑。
- 为日期属性补齐 `start/end/span` 语义，支持“覆盖今天”判断。
- 为子任务补齐父任务标题，并产出统一展示标题。
- 保持三天内高优任务逻辑不再额外强调每日重复任务。

## Non-Goals

- 不改动 Notion 数据库结构。
- 不新增新的主动推送渠道或新的摘要 section。
- 不处理多级父任务递归，仅处理直接父任务。

## Options Considered

### Option A: 仅在 heartbeat_snapshot 层修补

- 优点：改动范围小。
- 缺点：`notion_heartbeat.py` 事件源仍会继续漏读日期区间，并继续显示裸子任务标题。

### Option B: 仅在 event source 层修补

- 优点：能解决主动推送读不到的问题。
- 缺点：主快照链路仍保留旧语义，两个入口继续不一致。

### Option C: 在 `NotionSkill` 中统一归一化与匹配逻辑

- 优点：同一处维护日期解析、父任务补全、展示标题与 today-match 规则；主快照和事件源都能复用。
- 缺点：需要同时调整 skill、heartbeat source、配置和测试。

推荐采用 Option C。

## Design

### 1. 统一 Todo 归一化

`NotionSkill` 的 todo 归一化结果补充以下字段：

- `date_start`
- `date_end`
- `is_date_span`
- `display_title`

规则：

- 单日任务：`date_start == date_end == 任务日期`
- 区间任务：`date_start = start`，`date_end = end`
- 若存在父任务标题：`display_title = 父任务 / 子任务`
- 若无父任务：`display_title = title`

### 2. 统一 today-match 规则

在 `NotionSkill` 中提供 today-match helper，支持两种模式：

- `due_only`
  向后兼容旧行为，按 `date_start == today` 判断。
- `cover_today`
  默认模式。单日任务按 `date_start == today`，区间任务按 `date_start <= today <= date_end`。

### 3. 配置面

在 `tasks.yaml` 的 `heartbeat` 配置中新增字段：

- `notion_today_match_mode: cover_today | due_only`

默认值为 `cover_today`。应用启动后将该值注入 `NotionSkill`，事件源与快照都读取同一配置。

### 4. 主快照链路

`HeartbeatSnapshotSkill` 不再自己发明“今天”语义，而是优先消费 `NotionSkill.get_todo_snapshot()` 返回的归一化结果。

分类规则：

- `pending_today`: 使用统一 today-match helper，且 `done == false`
- `completed_today`: 使用统一 today-match helper，且 `done == true`
- `high_priority_due_soon`: 继续按 3 天内高优未完成分类；每日重复任务继续排除

“今天”文案改为“今日相关”，避免日期区间任务被误写成“今日到期”。

### 5. 事件源链路

`NotionTodoHeartbeatSource.collect()` 改为：

- 查询数据库原始行时不再使用 Notion API 的“今天”日期过滤
- 统一拉取最近一批记录后本地归一化
- 使用同一个 today-match helper 本地筛选
- 推送标题使用 `display_title`

这样可以规避现有 Notion 日期过滤偶发失败，同时保证区间任务和子任务上下文都一致。

## Error Handling

- 查询失败时维持 `available: false` / `None` 的现有容错行为。
- 父任务标题获取失败时不阻塞主任务归一化，只退化为显示子任务标题。
- 配置缺失或非法时回退到默认 `cover_today`。

## Testing

新增或调整测试覆盖：

- `NotionSkill` 归一化区间任务，输出 `date_start/date_end/is_date_span/display_title`
- `NotionSkill` today-match helper 在 `cover_today` 与 `due_only` 下的差异
- `NotionTodoHeartbeatSource` 读取区间任务并输出父任务 / 子任务
- `HeartbeatSnapshotSkill` 将“今日相关”任务分类正确，并保持每日重复任务不进入 `high_priority_due_soon`
