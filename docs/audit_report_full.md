# Hypo-Agent 完整审计报告

> 审计时间：2025-07
> 覆盖范围：src/hypo_agent/ 全量源码 + tests/ 全量测试
> 测试结果：719 passed, 36 warnings, 0 failures（93.85s）

---

## 第零部分：架构概览

### 核心流程

```
HTTP/WS → gateway/app.py
         → core/pipeline.py  (ReAct 循环、工具调度、历史管理)
         → core/channel_dispatcher.py  (fan-out / relay)
         → channel sinks: WebUI WS, QQBot, WeChat, Feishu, Notion
```

### 分层结构

| 层 | 目录 | 职责 |
|---|---|---|
| 网关层 | `gateway/` | FastAPI app、WebSocket handler、Dashboard REST API、Settings |
| 核心层 | `core/` | Pipeline、Dispatcher、Renderers、Heartbeat、ModelRouter、Delivery |
| 渠道层 | `channels/` | 各平台收发适配器（QQ/WeChat/Feishu/Notion/Coder）|
| 技能层 | `skills/` | 工具实现（fs、tmux、code_run、notion、email、reminder 等）|
| 安全层 | `security/` | PermissionManager、CircuitBreaker、KillSwitch |
| 记忆层 | `memory/` | L1 session（JSONL）、L2 structured（SQLite）、L3 semantic（向量）|

---

## 第一部分：整体优势

**Delivery 抽象（`core/delivery.py`）**
`DeliveryResult` 工厂方法 `ok()`/`failed()` 有防御性归一化。`ensure_delivery_result()` 统一处理 `bool | None | DeliveryResult`，防止跨渠道的 truthy-check 碎片化。

**PermissionManager（`security/permission_manager.py`）**
最长前缀匹配白名单规则；blocked_paths 独立列表按特异性排序；路径在 `__init__` 时统一解析。

**Channel Dispatcher（`core/channel_dispatcher.py`）**
`ChannelSink` 类型别名明确接口契约。平台前缀标签集中在一个 dict 中，易维护。

**弃用卫生**
`qq_channel.py:29` 使用 `@deprecated(...)` 并给出明确迁移路径（`QQBotChannelService`）。

**依赖安全钉**
`config/deps_safety_overrides.txt` 明确钉住 `litellm<1.82.7` 并附说明（已知恶意发布风险）。

**测试全绿**
719 个测试 0 失败，覆盖主要模块，CI 可信。

---

## 第二部分：Skills 层逐个评估

---

### 模块：`skills/fs_skill.py`

- 代码质量：5/5
- 错误处理：5/5
- 测试覆盖：5/5
- 可维护性：5/5

**注册工具：** `read_file` / `write_file` / `list_directory` / `delete_file` / `move_file`

**LLM 友好度：4/5** — 参数描述清晰，但 path 的白名单约束未在 description 中提示。

**问题：**
- 🟢 所有 OSError、权限拒绝均返回带清晰信息的 `SkillOutput(status="error")`

**建议：** 在工具 description 中加"only paths within allowed whitelist are accessible"。

---

### 模块：`skills/tmux_skill.py`

- 代码质量：5/5
- 错误处理：5/5
- 测试覆盖：4/5
- 可维护性：4/5

**注册工具：** `run_command(command, session_name?, timeout?)`

**LLM 友好度：3/5** — 工具描述极简，未说明禁止的命令类别（interactive/streaming/dangerous），Agent 会尝试 `top`/`tail -f` 等被拒命令浪费 ReAct 轮次。

**问题：**
- 🟡 description 未暴露 timeout 默认值（30s）
- 🟡 `_scan_command` 做 token-level 路径扫描，非完整路径解析
- 🟢 `asyncio.TimeoutError` 捕获后 kill window 并返回 `status="timeout"`
- 🟢 危险命令（`shutdown`/`reboot`）在执行前拦截

**建议：** 在 description 中说明禁止命令类别和默认超时。

---

### 模块：`skills/code_run_skill.py`

- 代码质量：5/5
- 错误处理：4/5
- 测试覆盖：4/5
- 可维护性：5/5

**注册工具：** `run_code(code, language?, timeout?)`

**LLM 友好度：4/5** — `language` 有 enum 约束，描述合理，但未说明沙箱限制。

**问题：**
- 🟡 bwrap 失败后静默 fallback 到无沙箱模式，Agent 不感知
- 🟡 `language` 参数无 default 值说明
- 🟢 临时文件通过 `_safe_unlink` 在任何路径下清理

**建议：** bwrap fallback 时在 SkillOutput 中加注"running without sandbox"标记。

---

### 模块：`skills/reminder_skill.py`

- 代码质量：5/5
- 错误处理：5/5
- 测试覆盖：5/5
- 可维护性：5/5

**注册工具：** `create_reminder` / `list_reminders` / `delete_reminder` / `update_reminder` / `snooze_reminder`

**LLM 友好度：5/5** — 全项目最详细的工具描述。`schedule_value` 给出 ISO 8601 示例、禁止相对时间、cron CRON_TZ 前缀用法。

**问题：**
- 🟢 过去时间检测（30s 容差）返回含服务器当前时间的友好错误
- 🟢 `ZoneInfoNotFoundError` 有 fallback
- 🟡 `heartbeat_config` 注入 Pydantic 生成的 schema 可能含 `$defs` 引用，Agent 理解成本略高

**建议：** 无紧急问题。

---

### A. 异常处理一致性

**没有统一异常层次。** 各模块各自定义异常类（`NotionUnavailableError`、`CoderUnavailableError`），无公共基类，跨层捕获退化为 `except Exception`。

**`except Exception` 无日志的位置（需优先修复）：**

```python
# gateway/dashboard_api.py:179
except Exception:
    return {"error": "unknown"}  # 异常被吞，无任何日志

# gateway/dashboard_api.py:212
except Exception:
    ...  # 同上

# gateway/dashboard_api.py:281
except Exception:
    ...  # 同上
```

全项目 `except Exception` 共 123 处，集中在 `gateway/app.py`（15处）、`gateway/dashboard_api.py`（3处）、`gateway/ws.py`（1处）。边界层使用 `except Exception` 是合理的，但无日志的情况需修复。

**建议：** 所有 `except Exception` 至少加 `logger.warning` 或 `logger.exception`；为 Skill 层定义 `HypoSkillError` 基类。

---

### B. 代码重复

- 🟡 **分页游标循环**：`NotionClient.query_database` 和 `_list_block_children_recursive` 有几乎相同的 `while True: ... if not has_more: break` 模式，可提取为 `_paginate(operation)` helper
- 🟡 **`str(x or "").strip()` 模式**：在 `models.py`、`delivery.py`、`channel_dispatcher.py`、`coder_webhook.py` 中出现 40+ 次，是统一风格但未提取为 utility
- 🟡 **`isinstance(response, dict)` 守卫**：在 `NotionClient` 和 `CoderClient` 的每个方法返回时重复，可在 `_call_with_retry`/`_request` 层统一处理

---

### C. 异步代码质量

**总体良好。** 所有同步阻塞调用均通过 `asyncio.to_thread` 卸载：

| 位置 | 说明 |
|------|------|
| `log_inspector_skill.py:172/297/314` | 文件 I/O |
| `agent_search_skill.py:110/145` | 同步 HTTP |
| `qq_channel.py:197` | 图片下载 |
| `weixin_adapter.py:314` | 图片下载 |
| `feishu_channel.py:120/126/301` | Feishu SDK 同步方法 |

**问题：**
- 🟡 `CoderClient._request` 每次调用创建新 `httpx.AsyncClient`，连接池无复用
- 🟡 `feishu_channel.py:126` 中 `asyncio.to_thread(thread.join, 5.0)` 是将阻塞 join 卸载到线程池，是合理 workaround 但略显奇异
- 🟡 `websockets.legacy` 弃用警告在生产代码中尚未迁移（websockets 14+ 已有新 API）

---

### D. 测试覆盖率分布

**测试总数：719（全部通过）**

| 覆盖充分（估计 >80%） | 覆盖薄弱（估计 <50%） |
|---|---|
| `security/permission_manager` | `channels/coder/`（约 3 个测试）|
| `security/circuit_breaker` | `memory/semantic_memory`（无边界测试）|
| `skills/reminder_skill` | `core/model_router`（无 fallback 路径测试）|
| `skills/fs_skill` | `gateway/ws.py`（WebSocket 异常路径）|
| `channels/notion/notion_client` | `skills/probe_skill`（FileNotFoundError 路径）|
| `core/heartbeat` | `skills/info_skill`（section 枚举边界）|
| `core/pipeline`（主路径）| `core/pipeline`（压缩/广播抑制路径）|


---

## 第三部分：核心服务深入评估

---

### 模块：`core/pipeline.py`

- 代码质量：3/5
- 错误处理：4/5
- 测试覆盖：4/5
- 可维护性：2/5

**问题：**
- 🔴 1664 行、67 个方法，单一类承载 ReAct 循环、工具调度、消息构建、历史管理、压缩、广播抑制、heartbeat 检测等全部职责
- 🟡 `_is_heartbeat_request`、`_is_internal_heartbeat_message`、`_session_persistence_suppressed`、`_broadcast_suppressed`、`_tool_status_context_suppressed`、`_history_suppressed` 六个 bool flag 方法堆叠，控制流难以追踪
- 🟡 `_effective_max_react_rounds` 和 `_should_force_final_response` 耦合，修改 round 上限需同时理解两个方法
- 🟢 `asyncio.timeout(...)` 在 `run_once` 中有超时保护

**建议：** 拆分为 `ReactLoop`（核心循环）、`MessageBuilder`（历史/消息构建）、`BroadcastController`（广播/抑制逻辑）三个协作对象，Pipeline 作为组合层。

---

### 模块：`core/heartbeat.py`

- 代码质量：5/5
- 错误处理：5/5
- 测试覆盖：5/5
- 可维护性：5/5

**问题：**
- 🟢 `asyncio.Lock` 防止并发重入；`timeout_seconds` 有下限保护（`max(5, ...)`）
- 🟢 `register_event_source` / `unregister_event_source` API 清晰，callback 支持 sync/async（`inspect.iscoroutinefunction` 动态判断）
- 🟢 `SILENT_SENTINEL = "**SILENT**"` 明确文档化，测试有专项覆盖
- 🟡 `_event_sources` 类型为 `dict[str, Any]`，callback 签名未强制约束

**建议：** 将 callback 类型收窄为 `Callable[[], Awaitable[str | None] | str | None]`。

---

### 模块：`core/model_router.py`

- 代码质量：4/5
- 错误处理：4/5
- 测试覆盖：3/5
- 可维护性：4/5

**问题：**
- 🟡 `config/models.yaml` 加载时无 Pydantic schema 校验，格式错误在运行时才报错
- 🟡 fallback 触发条件（context window 超限 vs API 错误）在日志中无区分，排查困难
- 🟡 token 计数使用 tiktoken 估算，对 Claude/Gemini 模型精度较低
- 🟢 fallback 链存在，主模型不可用时有降级路径

**建议：** 加载 `models.yaml` 时做 Pydantic 校验；fallback 触发时在日志中区分错误类别。

---

### 模块：`memory/`（三层）

- 代码质量：4/5
- 错误处理：4/5
- 测试覆盖：3/5
- 可维护性：4/5

**问题：**
- 🟡 **L1 session（JSONL）**：`append` 直接 open/write，多进程并发写无保护（单用户场景可接受）
- 🟡 **L2 structured（SQLite）**：连接对象在模块级别持有，无明确 `close()` 路径，依赖 GC
- 🟡 **L3 semantic**：向量检索仅有 smoke test，无空库/全匹配/阈值边界测试
- 🟢 三层之间读写路径互相独立，任一层可独立替换

**建议：** SQLite 连接改为 `async with` 生命周期管理；semantic memory 补充边界测试。

---

### 模块：`channels/notion/`（NotionClient + BlockConverter）

- 代码质量：5/5
- 错误处理：5/5
- 测试覆盖：4/5
- 可维护性：4/5

**问题：**
- 🟢 `_call_with_retry` 覆盖 `RequestTimeoutError`、`APIResponseError`（429/503）、`HTTPResponseError`
- 🟢 分页游标循环有 `next_cursor` 类型校验，防止无限循环
- 🟡 `BlockConverter` 对未知 block 类型（`synced_block`、`template` 等）静默降级为纯文本，无 warning 日志
- 🟡 `api_timeout_seconds`（10s）和 SDK 内部 `timeout_ms`（30000ms）是两套超时，文档说明不清

**建议：** `BlockConverter` 对未知 block 类型加 `logger.debug`；统一超时文档说明。

---

### 模块：`channels/coder/`（CoderClient + webhook）

- 代码质量：5/5
- 错误处理：4/5
- 测试覆盖：3/5
- 可维护性：4/5

**问题：**
- 🟢 HMAC-SHA256 签名校验使用 `hmac.compare_digest`，无时序攻击风险
- 🟡 `CoderClient._request` 每次请求创建新 `httpx.AsyncClient`，连接池无复用
- 🟡 `coder_webhook.py` 中 `session_id="main"` 硬编码，不支持多会话
- 🟡 `list_tasks` 中多 key 探测（`tasks`/`items`/`results`/`data`），说明对端 API 格式不确定

**建议：** `CoderClient` 改为持有实例级 `httpx.AsyncClient`；`session_id` 从配置注入。

---

## Top 10 优先修复项

| 优先级 | 问题 | 影响 | 文件 |
|---|---|---|---|
| 🔴 P1 | `dashboard_api.py` 三处 `except Exception` 无日志，异常被静默吞掉 | 生产问题无法排查 | `gateway/dashboard_api.py:179,212,281` |
| 🔴 P2 | `pipeline.py` 1664 行单一类，维护风险极高 | 长期可维护性 | `core/pipeline.py` |
| 🟡 P3 | `scripts/agent_cli.py:589` 仍使用已弃用的 `QQChannelService`（NapCat），36 个测试警告的根源 | 技术债务 | `scripts/agent_cli.py` |
| 🟡 P4 | `CoderClient._request` 每次短连接，无连接池复用 | 性能 | `channels/coder/coder_client.py` |
| 🟡 P5 | `coder_webhook.py` 中 `session_id="main"` 硬编码 | 扩展性 | `channels/coder/coder_webhook.py` |
| 🟡 P6 | `code_run_skill.py` bwrap fallback 静默，Agent 不感知沙箱状态 | 安全透明度 | `skills/code_run_skill.py` |
| 🟡 P7 | `model_router.py` 加载 `models.yaml` 时无 schema 校验 | 配置错误早期发现 | `core/model_router.py` |
| 🟡 P8 | `BlockConverter` 对未知 Notion block 类型静默丢内容 | 数据完整性 | `channels/notion/block_converter.py` |
| 🟡 P9 | `websockets.legacy` 弃用警告未处理，websockets 下一个 major 版本将移除 | 依赖升级阻塞 | `channels/qq_channel.py` 及相关 |
| 🟢 P10 | `memory/structured_store.py` SQLite 连接无明确关闭路径 | 资源泄漏（低概率）| `memory/structured_store.py` |

---

*报告生成于 2025-07，基于 git branch `main`，commit `a32dadc`*
