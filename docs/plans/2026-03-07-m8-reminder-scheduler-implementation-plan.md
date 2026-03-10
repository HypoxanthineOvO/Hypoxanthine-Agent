# M8 Reminder & Scheduler Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 完成 M8（定时提醒系统）端到端交付：支持自然语言提醒创建（含文本确认）、APScheduler 定时触发、Heartbeat 静默监控、提醒消息注入主会话并通过 WebSocket 主动推送。

**Architecture:** 采用“Scheduler -> EventQueue -> Pipeline 消费 -> SessionMemory + WS 广播”单通路。APScheduler 使用 MemoryJobStore，仅在启动时从 L2 `reminders` 表重建任务；提醒状态由 SQLite 状态机管理。ReminderSkill 负责工具层 CRUD/确认流，Heartbeat 在触发侧先代码预检再走 lightweight 模型综合判断。

**Tech Stack:** Python 3.12, FastAPI, asyncio, APScheduler (AsyncIOScheduler >=3.10), aiosqlite, LiteLLM/ModelRouter, Pydantic v2, structlog, pytest, Vue 3 + TypeScript + vitest.

---

## Skills and Constraints

- Execution skills to use: `@test-driven-development` `@verification-before-completion` `@systematic-debugging`
- 已确认设计决策必须遵守：
  - Queue 方案A（`reminder_trigger` / `heartbeat_trigger`）
  - 主会话归属（固定 `session_id="main"`）
  - APScheduler MemoryJobStore + L2 重建
  - 时间解析双阶段（`confirm=false` 预览，`confirm=true` 落库）
  - Heartbeat 预检 + lightweight 模型静默判断
- 兼容性约束：`Message.message_tag` 必须是 Optional 且默认 `None`。
- 依赖约束：仅新增 `apscheduler` 到 `pyproject.toml`（避免不必要新增依赖）。
- 提交约束：开发提交使用 `M8: <描述>`；里程碑文档提交单独使用 `M8[doc]: <说明>`。

---

## Phase Overview

1. M8.1 基础设施：模型/存储/队列/调度器/应用生命周期
2. M8.2 ReminderSkill：时间解析、文本确认流、5个工具能力
3. M8.3 触发链路：事件消费、Heartbeat、WS 主动推送、前端标记
4. 验证与文档：全链路回归 + 里程碑文档更新

---

### Task 1: 扩展 Pydantic 模型（Reminder + Heartbeat + message_tag）

**Files:**
- Modify: `src/hypo_agent/models.py`
- Test: `tests/test_models_serialization.py`

**Step 1: Write failing tests**

```python
def test_message_accepts_optional_message_tag():
    msg = Message(text="提醒", sender="assistant", session_id="main", message_tag="reminder")
    assert msg.message_tag == "reminder"


def test_reminder_models_validate_once_and_cron():
    payload = ReminderCreate(
        title="开会",
        schedule_type="once",
        schedule_value="2026-03-08T07:00:00+00:00",
    )
    assert payload.schedule_type == "once"
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_models_serialization.py::test_message_accepts_optional_message_tag tests/test_models_serialization.py::test_reminder_models_validate_once_and_cron -q`
Expected: FAIL（缺少字段/模型）

**Step 3: Write minimal implementation**
- 在 `Message` 增加 `message_tag: Literal["reminder", "heartbeat"] | None = None`
- 保持 `Literal`（而非裸 `str`）用于当前里程碑强校验；M9+ 扩展 tag 时再统一扩容枚举
- 新增：
  - `HeartbeatCheck`
  - `ReminderCreate`
  - `ReminderUpdate`
  - `Reminder`（包含 `id/status/next_run_at/heartbeat_config`）

**Step 4: Run tests to verify pass**
Run: `pytest tests/test_models_serialization.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/models.py tests/test_models_serialization.py
git commit -m "M8: add reminder and heartbeat models"
```

---

### Task 2: 为 StructuredStore 增加 reminders 表与 CRUD

**Files:**
- Modify: `src/hypo_agent/memory/structured_store.py`
- Test: `tests/memory/test_structured_store.py`

**Step 1: Write failing tests**

```python
async def test_structured_store_reminders_crud(tmp_path):
    store = StructuredStore(db_path=tmp_path / "hypo.db")
    await store.init()
    rid = await store.create_reminder(...)
    rows = await store.list_reminders(status="active")
    assert rows[0]["id"] == rid
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/memory/test_structured_store.py::test_structured_store_reminders_crud -q`
Expected: FAIL（方法/表不存在）

**Step 3: Write minimal implementation**
- `init()` 新增 `reminders` 表：
  - `id, title, description, schedule_type, schedule_value, channel, status, created_at, updated_at, next_run_at, heartbeat_config`
- 增加方法：
  - `create_reminder`
  - `get_reminder`
  - `list_reminders`
  - `update_reminder`
  - `delete_reminder`（soft delete -> `status=deleted`）
  - `mark_reminder_completed`
  - `set_reminder_next_run_at`
- 增加索引（status/next_run_at）

**Step 4: Run tests to verify pass**
Run: `pytest tests/memory/test_structured_store.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/memory/structured_store.py tests/memory/test_structured_store.py
git commit -m "M8: add reminders table and store methods"
```

---

### Task 3: 新建 EventQueue 封装与事件类型

**Files:**
- Create: `src/hypo_agent/core/event_queue.py`
- Test: `tests/core/test_event_queue.py`

**Step 1: Write failing tests**

```python
async def test_event_queue_fifo():
    q = EventQueue()
    await q.put({"event_type": "reminder_trigger", "reminder_id": "r1"})
    event = await q.get()
    assert event["event_type"] == "reminder_trigger"
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/core/test_event_queue.py -q`
Expected: FAIL（模块不存在）

**Step 3: Write minimal implementation**
- 定义 `SchedulerEventType = Literal["reminder_trigger", "heartbeat_trigger"]`
- `EventQueue` 包装 `asyncio.Queue[dict[str, Any]]`：`put/get/task_done/empty/qsize`

**Step 4: Run tests to verify pass**
Run: `pytest tests/core/test_event_queue.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/core/event_queue.py tests/core/test_event_queue.py
git commit -m "M8: add scheduler event queue"
```

---

### Task 4: 新建 SchedulerService（生命周期 + 启动重建）

**Files:**
- Create: `src/hypo_agent/core/scheduler.py`
- Test: `tests/core/test_scheduler.py`

**Step 1: Write failing tests**

```python
async def test_scheduler_start_rebuilds_active_reminders(...):
    service = SchedulerService(...)
    await service.start()
    assert service.is_running is True
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/core/test_scheduler.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- `SchedulerService` 封装 `AsyncIOScheduler`
- 生命周期：`start()` / `stop()`（幂等）
- 启动时加载 `status=active` 的提醒并注册 job
- 支持 `once`（DateTrigger）与 `cron`（CronTrigger）
- `cron` 注册时显式透传时区：`CronTrigger(..., timezone=timezone)`，不依赖默认 UTC
- 对外方法：`register_reminder_job` / `remove_reminder_job` / `reload_active_jobs`

**Step 4: Run tests to verify pass**
Run: `pytest tests/core/test_scheduler.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/core/scheduler.py tests/core/test_scheduler.py
git commit -m "M8: add scheduler service lifecycle"
```

---

### Task 5: AppDeps 与 Gateway 生命周期接入 EventQueue + Scheduler

**Files:**
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `pyproject.toml`
- Test: `tests/gateway/test_app_deps_permissions.py`
- Create: `tests/gateway/test_app_scheduler_lifecycle.py`

**Step 1: Write failing tests**
- 新增测试校验：
  - `AppDeps` 包含 `event_queue`、`scheduler`
  - FastAPI lifespan 启动调用 `scheduler.start()`，关闭调用 `scheduler.stop()`

**Step 2: Run test to verify it fails**
Run: `pytest tests/gateway/test_app_scheduler_lifecycle.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- `AppDeps` 新增：`event_queue`, `scheduler`
- `create_app` lifespan:
  - startup: `structured_store.init()` -> `pipeline.start_event_consumer()` -> `scheduler.start()`
  - shutdown: `scheduler.stop()` -> `pipeline.stop_event_consumer()`
- 为解决 Task 5/Task 11 时序差：
  - Task 5 先在 `ChatPipeline` 提供 no-op `start_event_consumer()` / `stop_event_consumer()` stub
  - Task 11 再替换为真实 Queue 消费循环实现
- `pyproject.toml` 加入 `apscheduler>=3.10,<4.0`

**Step 4: Run tests to verify pass**
Run: `pytest tests/gateway/test_app_deps_permissions.py tests/gateway/test_app_scheduler_lifecycle.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/gateway/app.py pyproject.toml tests/gateway/test_app_deps_permissions.py tests/gateway/test_app_scheduler_lifecycle.py
git commit -m "M8: wire scheduler and event queue into app lifecycle"
```

---

### Task 6: 创建 ReminderSkill 骨架并注册

**Files:**
- Create: `src/hypo_agent/skills/reminder_skill.py`
- Modify: `src/hypo_agent/skills/__init__.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `config/skills.yaml`
- Test: `tests/skills/test_reminder_skill.py`
- Test: `tests/gateway/test_app_deps_permissions.py`

**Step 1: Write failing tests**

```python
def test_reminder_skill_exposes_five_tools():
    skill = ReminderSkill(...)
    names = [t["function"]["name"] for t in skill.tools]
    assert names == [
        "create_reminder",
        "list_reminders",
        "delete_reminder",
        "update_reminder",
        "snooze_reminder",
    ]
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/skills/test_reminder_skill.py::test_reminder_skill_exposes_five_tools -q`
Expected: FAIL（类不存在）

**Step 3: Write minimal implementation**
- `ReminderSkill` 继承 `BaseSkill`
- 完成 5 个 tool schema（参数与设计一致）
- 在 `skills/__init__.py` 导出
- `config/skills.yaml` 增加 `reminder.enabled`
- `_register_enabled_skills()` 按开关注册

**Step 4: Run tests to verify pass**
Run: `pytest tests/skills/test_reminder_skill.py tests/gateway/test_app_deps_permissions.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/skills/reminder_skill.py src/hypo_agent/skills/__init__.py src/hypo_agent/gateway/app.py config/skills.yaml tests/skills/test_reminder_skill.py tests/gateway/test_app_deps_permissions.py
git commit -m "M8: add reminder skill scaffolding and registration"
```

---

### Task 7: 实现 create_reminder 的时间解析预览（confirm=false）

**Files:**
- Modify: `src/hypo_agent/skills/reminder_skill.py`
- Modify: `src/hypo_agent/core/model_router.py`
- Test: `tests/skills/test_reminder_skill.py`
- Test: `tests/core/test_model_router.py`

**Step 1: Write failing tests**
- ReminderSkill: `confirm=false` 时调用 lightweight 模型解析，并返回 `parsed_schedule` 预览结构
- ModelRouter: 新增 lightweight helper（如 `call_lightweight_json(...)`）可被 Heartbeat/Reminder 复用

**Step 2: Run test to verify it fails**
Run: `pytest tests/skills/test_reminder_skill.py::test_create_reminder_preview_uses_lightweight_parser tests/core/test_model_router.py::test_model_router_call_lightweight_json -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- 在 `ReminderSkill` 中：
  - 构造解析 prompt（要求输出 JSON：`schedule_type`, `schedule_value`, `human_readable`, `timezone`）
  - 解析失败时返回可解释错误
- 在 `ModelRouter` 中增加 lightweight 调用 helper（基于 `task_routing.lightweight`）

**Step 4: Run tests to verify pass**
Run: `pytest tests/skills/test_reminder_skill.py tests/core/test_model_router.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/skills/reminder_skill.py src/hypo_agent/core/model_router.py tests/skills/test_reminder_skill.py tests/core/test_model_router.py
git commit -m "M8: add lightweight time parsing for reminder preview"
```

---

### Task 8: 完成 ReminderSkill 的确认流 + CRUD + snooze

**Files:**
- Modify: `src/hypo_agent/skills/reminder_skill.py`
- Modify: `src/hypo_agent/memory/structured_store.py`
- Modify: `src/hypo_agent/core/scheduler.py`
- Test: `tests/skills/test_reminder_skill.py`

**Step 1: Write failing tests**
- `confirm=true` 时真正落库并注册 job
- `list/delete/update/snooze` 对 DB + Scheduler 双写一致
- `snooze`：取消旧 job，创建新的一次性提醒

**Step 2: Run test to verify it fails**
Run: `pytest tests/skills/test_reminder_skill.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- `create_reminder`:
  - `confirm=false` 返回预览，不写 DB
  - `confirm=true` 创建 reminder 行 + `SchedulerService.register_reminder_job`
- `list_reminders` 返回状态过滤结果
- `delete_reminder` 软删除 + remove job
- `update_reminder` 更新 DB + reschedule
- `snooze_reminder` 支持“10m/30m/2h”等 duration 文本，转换成一次性时间

**Step 4: Run tests to verify pass**
Run: `pytest tests/skills/test_reminder_skill.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/skills/reminder_skill.py src/hypo_agent/memory/structured_store.py src/hypo_agent/core/scheduler.py tests/skills/test_reminder_skill.py
git commit -m "M8: implement reminder confirmation flow and CRUD tools"
```

---

### Task 9: Scheduler 触发回调写入 EventQueue（提醒主链路）

**Files:**
- Modify: `src/hypo_agent/core/scheduler.py`
- Modify: `src/hypo_agent/core/event_queue.py`
- Test: `tests/core/test_scheduler.py`

**Step 1: Write failing tests**
- job 触发后会 enqueue `{"event_type":"reminder_trigger", ...}`
- once reminder 触发后状态变为 `completed`

**Step 2: Run test to verify it fails**
Run: `pytest tests/core/test_scheduler.py::test_scheduler_enqueues_reminder_event_on_trigger -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- job 回调读取 reminder
- 构建 queue event（含 `reminder_id/title/description/session_id=main/channel`）
- `once` 类型回调后 mark completed + remove job

**Step 4: Run tests to verify pass**
Run: `pytest tests/core/test_scheduler.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/core/scheduler.py src/hypo_agent/core/event_queue.py tests/core/test_scheduler.py
git commit -m "M8: enqueue reminder events from scheduler callbacks"
```

---

### Task 10: Heartbeat 预检 + lightweight 综合判断 + 静默

**Files:**
- Modify: `src/hypo_agent/core/scheduler.py`
- Modify: `src/hypo_agent/core/model_router.py`
- Test: `tests/core/test_scheduler.py`

**Step 1: Write failing tests**
- 预检类型覆盖：`file_exists/process_running/http_status/custom_command`
- 模型判断为“正常”时不入队，只记录日志
- 模型判断为“异常”时 enqueue `heartbeat_trigger`

**Step 2: Run test to verify it fails**
Run: `pytest tests/core/test_scheduler.py::test_heartbeat_normal_is_silent tests/core/test_scheduler.py::test_heartbeat_abnormal_enqueues_event -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- 实现 heartbeat 检查器（优先标准库 + `asyncio.create_subprocess_shell`）
- 组装检查结果摘要调用 lightweight 模型
- 判断结果 `normal/abnormal`，正常静默，异常入队
- `structlog` 记录完整 precheck + decision

**Step 4: Run tests to verify pass**
Run: `pytest tests/core/test_scheduler.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/core/scheduler.py src/hypo_agent/core/model_router.py tests/core/test_scheduler.py
git commit -m "M8: add heartbeat precheck and silent decision flow"
```

---

### Task 11: Pipeline 增加 EventQueue 消费循环并写入主会话

**Files:**
- Modify: `src/hypo_agent/core/pipeline.py`
- Modify: `src/hypo_agent/models.py`
- Test: `tests/core/test_pipeline.py`
- Create: `tests/core/test_pipeline_event_consumer.py`

**Step 1: Write failing tests**
- `start_event_consumer()` 从 queue 消费 `reminder_trigger/heartbeat_trigger`
- 生成 `Message(sender="assistant", session_id="main", message_tag=...)`
- 即使无 WS 连接，也会写入 `SessionMemory`

**Step 2: Run test to verify it fails**
Run: `pytest tests/core/test_pipeline_event_consumer.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- `ChatPipeline` 新增：
  - `event_queue` 注入
  - `start_event_consumer()` / `stop_event_consumer()`
  - `_consume_event_loop()`
  - `on_proactive_message` 回调（用于 WS 广播）
- 构造消息文本模板：
  - reminder: `🔔 提醒：...`
  - heartbeat: `🔔 Heartbeat 异常：...`

**Step 4: Run tests to verify pass**
Run: `pytest tests/core/test_pipeline.py tests/core/test_pipeline_event_consumer.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/core/pipeline.py src/hypo_agent/models.py tests/core/test_pipeline.py tests/core/test_pipeline_event_consumer.py
git commit -m "M8: add pipeline event consumer for proactive reminders"
```

---

### Task 12: WebSocket 支持主动推送提醒消息

**Files:**
- Modify: `src/hypo_agent/gateway/ws.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Test: `tests/gateway/test_ws_echo.py`
- Create: `tests/gateway/test_ws_push.py`

**Step 1: Write failing tests**
- 建立连接后，不发用户消息也可收到服务端主动推送
- 推送 payload 与 `Message.model_dump(mode="json")` 对齐

**Step 2: Run test to verify it fails**
Run: `pytest tests/gateway/test_ws_push.py -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- 在 `ws.py` 增加连接管理器（register/unregister/broadcast）
- `websocket_chat` 接入连接管理器
- `app.py` 将 pipeline `on_proactive_message` 绑定到 WS broadcast

**Step 4: Run tests to verify pass**
Run: `pytest tests/gateway/test_ws_echo.py tests/gateway/test_ws_push.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/gateway/ws.py src/hypo_agent/gateway/app.py tests/gateway/test_ws_echo.py tests/gateway/test_ws_push.py
git commit -m "M8: support proactive reminder push over websocket"
```

---

### Task 13: 前端支持 message_tag=reminder/heartbeat 轻标记

**Files:**
- Modify: `web/src/types/message.ts`
- Modify: `web/src/composables/useChatSocket.ts`
- Modify: `web/src/components/chat/MessageBubble.vue`
- Create: `web/src/components/chat/__tests__/MessageBubble.spec.ts`
- Modify: `web/src/composables/__tests__/useChatSocket.spec.ts`

**Step 1: Write failing tests**
- `useChatSocket` 接收服务端 message 时保留 `message_tag`
- `MessageBubble` 对 `message_tag="reminder"` 显示轻量标记（图标/浅色条）

**Step 2: Run test to verify it fails**
Run: `cd web && npm run test -- --run src/composables/__tests__/useChatSocket.spec.ts src/components/chat/__tests__/MessageBubble.spec.ts`
Expected: FAIL

**Step 3: Write minimal implementation**
- TS 类型扩展 `message_tag?: "reminder" | "heartbeat"`
- MessageBubble 增加 `data-message-tag` 与可视化标记
- 保持现有布局/暗色模式兼容

**Step 4: Run tests to verify pass**
Run: `cd web && npm run test -- --run src/composables/__tests__/useChatSocket.spec.ts src/components/chat/__tests__/MessageBubble.spec.ts`
Expected: PASS

**Step 5: Commit**
```bash
git add web/src/types/message.ts web/src/composables/useChatSocket.ts web/src/components/chat/MessageBubble.vue web/src/components/chat/__tests__/MessageBubble.spec.ts web/src/composables/__tests__/useChatSocket.spec.ts
git commit -m "M8: add frontend reminder message tag rendering"
```

---

### Task 14 (Optional): 增加 `/reminders` 斜杠指令

**Files:**
- Modify: `src/hypo_agent/core/slash_commands.py`
- Modify: `tests/core/test_slash_commands.py`

**Step 1: Write failing tests**
- `/reminders` 返回 active reminders 摘要

**Step 2: Run test to verify it fails**
Run: `pytest tests/core/test_slash_commands.py::test_slash_commands_reminders_lists_active_reminders -q`
Expected: FAIL

**Step 3: Write minimal implementation**
- 注册 `/reminders`
- 通过 `structured_store.list_reminders(status="active")` 生成文本

**Step 4: Run tests to verify pass**
Run: `pytest tests/core/test_slash_commands.py -q`
Expected: PASS

**Step 5: Commit**
```bash
git add src/hypo_agent/core/slash_commands.py tests/core/test_slash_commands.py
git commit -m "M8: add reminders slash command"
```

---

### Task 15: 全量验证与文档收尾

**Files:**
- Modify: `docs/architecture.md`（Scheduler/Session 说明与 M8 一致）
- Create/Modify: `docs/runbooks/m8-reminder-scheduler.md`（操作与排障）

**Step 1: Run targeted backend tests**
Run: `pytest tests/core/test_event_queue.py tests/core/test_scheduler.py tests/skills/test_reminder_skill.py tests/core/test_pipeline_event_consumer.py tests/gateway/test_ws_push.py -q`
Expected: PASS

**Step 2: Run full backend regression**
Run: `pytest -q`
Expected: PASS

**Step 3: Run frontend tests**
Run: `cd web && npm run test`
Expected: PASS

**Step 4: Manual smoke checks**
- 启动网关，创建 `confirm=false` 提醒，确认后触发并收到 WS 推送
- 断开 WS 时触发提醒，重连后在 `/api/sessions/main/messages` 可见历史
- Heartbeat 正常场景静默，异常场景推送

**Step 5: Commit docs separately**
```bash
git add docs/architecture.md docs/runbooks/m8-reminder-scheduler.md
git commit -m "M8[doc]: add reminder scheduler architecture and runbook"
```

---

## Final Verification Checklist

- `create_reminder(confirm=false)` 仅返回解析预览，不落库
- 文本确认后 `create_reminder(confirm=true)` 才落库并调度
- `SchedulerService` 重启后可从 `reminders` 表恢复 active jobs
- 触发事件进入 EventQueue 并由 Pipeline 消费
- 提醒消息写入 `session_id="main"` 且带 `message_tag`
- WS 在线时即时推送；WS 离线时仅持久化，重连后历史可见
- Heartbeat 正常静默、异常通知
- `/reminders`（若启用）可展示 active 列表
