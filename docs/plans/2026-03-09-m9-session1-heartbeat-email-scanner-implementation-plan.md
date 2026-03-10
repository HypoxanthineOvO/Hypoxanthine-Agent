# M9 Session 1 (Heartbeat + EmailScannerSkill) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 完成 M9.0 HeartbeatService 与 M9.1 EmailScannerSkill 端到端交付，支持定时心跳巡检、邮件扫描与主动推送（含 `message_tag`），且不破坏 M8 Reminder 链路。

**Architecture:** 在现有 M8 `Scheduler -> EventQueue -> Pipeline` 主动消息通路上，抽离独立 `HeartbeatService` 负责健康检查、漏网提醒兜底和可扩展事件源聚合；新增 `EmailScannerSkill` 负责多账户 IMAP 扫描、三层分类、摘要、附件与去重持久化。调度层统一通过 `tasks.yaml` 的 interval 配置注册 heartbeat/email_scan 周期任务，推送仍通过中心队列串行消费以避免并发干扰对话。

**Tech Stack:** Python 3.12, FastAPI, asyncio, APScheduler, aiosqlite, imaplib/email (stdlib), LiteLLM/ModelRouter, Pydantic v2, structlog, Vue 3 + TypeScript + vitest.

---

## Skills and Constraints

- Execution skills to use: `@test-driven-development` `@systematic-debugging` `@verification-before-completion`
- 验收铁律：改代码 → pytest -q → 重启 Agent → python scripts/agent_cli.py smoke → 通过才算完。pytest 绿但 smoke 失败 = 没修好。
- Keep scope: only M9.0 + M9.1 (no QQ/TUI/Notion/AgentSearch).
- Keep ReminderSkill behavior unchanged (read-only reference).
- Email protocol layer uses stdlib only (`imaplib`, `email`), no third-party IMAP client.
- Push path remains unified: `asyncio.Queue` -> `ChatPipeline` -> L1 + WS.
- Smoke 测试与开发调试时使用 `heartbeat.interval_minutes=1` 快速验收；生产默认可保持 `15` 或 `30`。
- Reality check from current codebase (must preserve):
  - Event queue payload is currently `dict` with `event_type` (not raw `Message`) and consumed by `ChatPipeline._event_to_message`.
  - WS proactive push already serializes full `Message` via `message.model_dump(mode="json")`; proactive tag propagation should be verified by tests, not by introducing protocol changes.
- Doc commit rule for milestone docs: `M9[doc]: <说明>`.

---

## Phase Overview

0. 先扩展 `agent_cli.py smoke` 验收用例，建立 M9 真机门禁
1. 配置与模型契约扩展（tasks/secrets/skills/message_tag/email rules schema）
2. HeartbeatService 抽离与调度接入（M9.0）
3. EmailScannerSkill 实现（M9.1）与 Heartbeat 事件源对接
4. 前端 message_tag 渲染与全量回归

---

### Task 0: 设计并扩展 agent_cli.py smoke 用例（M9 验收门禁先行）

**Files:**
- Modify: `scripts/agent_cli.py`
- Modify: `config/tasks.yaml`
- Optional: `config/tasks.local.yaml`（若仓库已有本地覆盖配置）

**Step 1: Write the failing smoke cases (with staged skip stubs)**
- 阅读 `scripts/agent_cli.py` 的 `smoke` 子命令，确认现有覆盖（创建提醒、`/reminders`、主动提醒推送、DB 状态）。
- 设计并加入 M9 smoke case（先写桩/skip，后续任务逐步变绿）：
  - 基础对话回归：`send "你好"`（已有能力回归确认，不得退化）
  - 提醒回归：`send "/reminders"`（已有能力回归确认，不得退化）
  - heartbeat 推送验证：`heartbeat.interval_minutes=1`，启动服务后 listen 等待，必须收到 `message_tag="heartbeat"`
  - proactive `message_tag` 字段完整性：验证主动 WS 消息 `message_tag` 存在且值正确
  - email_scan 事件触发：可 mock IMAP 或对真实邮箱场景标记 skip，但需验证 `scheduled_scan` 入队后 WS 推送链路

**Step 2: Run smoke to verify new cases are initially FAIL/SKIP as expected**
Run: `python scripts/agent_cli.py smoke`  
Expected: 基线回归 case 可运行；M9 新增 case 在功能未完成前为 FAIL/SKIP（有明确原因）。

**Step 3: Write minimal implementation for smoke harness extension**
- 将 smoke 重构为可枚举 case 的执行器，支持 `PASS/FAIL/SKIP` 明细输出。
- 增加 listen 等待逻辑以捕获主动 WS 推送并断言 `message_tag`。
- 增加短间隔调试入口：smoke/调试场景强制或覆盖 `heartbeat.interval_minutes=1`（仅验收用）。
- `email_scan` case 支持 mock trigger 或暂时 skip，占位后续 Task 12 前逐步转绿。

**Step 4: Run smoke to verify harness behavior**
Run: `python scripts/agent_cli.py smoke`  
Expected: 回归 case 通过；M9 新增 case 状态清晰可追踪（未实现功能可 skip，不得静默）。

**Step 5: Commit**

```bash
git add scripts/agent_cli.py config/tasks.yaml
git commit -m "M9: extend agent_cli smoke cases for heartbeat and email scan"
```

> 铁律：pytest -q 必须全绿才能进入下一个 Task。如有 FAIL，当场修复。

---

### Task 1: 扩展配置与模型契约（M9 配置面）

**Files:**
- Modify: `src/hypo_agent/models.py`
- Modify: `src/hypo_agent/core/config_loader.py`
- Modify: `config/tasks.yaml`
- Modify: `config/skills.yaml`
- Modify: `config/secrets.yaml.example`
- Test: `tests/test_models_serialization.py`
- Test: `tests/core/test_config_loader.py`

**Step 1: Write the failing tests**

```python
def test_message_accepts_email_scan_tag():
    msg = Message(sender="assistant", session_id="main", message_tag="email_scan")
    assert msg.message_tag == "email_scan"

def test_secrets_config_accepts_services_email_accounts():
    cfg = SecretsConfig.model_validate({...})
    assert cfg.services.email.accounts[0].name == "主邮箱"
```

**Step 2: Run tests to verify they fail**
Run: `pytest tests/test_models_serialization.py tests/core/test_config_loader.py -q`  
Expected: FAIL（缺少 services/email 结构、message_tag 枚举不足、tasks 配置模型缺失）。

**Step 3: Write minimal implementation**
- `Message.message_tag` 扩展 `Literal`，加入 `email_scan`。
- 新增配置模型：
  - `EmailAccountConfig`
  - `EmailServiceConfig`
  - `ServicesConfig`
  - `TasksConfig`（包含 `heartbeat.enabled/interval_minutes` 与 `email_scan.enabled/interval_minutes`）
- `SecretsConfig` 新增 `services: ServicesConfig | None = None`，保持向后兼容。
- `config_loader` 增加 tasks/secrets 的加载函数和校验入口。
- `tasks.yaml` 保持生产默认 heartbeat 间隔（15/30）；smoke/调试通过局部覆盖改为 1。

**Step 4: Run tests to verify pass**
Run: `pytest tests/test_models_serialization.py tests/core/test_config_loader.py -q`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/hypo_agent/models.py src/hypo_agent/core/config_loader.py config/tasks.yaml config/skills.yaml config/secrets.yaml.example tests/test_models_serialization.py tests/core/test_config_loader.py
git commit -m "M9: add heartbeat/email config models and loaders"
```

> 铁律：pytest -q 必须全绿才能进入下一个 Task。如有 FAIL，当场修复。

---

### Task 2: 沿用现有 EventQueue event_type 层并扩展 email_scan 映射

**Files:**
- Modify: `src/hypo_agent/core/event_queue.py`
- Modify: `src/hypo_agent/core/pipeline.py`
- Test: `tests/core/test_event_queue.py`
- Test: `tests/core/test_pipeline_event_consumer.py`

**Step 1: Write the failing tests**

```python
def test_event_queue_accepts_email_scan_event():
    ...

def test_pipeline_event_consumer_writes_email_scan_message():
    ...
    assert msg.message_tag == "email_scan"
```

**Step 2: Run tests to verify they fail**
Run: `pytest tests/core/test_event_queue.py tests/core/test_pipeline_event_consumer.py -q`  
Expected: FAIL（event_type 未覆盖 `email_scan_trigger`、pipeline 未生成 email 消息）。

**Step 3: Write minimal implementation**
- 不引入新抽象层，保持现有 event_type 流水线。
- `SchedulerEventType` 增加 `email_scan_trigger`（命名在全项目统一）。
- `ChatPipeline._event_to_message()` 新增 email 分支：
  - `message_tag="email_scan"`
  - 文本包含分类 emoji（🔴/⚪/📂）和摘要。
- Heartbeat 分支支持直接消费 `summary` 字段，减少模板硬编码。

**Step 4: Run tests to verify pass**
Run: `pytest tests/core/test_event_queue.py tests/core/test_pipeline_event_consumer.py -q`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/hypo_agent/core/event_queue.py src/hypo_agent/core/pipeline.py tests/core/test_event_queue.py tests/core/test_pipeline_event_consumer.py
git commit -m "M9: extend queue and pipeline for heartbeat and email scan events"
```

> 铁律：pytest -q 必须全绿才能进入下一个 Task。如有 FAIL，当场修复。

---

### Task 3: StructuredStore 增加邮件去重与漏网提醒查询能力

**Files:**
- Modify: `src/hypo_agent/memory/structured_store.py`
- Test: `tests/memory/test_structured_store.py`

**Step 1: Write the failing tests**

```python
async def test_structured_store_processed_emails_dedup(tmp_path):
    ...
    assert first_insert_ok and second_insert_skipped

async def test_structured_store_list_overdue_pending_reminders(tmp_path):
    ...
    assert len(rows) == 1
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/memory/test_structured_store.py -q`  
Expected: FAIL（`processed_emails` 表/方法不存在）。

**Step 3: Write minimal implementation**
- `init()` 创建 `processed_emails` 表与 `UNIQUE(account_name, message_id)`。
- 新增方法：
  - `insert_processed_email(...) -> bool`（冲突返回 False）
  - `has_processed_email(...) -> bool`
  - `list_overdue_pending_reminders(...)`（基于当前 reminders 实际 schema：`status='active'` 且 `next_run_at` 过期；如后续检测到 `pending/trigger_time` 字段则兼容分支处理）
- 保持 SQLite 向后兼容迁移逻辑。

**Step 4: Run tests to verify pass**
Run: `pytest tests/memory/test_structured_store.py -q`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/hypo_agent/memory/structured_store.py tests/memory/test_structured_store.py
git commit -m "M9: add processed emails persistence and overdue reminder queries"
```

> 铁律：pytest -q 必须全绿才能进入下一个 Task。如有 FAIL，当场修复。

---

### Task 4: 新建 HeartbeatService（M9.0 核心）

**Files:**
- Create: `src/hypo_agent/core/heartbeat.py`
- Test: `tests/core/test_heartbeat.py`

**Step 1: Write the failing tests**

```python
async def test_heartbeat_silent_when_no_events(...):
    ...

async def test_heartbeat_pushes_when_should_push_true(...):
    ...

def test_heartbeat_register_event_source_invokes_callbacks(...):
    ...
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/core/test_heartbeat.py -q`  
Expected: FAIL（模块不存在）。

**Step 3: Write minimal implementation**
- `HeartbeatService.__init__(structured_store, model_router, message_queue, scheduler, ...)`
- `register_event_source(name, callback)`
- `run()` 主流程：
  - 内建检查：SQLite 可访问、Scheduler 状态、router 可用性（不做额外 LLM ping）
  - 漏网提醒兜底：扫描过期未触发记录
  - 聚合外部事件源回调结果
  - 调 `model_router.call_lightweight_json()` 仅做一次“是否推送”判断 `{should_push, summary}`
  - `should_push=true` 入队 heartbeat 事件，`should_push=false` 记录 `heartbeat.silent`

**Step 4: Run tests to verify pass**
Run: `pytest tests/core/test_heartbeat.py -q`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/hypo_agent/core/heartbeat.py tests/core/test_heartbeat.py
git commit -m "M9: implement heartbeat service with event source aggregation"
```

> 铁律：pytest -q 必须全绿才能进入下一个 Task。如有 FAIL，当场修复。

---

### Task 5: SchedulerService 增加通用 Interval Job 注册能力

**Files:**
- Modify: `src/hypo_agent/core/scheduler.py`
- Test: `tests/core/test_scheduler.py`

**Step 1: Write the failing tests**

```python
async def test_scheduler_registers_interval_job_for_heartbeat():
    ...

async def test_scheduler_registers_interval_job_for_email_scan():
    ...
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/core/test_scheduler.py -q`  
Expected: FAIL（缺少 interval helper）。

**Step 3: Write minimal implementation**
- 新增 `register_interval_job(job_id, minutes, coro, replace_existing=True)`。
- 保持 reminder 现有 job 逻辑不变。
- 增加 shutdown 时 interval jobs 清理的幂等保障。

**Step 4: Run tests to verify pass**
Run: `pytest tests/core/test_scheduler.py -q`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/hypo_agent/core/scheduler.py tests/core/test_scheduler.py
git commit -m "M9: add interval scheduling helpers for heartbeat and email scan"
```

> 铁律：pytest -q 必须全绿才能进入下一个 Task。如有 FAIL，当场修复。

---

### Task 6: App 装配 HeartbeatService 并读取 tasks.yaml 注册心跳任务

**Files:**
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/gateway/config_api.py`
- Test: `tests/gateway/test_app_scheduler_lifecycle.py`
- Test: `tests/gateway/test_config_api.py`

**Step 1: Write the failing tests**

```python
def test_app_registers_heartbeat_job_from_tasks_config(...):
    ...

def test_config_api_validates_new_tasks_fields(...):
    ...
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/gateway/test_app_scheduler_lifecycle.py tests/gateway/test_config_api.py -q`  
Expected: FAIL（未读取 heartbeat/email_scan 配置）。

**Step 3: Write minimal implementation**
- 在 app startup 读取 `tasks.yaml`。
- `heartbeat.enabled=true` 时调用 scheduler interval 注册 `heartbeat_service.run`。
- `config_api` 的 `TasksConfigFile` 改为明确模型，校验 `interval_minutes>0`。
- 在 `app.state` 暴露 `heartbeat_service` 供后续组件引用。

**Step 4: Run tests to verify pass**
Run: `pytest tests/gateway/test_app_scheduler_lifecycle.py tests/gateway/test_config_api.py -q`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/hypo_agent/gateway/app.py src/hypo_agent/gateway/config_api.py tests/gateway/test_app_scheduler_lifecycle.py tests/gateway/test_config_api.py
git commit -m "M9: wire heartbeat service and tasks config into app lifecycle"
```

> 铁律：pytest -q 必须全绿才能进入下一个 Task。如有 FAIL，当场修复。

**Step 6: Smoke gate (Heartbeat required)**
- 重启 Agent（确保加载 `heartbeat.interval_minutes=1` 的 smoke/调试配置）。
- Run: `python scripts/agent_cli.py smoke`
- Expected:
  - 基础对话回归（`send "你好"`）通过
  - 提醒回归（`send "/reminders"`）通过
  - heartbeat 推送验证通过（收到 `message_tag="heartbeat"`）
  - proactive `message_tag` 字段完整性通过
  - email_scan 相关 case 可暂时 `SKIP`（待 Task 12 转绿）

---

### Task 7: 新建 EmailScannerSkill 骨架与规则文件解析（Layer 1）

**Files:**
- Create: `src/hypo_agent/skills/email_scanner_skill.py`
- Create: `config/email_rules.yaml`
- Modify: `src/hypo_agent/skills/__init__.py`
- Test: `tests/skills/test_email_scanner_skill.py`

**Step 1: Write the failing tests**

```python
def test_email_rule_first_match_wins():
    ...

def test_rule_skip_llm_true_skips_layer2_and_layer3():
    ...
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/skills/test_email_scanner_skill.py -q`  
Expected: FAIL（skill 与规则解析不存在）。

**Step 3: Write minimal implementation**
- `EmailScannerSkill(BaseSkill)` + tools:
  - `scan_emails`
  - `search_emails`
  - `get_email_detail`
- `email_rules.yaml` 结构及 Pydantic 校验。
- 实现 Layer 1 规则匹配（`from` 子串、`subject_contains` 包含、首条命中即停）。

**Step 4: Run tests to verify pass**
Run: `pytest tests/skills/test_email_scanner_skill.py -q`  
Expected: PASS（规则相关用例通过）。

**Step 5: Commit**

```bash
git add src/hypo_agent/skills/email_scanner_skill.py src/hypo_agent/skills/__init__.py config/email_rules.yaml tests/skills/test_email_scanner_skill.py
git commit -m "M9: scaffold email scanner skill and rule matching layer"
```

> 铁律：pytest -q 必须全绿才能进入下一个 Task。如有 FAIL，当场修复。

---

### Task 8: 实现 IMAP 扫描主流程（多邮箱、去重、mark-as-read）+ scheduled_scan

**Files:**
- Modify: `src/hypo_agent/skills/email_scanner_skill.py`
- Modify: `src/hypo_agent/memory/structured_store.py`
- Test: `tests/skills/test_email_scanner_skill.py`

**Step 1: Write the failing tests**

```python
def test_scan_emails_iterates_accounts_and_isolates_failures(...):
    ...

def test_scan_emails_marks_seen_after_processing(...):
    ...

def test_scan_emails_deduplicates_with_processed_emails(...):
    ...

def test_scheduled_scan_enqueues_email_scan_event(...):
    ...
```

**Step 2: Run tests to verify it fails**
Run: `pytest tests/skills/test_email_scanner_skill.py -q`  
Expected: FAIL（IMAP 流程未实现）。

**Step 3: Write minimal implementation**
- 使用 `imaplib.IMAP4_SSL` + `email` 标准库解析。
- 扫描 `UNSEEN`，处理完成后 `+FLAGS (\\Seen)`。
- 多账户遍历：单账户失败只记录错误并继续。
- 扫描前用 `processed_emails` 去重。
- 增加 `scheduled_scan()`（非 tool）：
  - 调用内部扫描主流程并聚合结果
  - 构造 `event_type="email_scan_trigger"` 事件，写入中心队列
  - 返回结构化统计用于日志/测试断言

**Step 4: Run tests to verify pass**
Run: `pytest tests/skills/test_email_scanner_skill.py -q`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/hypo_agent/skills/email_scanner_skill.py src/hypo_agent/memory/structured_store.py tests/skills/test_email_scanner_skill.py
git commit -m "M9: implement imap scanning with dedup, mark-as-read and multi-account isolation"
```

> 铁律：pytest -q 必须全绿才能进入下一个 Task。如有 FAIL，当场修复。

---

### Task 9: Layer 2/3 分类与摘要（轻量分类 + 强模型摘要）

**Files:**
- Modify: `src/hypo_agent/skills/email_scanner_skill.py`
- Test: `tests/skills/test_email_scanner_skill.py`

**Step 1: Write the failing tests**

```python
def test_layer2_calls_lightweight_json_for_unmatched_mail(...):
    ...

def test_layer3_calls_default_model_for_important_and_system(...):
    ...
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/skills/test_email_scanner_skill.py -q`  
Expected: FAIL（分类摘要链路未接入）。

**Step 3: Write minimal implementation**
- Layer 2：`call_lightweight_json()` 返回 `{category, confidence, reason}`。
- Layer 3：`router.call(chat_model, ...)` 生成中文摘要（仅 important/system）。
- `archive` 不推送，`low_priority` 可简述。

**Step 4: Run tests to verify pass**
Run: `pytest tests/skills/test_email_scanner_skill.py -q`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/hypo_agent/skills/email_scanner_skill.py tests/skills/test_email_scanner_skill.py
git commit -m "M9: add llm classification and summary layers for email scanner"
```

> 铁律：pytest -q 必须全绿才能进入下一个 Task。如有 FAIL，当场修复。

---

### Task 10: 附件下载路径与安全白名单

**Files:**
- Modify: `src/hypo_agent/skills/email_scanner_skill.py`
- Modify: `config/security.yaml`
- Test: `tests/skills/test_email_scanner_skill.py`

**Step 1: Write the failing tests**

```python
def test_email_attachments_saved_under_memory_email_attachments(...):
    ...
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/skills/test_email_scanner_skill.py -q`  
Expected: FAIL（附件落盘路径不存在或不一致）。

**Step 3: Write minimal implementation**
- 下载到 `memory/email_attachments/{account_name}/{date}/{filename}`。
- 自动创建目录，路径写入 `processed_emails.attachment_paths`。
- 更新 `security.yaml`：为 `./memory/email_attachments` 添加 `read, write`。

**Step 4: Run tests to verify pass**
Run: `pytest tests/skills/test_email_scanner_skill.py -q`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/hypo_agent/skills/email_scanner_skill.py config/security.yaml tests/skills/test_email_scanner_skill.py
git commit -m "M9: add attachment persistence and security whitelist"
```

> 铁律：pytest -q 必须全绿才能进入下一个 Task。如有 FAIL，当场修复。

---

### Task 11: Bootstrap 冷启动规则草稿流程

**Files:**
- Modify: `src/hypo_agent/skills/email_scanner_skill.py`
- Test: `tests/skills/test_email_scanner_skill.py`

**Step 1: Write the failing tests**

```python
def test_bootstrap_rules_returns_draft_without_writing_file(...):
    ...

def test_bootstrap_rules_confirm_writes_email_rules_yaml(...):
    ...
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/skills/test_email_scanner_skill.py -q`  
Expected: FAIL（bootstrap 不存在）。

**Step 3: Write minimal implementation**
- `bootstrap_rules()`：
  - 拉取最近 50~100 封邮件元数据
  - 调轻量模型生成规则草稿 YAML
  - 返回草稿并通过队列推送确认消息（默认不自动写文件）
- 增加确认写入机制（满足“用户确认后写入”）：
  - Skill 缓存 `draft_id -> yaml_content`
  - 在 `scan_emails` 增加可选参数：`bootstrap_confirm`、`draft_id`
  - `bootstrap_confirm=true` 且 `draft_id` 命中时，原子写入 `config/email_rules.yaml`
- 主动工具与定时扫描在“规则缺失”时触发 bootstrap。

**Step 4: Run tests to verify pass**
Run: `pytest tests/skills/test_email_scanner_skill.py -q`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/hypo_agent/skills/email_scanner_skill.py tests/skills/test_email_scanner_skill.py
git commit -m "M9: add email rule bootstrap draft workflow"
```

> 铁律：pytest -q 必须全绿才能进入下一个 Task。如有 FAIL，当场修复。

---

### Task 12: App/SkillManager 集成 EmailScannerSkill 与定时扫描任务

**Files:**
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/core/skill_manager.py`
- Modify: `src/hypo_agent/skills/__init__.py`
- Test: `tests/gateway/test_app_deps_permissions.py`
- Test: `tests/gateway/test_app_scheduler_lifecycle.py`
- Test: `tests/skills/test_skill_manager.py`

**Step 1: Write the failing tests**

```python
def test_build_default_deps_registers_email_scanner_when_enabled(...):
    ...

def test_app_registers_email_scan_interval_job(...):
    ...
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/gateway/test_app_deps_permissions.py tests/gateway/test_app_scheduler_lifecycle.py tests/skills/test_skill_manager.py -q`  
Expected: FAIL（skill 未注册、email_scan job 未挂载）。

**Step 3: Write minimal implementation**
- `skills.yaml` 支持 `email_scanner: true`。
- `_register_enabled_skills` 注册 EmailScannerSkill。
- app 启动读取 `tasks.email_scan` 并注册 interval 调用 `EmailScannerSkill.scheduled_scan()`。
- EmailScannerSkill 初始化时 `heartbeat_service.register_event_source("email", self._check_new_emails)`。

**Step 4: Run tests to verify pass**
Run: `pytest tests/gateway/test_app_deps_permissions.py tests/gateway/test_app_scheduler_lifecycle.py tests/skills/test_skill_manager.py -q`  
Expected: PASS.

**Step 5: Commit**

```bash
git add src/hypo_agent/gateway/app.py src/hypo_agent/core/skill_manager.py src/hypo_agent/skills/__init__.py tests/gateway/test_app_deps_permissions.py tests/gateway/test_app_scheduler_lifecycle.py tests/skills/test_skill_manager.py
git commit -m "M9: wire email scanner skill and scheduled scan integration"
```

> 铁律：pytest -q 必须全绿才能进入下一个 Task。如有 FAIL，当场修复。

**Step 6: Smoke gate (all M9 new cases required)**
- 重启 Agent（保持 `heartbeat.interval_minutes=1`，并启用 email_scan 定时任务）。
- Run: `python scripts/agent_cli.py smoke`
- Expected: M9 新增 smoke case 全部 PASS：
  - 基础对话回归（`send "你好"`）
  - 提醒回归（`send "/reminders"`）
  - heartbeat 推送验证（`message_tag="heartbeat"`）
  - proactive `message_tag` 字段完整性
  - email_scan 事件触发后 WS 推送到达

---

### Task 13: 前端 message_tag 渲染（💓 / 🔔）

**Files:**
- Modify: `web/src/types/message.ts`
- Modify: `web/src/components/chat/MessageBubble.vue`
- Test: `web/src/components/chat/__tests__/MessageBubble.spec.ts`
- Test: `web/src/composables/__tests__/useChatSocket.spec.ts`

**Step 1: Write the failing tests**

```ts
it("renders 💓 for heartbeat and 🔔 for reminder", () => { ... });
it("does not render tag for normal message", () => { ... });
```

**Step 2: Run test to verify it fails**
Run: `cd web && npm run test -- MessageBubble.spec.ts`  
Expected: FAIL（尚未覆盖 heartbeat 显示 💓 与默认无 tag 文案断言）。

**Step 3: Write minimal implementation**
- `message_tag` 类型扩展至 `"reminder" | "heartbeat" | "email_scan" | "tool_status"`（前端容错可用 `string` 联合）。
- `MessageBubble`：
  - reminder 显示 `🔔`
  - heartbeat 显示 `💓`
  - 其他默认不显示（email_scan 先不加强制徽标）
- 增加 WS 透传验证（测试层）：确保 proactive message 的 `message_tag` 从后端 payload 到前端状态不丢失。

**Step 4: Run tests to verify pass**
Run: `cd web && npm run test`  
Expected: PASS（含既有用例）。

**Step 5: Commit**

```bash
git add web/src/types/message.ts web/src/components/chat/MessageBubble.vue web/src/components/chat/__tests__/MessageBubble.spec.ts web/src/composables/__tests__/useChatSocket.spec.ts
git commit -m "M9: update chat message tag rendering for heartbeat and reminder"
```

> 铁律：pytest -q 必须全绿才能进入下一个 Task。如有 FAIL，当场修复。

---

### Task 14: 端到端回归与文档交接

**Files:**
- Modify: `docs/architecture.md`（M9.0/M9.1 实际落地后补充）
- Optional: `docs/runbooks/m9-heartbeat-email-scanner.md`
- Modify: `docs/plans/2026-03-09-m9-session1-heartbeat-email-scanner-implementation-plan.md`

**Step 1: Run backend full suite**
Run: `pytest -q`  
Expected: PASS（不低于 M8 基线并通过新增测试）。

**Step 2: Run frontend full suite**
Run: `cd web && npm run test`  
Expected: PASS（不低于 M8 基线并通过新增用例）。

**Step 3: Restart Agent and run smoke full gate**
- 重启 Agent（确保加载最终 `tasks.yaml`，并可在验收环境维持 `heartbeat.interval_minutes=1`）。
- Run: `python scripts/agent_cli.py smoke`
- Expected: ALL PASS（包含全部 M9 新增 case）。

**Step 4: Manual smoke checklist**
- 启动服务后验证 heartbeat interval 生效。
- 构造过期 reminder 验证 heartbeat fallback push。
- mock email scan 触发后验证 WebUI 收到 `message_tag=email_scan` 消息。
- 对话进行中触发定时事件，确认队列串行且不打断流式响应。

**Step 5: Commit docs separately**

```bash
git add docs/architecture.md docs/runbooks/m9-heartbeat-email-scanner.md docs/plans/2026-03-09-m9-session1-heartbeat-email-scanner-implementation-plan.md
git commit -m "M9[doc]: add heartbeat and email scanner architecture/runbook updates"
```

> 铁律：pytest -q 必须全绿才能进入下一个 Task。如有 FAIL，当场修复。

---

## Acceptance Checklist Mapping

- [ ] Heartbeat 按 `tasks.yaml` 间隔执行
- [ ] 无事件时只记录 `heartbeat.silent`
- [ ] 过期漏网提醒可触发兜底推送
- [ ] `register_event_source` 可聚合外部回调（含 email）
- [ ] Email 多账户扫描，单账户失败不阻断
- [ ] Layer 1 命中 `skip_llm=true` 时不调用 LLM
- [ ] Layer 2 分类 JSON 解析稳定
- [ ] Layer 3 摘要只用于 important/system
- [ ] 邮件去重与 mark-as-read 生效
- [ ] 附件落盘路径正确且可写
- [ ] Bootstrap 无规则文件时返回草稿并请求确认
- [ ] 定时 email_scan 通过 Queue 推送到主会话
- [ ] 前端正确渲染 💓/🔔
- [ ] proactive WS 消息中的 `message_tag` 前后端链路不丢失
- [ ] agent_cli smoke：heartbeat case 在 1 分钟间隔下可稳定通过
- [ ] agent_cli smoke：email_scan 触发与推送 case 可通过（mock/实连均有可执行路径）
- [ ] pytest + vitest + agent_cli smoke 全量通过
