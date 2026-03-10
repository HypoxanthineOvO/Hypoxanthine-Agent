# M6: 斜杠指令系统 + OutputCompressor + Model Router 轻量完善 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在不改动前端的前提下，为 Hypo-Agent 增加零 token 的斜杠运维指令、工具输出智能压缩链路、以及配置驱动的 task routing 与调用延迟统计，确保可观测、可测试、可回归。

**Architecture:** 在 `ChatPipeline` 入口增加 `SlashCommandHandler` 前置拦截，命中 `/...` 指令时直接返回并跳过 LLM；在工具执行路径插入 `OutputCompressor`，仅对超长输出进行模型压缩并保留原文日志+缓存；在 `ModelRouter` 统一记录每次调用 latency 与 token usage 到 `StructuredStore`，并由 `/model status`/`/token*` 指令消费聚合数据。

**Tech Stack:** Python 3.12, FastAPI, LiteLLM, SQLite (aiosqlite), structlog, pytest

**Skill References:** `@superpowers:test-driven-development` `@superpowers:verification-before-completion`

---

### Task 1: RED - Model Router task_routing 与 latency 统计契约测试

**Files:**
- Modify: `tests/core/test_model_router.py`
- Modify: `tests/memory/test_structured_store.py`

**Step 1: 写失败测试（Router）**

新增测试覆盖：
- `get_model_for_task("chat")` 读取 `task_routing.chat`
- `get_model_for_task("unknown")` 回退 `default_model`
- `call_with_tools()` 成功时发出包含 `latency_ms` 的回调事件
- `stream()` 成功时同样发出 `latency_ms`

```python
assert router.get_model_for_task("chat") == "Gemini3Pro"
assert router.get_model_for_task("nonexistent") == "Gemini3Pro"
assert emitted[0]["latency_ms"] >= 0
```

**Step 2: 写失败测试（Store）**

新增测试覆盖：
- `token_usage` 记录/读取 `latency_ms`
- 按模型聚合 token（`input/output/total`）
- 按模型聚合延迟（`count/min/max/avg`）

**Step 3: 运行测试确认 RED**

Run: `pytest tests/core/test_model_router.py tests/memory/test_structured_store.py -v`
Expected: FAIL（缺少 `get_model_for_task`、`latency_ms` 字段/聚合查询）。

**Step 4: Commit RED**

```bash
git add tests/core/test_model_router.py tests/memory/test_structured_store.py
git commit -m "M6: add failing tests for router task routing and latency stats"
```

### Task 2: GREEN - 实现 Router task_routing + latency 持久化

**Files:**
- Modify: `src/hypo_agent/core/model_router.py`
- Modify: `src/hypo_agent/memory/structured_store.py`
- Modify: `src/hypo_agent/gateway/app.py`

**Step 1: 实现 `ModelRouter.get_model_for_task(task_type)`**

```python
def get_model_for_task(self, task_type: str) -> str:
    return self.config.task_routing.get(task_type, self.config.default_model)
```

**Step 2: 给 `call_with_tools` / `stream` 增加 latency 采集**

- 使用 `time.perf_counter()` 记录单次成功调用耗时（毫秒）。
- 在成功事件 payload 中新增 `latency_ms`。
- 保持 fallback 逻辑不变。

**Step 3: 扩展 StructuredStore 的 `token_usage`**

- 表结构新增 `latency_ms REAL`。
- `init()` 中增加向后兼容迁移（旧库无列时执行 `ALTER TABLE`）。
- `record_token_usage(..., latency_ms)` 写入新列。
- 新增聚合查询方法供 slash 指令复用：
  - `summarize_token_usage(session_id: str | None = None)`
  - `summarize_latency_by_model()`

**Step 4: 更新 app 的回调写入**

- 将 `_build_default_pipeline()` 中的回调升级为通用成功回调，写入 token + latency。

**Step 5: 运行测试确认 GREEN**

Run: `pytest tests/core/test_model_router.py tests/memory/test_structured_store.py -v`
Expected: PASS。

**Step 6: Commit GREEN**

```bash
git add src/hypo_agent/core/model_router.py src/hypo_agent/memory/structured_store.py src/hypo_agent/gateway/app.py
git commit -m "M6: implement task routing and latency metrics persistence"
```

### Task 3: RED - Slash Commands 行为与 Pipeline 前置拦截测试

**Files:**
- Create: `tests/core/test_slash_commands.py`
- Modify: `tests/core/test_pipeline.py`
- Modify: `tests/core/test_pipeline_tools.py`
- Modify: `tests/memory/test_session_memory.py`

**Step 1: 写失败测试（指令处理器）**

覆盖 8 条指令与未知指令：
- `/help`
- `/model status`
- `/token`
- `/token total`
- `/kill`（二次调用 toggle）
- `/clear`
- `/session list`
- `/skills`
- 未知 `/xxx` 提示
- 非 `/` 文本返回 `None`

**Step 2: 写失败测试（Pipeline 拦截）**

- `run_once()` 命中 slash 时不调用 `router.call`
- `stream_reply()` 命中 slash 时不调用 `router.stream/call_with_tools`
- slash 返回通过现有 websocket 事件格式输出（`assistant_chunk` + `assistant_done`）

**Step 3: 写失败测试（SessionMemory 清空）**

新增 `clear_session(session_id)` 测试：
- 清空内存 buffer
- 删除对应 `.jsonl`

**Step 4: 运行测试确认 RED**

Run: `pytest tests/core/test_slash_commands.py tests/core/test_pipeline.py tests/core/test_pipeline_tools.py tests/memory/test_session_memory.py -v`
Expected: FAIL（`slash_commands` 不存在，pipeline 未拦截，session 无 clear）。

**Step 5: Commit RED**

```bash
git add tests/core/test_slash_commands.py tests/core/test_pipeline.py tests/core/test_pipeline_tools.py tests/memory/test_session_memory.py
git commit -m "M6: add failing tests for slash commands and pipeline interception"
```

### Task 4: GREEN - 实现 SlashCommandHandler 与 Pipeline 集成

**Files:**
- Create: `src/hypo_agent/core/slash_commands.py`
- Modify: `src/hypo_agent/core/pipeline.py`
- Modify: `src/hypo_agent/memory/session.py`
- Modify: `src/hypo_agent/core/skill_manager.py`
- Modify: `src/hypo_agent/gateway/app.py`

**Step 1: 实现 `SlashCommandHandler.try_handle(message)`**

- 仅当 `message.text.strip().startswith("/")` 时处理。
- 未匹配返回统一提示（含 `/help` 指引）。
- 命中指令直接返回文本，不触发 LLM。

**Step 2: 完成 8 条指令的数据拼装**

- `/help`: 固定指令列表。
- `/model status`: 读取 `router.config` + `StructuredStore` 聚合数据。
- `/token`: 当前 session 汇总。
- `/token total`: 全局按模型汇总。
- `/kill`: `CircuitBreaker` toggle。
- `/clear`: 调用 `SessionMemory.clear_session()`。
- `/session list`: 复用 `SessionMemory.list_sessions()`。
- `/skills`: 已注册 skill + enabled/disabled + breaker 状态。

**Step 3: Pipeline 前置拦截**

- 在 `run_once()` 与 `stream_reply()` 开始处调用 `try_handle`。
- 命中则直接返回/产出事件并结束，保证零 token 消耗。

**Step 4: 提供必要只读接口**

- `SkillManager` 增加 skill 状态查询接口（避免 slash 访问私有字段）。

**Step 5: 运行测试确认 GREEN**

Run: `pytest tests/core/test_slash_commands.py tests/core/test_pipeline.py tests/core/test_pipeline_tools.py tests/memory/test_session_memory.py -v`
Expected: PASS。

**Step 6: Commit GREEN**

```bash
git add src/hypo_agent/core/slash_commands.py src/hypo_agent/core/pipeline.py src/hypo_agent/memory/session.py src/hypo_agent/core/skill_manager.py src/hypo_agent/gateway/app.py
git commit -m "M6: implement slash command system with pipeline pre-dispatch"
```

### Task 5: RED - OutputCompressor 算法与缓存测试

**Files:**
- Create: `tests/core/test_output_compressor.py`
- Modify: `tests/skills/test_tmux_skill.py`
- Modify: `tests/skills/test_code_run_skill.py`

**Step 1: 写失败测试（compress_if_needed）**

覆盖场景：
- `<=2500` 透传
- `2501~128K` 单次压缩
- `>128K` 分块压缩（~80K）+ 最多 3 轮迭代
- 标记格式严格匹配：
  `[📦 Output compressed from X → Y chars. Original saved to logs. Ask me for details.]`
- 原文缓存仅保留最近 10 条

**Step 2: 写失败测试（技能输出上限）**

- `TmuxSkill.max_output_chars` 默认 262144
- `CodeRunSkill.max_output_chars` 默认 262144

**Step 3: 运行测试确认 RED**

Run: `pytest tests/core/test_output_compressor.py tests/skills/test_tmux_skill.py tests/skills/test_code_run_skill.py -v`
Expected: FAIL（压缩器不存在，默认上限仍 8000）。

**Step 4: Commit RED**

```bash
git add tests/core/test_output_compressor.py tests/skills/test_tmux_skill.py tests/skills/test_code_run_skill.py
git commit -m "M6: add failing tests for output compression and output limits"
```

### Task 6: GREEN - 实现 OutputCompressor 与技能输出上限调整

**Files:**
- Create: `src/hypo_agent/core/output_compressor.py`
- Modify: `src/hypo_agent/skills/tmux_skill.py`
- Modify: `src/hypo_agent/skills/code_run_skill.py`

**Step 1: 实现 `OutputCompressor`**

核心接口：

```python
async def compress_if_needed(self, output: str, metadata: dict) -> tuple[str, bool]:
    ...
```

- 阈值：2500 字符。
- 轻量模型：`router.get_model_for_task("lightweight")`。
- 单段压缩：`<=128K`。
- 分段压缩：`~80K` chunks，独立压缩，合并后最多再迭代 3 轮。
- 压缩失败时降级为原文（不抛出到主流程）。

**Step 2: 原文保留机制**

- structlog 记录原文长度与摘要信息。
- 维护最近 10 条原文缓存（dict/ordered dict）。

**Step 3: 调整技能默认输出上限**

- `TmuxSkill.max_output_chars = 262144`
- `CodeRunSkill.max_output_chars = 262144`

**Step 4: 运行测试确认 GREEN**

Run: `pytest tests/core/test_output_compressor.py tests/skills/test_tmux_skill.py tests/skills/test_code_run_skill.py -v`
Expected: PASS。

**Step 5: Commit GREEN**

```bash
git add src/hypo_agent/core/output_compressor.py src/hypo_agent/skills/tmux_skill.py src/hypo_agent/skills/code_run_skill.py
git commit -m "M6: implement output compressor and raise skill output limits"
```

### Task 7: RED - Pipeline 中 OutputCompressor 插入点测试

**Files:**
- Modify: `tests/core/test_pipeline_tools.py`
- Modify: `tests/gateway/test_app_deps_permissions.py`

**Step 1: 写失败测试（工具输出压缩集成）**

- 工具结果超阈值时，pipeline 调用 `compress_if_needed`。
- `tool_call_result` 事件中返回压缩后内容（含标记）。
- 送回 ReAct 的 `tool` message 为压缩后内容，避免超长上下文。

**Step 2: 写失败测试（AppDeps 注入）**

- `AppDeps` 包含 `output_compressor` 字段。
- 默认 app 构建后 `app.state.output_compressor` 存在。

**Step 3: 运行测试确认 RED**

Run: `pytest tests/core/test_pipeline_tools.py tests/gateway/test_app_deps_permissions.py -v`
Expected: FAIL（pipeline 尚未插入压缩器，app 未注入）。

**Step 4: Commit RED**

```bash
git add tests/core/test_pipeline_tools.py tests/gateway/test_app_deps_permissions.py
git commit -m "M6: add failing tests for pipeline output compression integration"
```

### Task 8: GREEN - Pipeline/AppDeps 完成 OutputCompressor 接线

**Files:**
- Modify: `src/hypo_agent/core/pipeline.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/gateway/ws.py`

**Step 1: 扩展依赖注入**

- `AppDeps` 新增 `output_compressor` 字段。
- `_build_default_pipeline()` 构建并注入 `OutputCompressor`。
- `app.state.output_compressor = deps.output_compressor`。

**Step 2: 在 ReAct 工具结果处插入压缩**

- `SkillOutput -> serialize -> compress_if_needed -> emit tool_call_result + append tool message`
- 保持非超长输出路径不变。

**Step 3: 运行测试确认 GREEN**

Run: `pytest tests/core/test_pipeline_tools.py tests/gateway/test_app_deps_permissions.py -v`
Expected: PASS。

**Step 4: Commit GREEN**

```bash
git add src/hypo_agent/core/pipeline.py src/hypo_agent/gateway/app.py src/hypo_agent/gateway/ws.py
git commit -m "M6: wire output compressor into pipeline and app deps"
```

### Task 9: 综合回归、文档与里程碑收尾

**Files:**
- Modify/Create: `docs/architecture.md`（按需更新 M6 设计）
- Create: `docs/runbooks/m6-slash-commands-and-output-compressor.md`（建议）

**Step 1: 运行后端回归**

Run: `pytest -q`
Expected: 全部 PASS。

**Step 2: 输出关键验证证据**

- slash 指令测试通过。
- output compressor 阈值/分段/迭代测试通过。
- router latency + task routing 测试通过。

**Step 3: 文档单独提交（强制约定）**

```bash
git add docs/architecture.md docs/runbooks/m6-slash-commands-and-output-compressor.md docs/plans/2026-03-05-m6-slash-commands-output-compressor-model-router-implementation-plan.md
git commit -m "M6[doc]: document slash commands, output compression, and router metrics"
```

**Step 4: 代码提交建议（按功能分批）**

- `M6: implement task routing and latency metrics persistence`
- `M6: implement slash command system with pipeline pre-dispatch`
- `M6: implement output compressor and raise skill output limits`
- `M6: wire output compressor into pipeline and app deps`

---

## 风险与约束处理

1. **SQLite 迁移兼容性**：`token_usage` 新增 `latency_ms` 必须对旧库幂等迁移，避免启动失败。
2. **压缩失败降级**：`OutputCompressor` 失败时回落原文，不得中断工具链路。
3. **零 token 保证**：slash 命中时不得触发 `router.call/call_with_tools/stream`。
4. **消息体大小控制**：工具输出先压缩再回灌 ReAct，防止上下文膨胀。
5. **不改前端**：所有新能力通过现有 websocket 事件格式承载。
