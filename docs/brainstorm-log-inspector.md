# LogInspectorSkill Brainstorm

日期：2026-03-17

## 1. 调研范围与方法

本次调研覆盖了以下对象：

- 日志初始化与 handler：`src/hypo_agent/core/logging.py`、`src/hypo_agent/gateway/main.py`
- 本地启动/部署入口：`scripts/start.sh`、`scripts/start-dev.sh`、`deploy/hypo-agent.service`、`deploy/nginx/hypo-agent.conf`
- 持久化“可观测数据”：`src/hypo_agent/memory/session.py`、`src/hypo_agent/memory/structured_store.py`
- 对外读取入口：`src/hypo_agent/gateway/sessions_api.py`、`src/hypo_agent/gateway/dashboard_api.py`、`src/hypo_agent/gateway/compressed_api.py`
- 仓库中的实际运行产物：`logs/`、`memory/`、`test/sandbox/`

## 2. 现状扫描

### 2.1 实际日志/观测数据存放位置

| 类型 | 位置 | 文件名/模式 | 格式 | rotate |
| --- | --- | --- | --- | --- |
| 本地一键启动后端日志 | `logs/backend.log` | 固定文件名 | 混合：structlog JSON + uvicorn 纯文本 | 无 |
| 本地一键启动前端日志 | `logs/frontend.log` | 固定文件名 | 纯文本（Vite/npm stdout/stderr） | 无 |
| 开发热重载日志 | 终端 stdout/stderr | 无固定文件 | 纯文本 + uvicorn | 无 |
| 生产后端日志 | journald (`hypo-agent` service) | systemd unit 流 | 混合：structlog JSON + uvicorn 纯文本 | 由 journald 管理，仓库未配置 |
| Nginx 访问/错误日志 | 系统默认 Nginx 日志目录 | 依赖系统默认 | 纯文本 | 由系统/Nginx 管理，仓库未配置 |
| 会话历史 | `memory/sessions/*.jsonl` | `<session_id>.jsonl` | JSONL，每行一条 `Message` | 无 |
| 测试模式会话历史 | `test/sandbox/memory/sessions/*.jsonl` | `<session_id>.jsonl` | JSONL | 无 |
| 结构化工具/模型历史 | `memory/hypo.db` | SQLite 单文件 | SQL 表 | 无 |
| 测试模式结构化历史 | `test/sandbox/hypo.db` | SQLite 单文件 | SQL 表 | 无 |
| 临时压缩原文缓存 | 进程内内存 `OutputCompressor._recent_originals` | `cache_id` | Python 内存对象，经 `/api/compressed/{cache_id}` 暴露 | 进程重启即丢失 |
| 额外测试运行输出 | `test/sandbox/test_run.log` | 固定文件名 | 混合文本 | 非框架内建，疑似人工重定向产物 |

补充：

- 当前仓库中 `logs/backend.log` 与 `logs/frontend.log` 都存在，但内容为空。
- `memory/sessions/main.jsonl` 与 `memory/hypo.db` 中存在真实历史数据，可视为当前系统最稳定的“自查数据源”。
- `deploy/nginx/hypo-agent.conf` 没有显式 `access_log` / `error_log` 指令，因此生产 Nginx 日志路径只能继承系统默认配置。这一点是推断，不是仓库内显式契约。

### 2.2 logging 初始化与 handler

当前初始化链路：

- `src/hypo_agent/gateway/main.py` 启动时调用 `configure_logging()`
- `src/hypo_agent/core/logging.py` 中：
  - 调用 `logging.basicConfig(format="%(message)s", level=INFO)`
  - 调用 `structlog.configure(...)`
  - 默认 `json_logs=True`，即 structlog 输出 JSON
  - 测试模式仅追加 contextvar `mode="test"`

现状特点：

- 没有 file handler
- 没有 logging YAML 配置
- 没有按环境切换 sink/path 的配置层
- 没有 rotation 配置
- 没有统一接管 uvicorn access/error 日志

因此“日志写去哪里”并不是由 logging 模块决定，而是由启动方式决定：

- `scripts/start.sh` 用 shell 重定向把 stdout/stderr 追加到 `logs/backend.log`
- `scripts/start-dev.sh` 直接输出到当前终端
- `deploy/hypo-agent.service` 把 stdout/stderr 发到 journald

### 2.3 日志格式与级别

后端模块大多使用 `structlog.get_logger(...)`，常见字段包括：

- `event`
- `level`
- `logger`
- `timestamp`
- 部分业务字段：`session_id`、`tool_name`、`reminder_id`、`resolved_model` 等

但目前不是严格统一的：

- 大部分日志是结构化 event，例如 `skill.invoke.start`、`scheduler.job_registered`
- 少量日志是非结构化文本，甚至带 emoji，例如：
  - `💓 Heartbeat: 邮件技能未启用，跳过`
  - `⚠️ Heartbeat: 邮件连接失败，下次重试 - {exc}`
- uvicorn 自身日志仍是 `INFO:` / `ERROR:` 纯文本
- 第三方库告警也会直接混入，例如 `tzlocal` 的纯文本 warning

结论：同一份 sink 中已经出现 JSON 与纯文本混杂。

### 2.4 工具调用历史是否有持久化

有，而且是当前最适合做 “Agent 自查” 的结构化基础。

持久化位置：

- SQLite：`memory/hypo.db`
- 表：`tool_invocations`

字段（来自 `src/hypo_agent/memory/structured_store.py`）：

- `session_id`
- `tool_name`
- `skill_name`
- `params_json`
- `status`
- `result_summary`
- `duration_ms`
- `error_info`
- `compressed_meta_json`
- `created_at`

相关读取入口：

- `GET /api/sessions/{session_id}/tool-invocations`
- `GET /api/dashboard/recent-tasks`
- `GET /api/dashboard/latency-stats`（在没有 token_usage 时回退到 tool_invocations.duration_ms）

实测数据也表明该表已被真实使用，当前 `memory/hypo.db` 中有 `success/error/blocked/fused` 等状态记录。

### 2.5 其他可用于自查的持久化源

除了 `tool_invocations`，当前还有两类重要持久化源：

#### A. Session JSONL

位置：

- `memory/sessions/<session_id>.jsonl`
- `test/sandbox/memory/sessions/<session_id>.jsonl`

内容：

- 每行一个 `Message`
- 包含 `sender`、`text`、`message_tag`、`metadata`、`timestamp`、`channel`、`sender_id`

优点：

- 完整保留用户/助手消息时间线
- 适合做“最近发生了什么”的会话复盘

缺点：

- 没有日志级别
- 不是事件日志格式
- 不适合直接做错误聚合

#### B. token_usage

位置：

- `memory/hypo.db` 的 `token_usage` 表

内容：

- `requested_model`
- `resolved_model`
- `input_tokens`
- `output_tokens`
- `total_tokens`
- `latency_ms`
- `created_at`

优点：

- 对模型调用成本/延迟追踪很有价值

缺点：

- 没有 prompt/response 摘要
- 没有错误上下文
- 不能替代 runtime log

### 2.6 一个明显的现状问题：压缩提示与真实存储不一致

`OutputCompressor` 当前会向用户展示：

`Original saved to logs.`

但实际实现是：

- 原始输出只保存在进程内 LRU 缓存 `_recent_originals`
- 通过 `/api/compressed/{cache_id}` 拉取
- 进程重启后即丢失

这不是磁盘日志，也不是持久化存储。

对 LogInspectorSkill 来说，这意味着“查看压缩前原文”目前只能读取内存态缓存，无法做历史复盘。

## 3. 现状问题评估

### 3.1 日志体系偏散

当前至少存在 5 套来源：

- structlog stdout
- uvicorn stdout
- Vite/npm stdout
- journald
- SQLite / JSONL 持久化

这些来源没有统一索引，也没有统一 source registry。

### 3.2 格式不统一

目前混有：

- JSON 结构化日志
- 普通文本行
- shell 重定向文件
- journald 事件流
- SQLite 结构化表
- JSONL 会话记录

如果直接做 LogInspectorSkill，它会先变成“多源适配器集合”，复杂度高于真正的日志分析逻辑。

### 3.3 本地日志没有 rotation

仓库内未发现：

- `RotatingFileHandler`
- `TimedRotatingFileHandler`
- `logrotate` 配置
- `logs/` 清理策略

因此：

- `scripts/start.sh` 生成的 `logs/backend.log` / `logs/frontend.log` 会无限增长
- test/dev 环境没有 retention 策略

### 3.4 sink 与环境不统一

同一个系统在不同模式下的日志去向完全不同：

- `start.sh` -> 文件
- `start-dev.sh` -> 控制台
- systemd -> journald
- test mode -> 通常也是控制台或外部重定向

这对 Agent 自查很不友好，因为 skill 很难稳定知道“该读哪里”。

### 3.5 关键字段覆盖不稳定

好的一面：

- 很多事件已经携带 `session_id` / `tool_name` / `resolved_model`

问题：

- 并不是所有日志都带这些字段
- 没有统一 `trace_id` / `request_id` / `correlation_id`
- 用户消息、模型调用、工具调用之间难以稳定串联

### 3.6 “Agent 自查”已经有基础，但仍然缺口明显

当前能支撑的自查：

- 最近会话消息看 `memory/sessions/*.jsonl`
- 最近工具调用看 `tool_invocations`
- 最近模型用量看 `token_usage`
- 最近压缩原文看 `/api/compressed/{cache_id}`（仅限进程存活期）

当前缺失：

- 统一的 log source registry
- 稳定的 runtime file sink
- 可检索的错误/告警聚合视图
- 跨 source 的时间线拼接
- 按 session/tool/model 的统一过滤
- 历史压缩原文落盘

## 4. 建议的日志规范化方案

### 4.1 目标

先不要一口气做“全栈 observability 平台”。第一阶段目标应当是：

1. 让 Agent 知道可读的日志源在哪里
2. 让 backend runtime log 具备稳定路径与统一格式
3. 让会话、模型、工具三类结构化数据能按同一时间线关联

### 4.2 建议新增 `config/logging.yaml`

建议所有 logging 行为进入 YAML 配置，而不是写死在 `configure_logging()` 里。

建议字段：

```yaml
version: 1
runtime:
  level: INFO
  json_logs: true
  include_uvicorn_access: true
paths:
  root: ./logs
  backend_app: ./logs/backend/app.jsonl
  backend_access: ./logs/backend/access.log
  frontend_dev: ./logs/frontend/dev.log
  compressed_cache: ./logs/backend/compressed/
rotation:
  policy: size
  max_bytes: 52428800
  backup_count: 7
retention:
  days: 14
```

测试模式建议自动重写到：

```yaml
paths:
  root: ./test/sandbox/logs
```

### 4.3 建议的统一路径规范

#### 开发 / 本地运行

- `logs/backend/app.jsonl`
- `logs/backend/access.log`
- `logs/frontend/dev.log`
- `logs/backend/compressed/<cache_id>.txt` 或 `.json`

#### 测试模式

- `test/sandbox/logs/backend/app.jsonl`
- `test/sandbox/logs/backend/access.log`
- `test/sandbox/logs/frontend/dev.log`
- `test/sandbox/logs/backend/compressed/`

#### 生产模式

建议二选一：

- 方案 A：继续使用 journald 作为主 sink，但额外增加 file sink 供 skill 读取
- 方案 B：统一落到文件，并由 systemd/journald 做 stdout 兜底

我的建议是方案 A：

- 运维仍然保留 journald
- Agent 自查直接读固定文件，不依赖 `journalctl` 权限和输出解析

### 4.4 建议的统一事件字段

backend JSON log 至少统一以下字段：

- `timestamp`
- `level`
- `logger`
- `event`
- `session_id`
- `channel`
- `tool_name`
- `skill_name`
- `model_name`
- `request_id`
- `trace_id`
- `duration_ms`
- `status`
- `error`

其中：

- `request_id` 用于一次用户输入的全链路追踪
- `trace_id` 可与未来 HTTP/API 层对齐
- `session_id` 是 LogInspectorSkill 最重要的过滤维度

### 4.5 rotation 策略建议

建议：

- backend runtime log：按大小 rotate，`50MB * 7`
- frontend dev log：按大小 rotate，`20MB * 5`
- compressed 原文：按天清理，保留 3-7 天
- SQLite / JSONL 不走日志 rotation，但要有归档/清理策略

原因：

- runtime log 适合滚动切分
- SQLite / session JSONL 更像业务存储，不应混用 logrotate

## 5. LogInspectorSkill 设计方案

阶段 1 不建议直接做“任意 grep 的万能日志工具”，而是做面向 Agent 自查的高价值工具集。

### 5.1 工具 1：`list_log_sources`

作用：

- 告诉 Agent 当前有哪些可用观测源，以及是否存在

建议签名：

```python
list_log_sources() -> dict
```

建议返回：

```json
{
  "runtime_mode": "test",
  "sources": [
    {
      "name": "backend_app",
      "kind": "file",
      "path": "test/sandbox/logs/backend/app.jsonl",
      "exists": true
    },
    {
      "name": "tool_invocations",
      "kind": "sqlite",
      "path": "test/sandbox/hypo.db",
      "table": "tool_invocations",
      "exists": true
    }
  ]
}
```

### 5.2 工具 2：`tail_runtime_logs`

作用：

- 查看 backend runtime log 的近期事件

建议签名：

```python
tail_runtime_logs(
    source: str = "backend_app",
    lines: int = 200,
    level: str | None = None,
    contains: str | None = None,
    session_id: str | None = None,
    since_minutes: int | None = None,
) -> dict
```

建议返回：

- `source`
- `matched_count`
- `items`
- 每条 item 保留原始字段 + 解析后的标准字段

### 5.3 工具 3：`query_tool_invocations`

作用：

- 查询持久化工具调用历史

建议签名：

```python
query_tool_invocations(
    session_id: str | None = None,
    tool_name: str | None = None,
    status: str | None = None,
    since_hours: int = 24,
    limit: int = 100,
) -> dict
```

建议返回：

- `summary`
- `items`
- `status_counts`

这是当前最容易落地、价值也最高的工具。

### 5.4 工具 4：`inspect_session_timeline`

作用：

- 把 `session JSONL + tool_invocations + token_usage` 拼成一条时间线

建议签名：

```python
inspect_session_timeline(
    session_id: str,
    limit: int = 200,
    include_messages: bool = True,
    include_tools: bool = True,
    include_model_calls: bool = True,
) -> dict
```

建议返回：

- 时间线数组，统一字段：
  - `timestamp`
  - `kind` (`message` / `tool` / `model`)
  - `summary`
  - `payload`

这是“Agent 自查”真正需要的能力，因为单读 runtime log 不够回答“刚才为什么失败了”。

### 5.5 工具 5：`summarize_failures`

作用：

- 对最近一段时间的错误、超时、熔断做摘要

建议签名：

```python
summarize_failures(
    since_hours: int = 24,
    session_id: str | None = None,
    limit: int = 50,
) -> dict
```

建议返回：

- `top_errors`
- `top_failed_tools`
- `fused_tools`
- `latest_failures`

优先基于：

- `tool_invocations.status`
- runtime log 中的 `warning/error/exception`

## 6. 需要的前置重构

在实现 LogInspectorSkill 之前，建议先做以下前置工作。

### P0. 统一 runtime log 落点

必须做。

- 引入 `config/logging.yaml`
- 增加固定 file sink
- test mode 下自动落到 `test/sandbox/logs/`

否则 skill 连“默认读哪个文件”都不稳定。

### P0. 修正 OutputCompressor 提示语或实现

必须做。

二选一：

- 把提示语改成 `Original cached in memory`
- 或者真的把原文落盘到 `logs/backend/compressed/`

否则用户与后续 skill 都会被误导。

### P0. 统一 runtime log schema

必须做。

- 去掉 scheduler 中的 emoji / f-string 文本日志
- 全部改成结构化 event + fields
- 关键字段至少补齐 `session_id/tool_name/status/error`

### P1. 统一 uvicorn/access 日志策略

建议做。

当前 uvicorn 日志与 structlog 混在同一 sink 内，解析成本高。

建议：

- access log 单独 sink
- app log 单独 sink

### P1. 增加 source registry

建议做。

可以新增一个轻量配置，例如：

```yaml
sources:
  backend_app:
    kind: file
    path: ./logs/backend/app.jsonl
  tool_invocations:
    kind: sqlite_table
    path: ./memory/hypo.db
    table: tool_invocations
  session_memory:
    kind: jsonl_dir
    path: ./memory/sessions
```

这样 LogInspectorSkill 就不用把路径硬编码在代码里。

### P2. 增加 request/session 级 correlation id

建议做。

没有这层关联时：

- runtime log
- tool_invocations
- token_usage
- session message

只能靠时间近似拼接，准确性一般。

## 7. 预估工作量与依赖

### 7.1 前置重构

| 项目 | 预估 |
| --- | --- |
| 引入 `config/logging.yaml` 与 file sink | 0.5 - 1 天 |
| runtime/test 路径隔离与目录创建 | 0.5 天 |
| 统一结构化事件格式 | 0.5 - 1 天 |
| 修正 OutputCompressor 原文存储语义 | 0.5 天 |

### 7.2 LogInspectorSkill 本体

| 项目 | 预估 |
| --- | --- |
| `list_log_sources` + `query_tool_invocations` | 0.5 天 |
| `tail_runtime_logs` | 0.5 - 1 天 |
| `inspect_session_timeline` | 1 天 |
| `summarize_failures` | 0.5 - 1 天 |
| 单测 + smoke | 0.5 - 1 天 |

总计建议按 **3 - 5 天** 估算，而不是半天速成。

### 7.3 依赖

建议只用标准库优先：

- `logging`
- `json`
- `sqlite3`
- `pathlib`
- `collections`

可选依赖：

- 如需读 journald，可评估 `subprocess + journalctl --output json`
- 不建议一开始引入 ELK / OpenTelemetry / Loki 这类重型体系

## 8. 建议的实施顺序

1. 先整理 logging sink 与路径
2. 再统一 runtime log schema
3. 然后实现 `list_log_sources` / `query_tool_invocations`
4. 最后实现时间线拼接与失败摘要

原因：

- 没有统一 sink，LogInspectorSkill 会先被迫处理环境分支
- 先做结构化基础，后面 skill 实现会简单很多

## 9. 最终建议

`LogInspectorSkill` 是可行的，但不建议立刻在当前日志体系上硬做完整版本。

最现实的路线是：

- 先把 runtime log 收敛成“固定路径 + 固定格式 + 可 rotate”
- 先利用已经成熟的 `tool_invocations` / `token_usage` / `session JSONL`
- 第一版 skill 优先做“结构化自查”，不要一上来做“全文日志搜索引擎”

如果直接在当前状态开做，最大的风险不是功能写不出来，而是：

- source 太散
- 格式太杂
- 环境分叉太多
- 结果很难稳定回答用户“刚才到底发生了什么”
