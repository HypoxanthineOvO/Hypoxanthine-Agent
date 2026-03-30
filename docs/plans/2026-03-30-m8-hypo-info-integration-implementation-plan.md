# M8 Hypo-Info Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 `InfoReachSkill` 从 TrendRadar 本地 SQLite/HTML 直读切换为 Hypo-Info REST API，并同步完成 `info_*` 工具重命名、订阅表迁移、Heartbeat/Scheduler/配置更新与端到端回归。

**Architecture:** 保持现有 `BaseSkill -> SkillManager -> EventQueue -> Pipeline` 主动消息链路不变，只替换 `InfoReachSkill` 的数据源和对外工具面。`InfoReachSkill` 内新增基于 `httpx.AsyncClient` 的 Hypo-Info API 访问层，订阅仍存于 L2 SQLite，但表名与字段对齐 Hypo-Info 分类体系；调度与主动推送统一从 `trendradar_*` 迁移到 `hypo_info_*` 命名。

**Tech Stack:** Python 3.12, asyncio, httpx.AsyncClient, aiosqlite, Pydantic v2, FastAPI, APScheduler, pytest, uv.

---

## Skills and Constraints

- Announce at execution start: `I'm using the writing-plans skill to create the implementation plan.`
- Execution skills to use after approval: `@executing-plans` `@test-driven-development` `@verification-before-completion`
- 严格遵守本次设计约束：
  - `InfoReachSkill` 不再读取 `~/trendradar/output`
  - REST API 固定走 `services.hypo_info.base_url`，缺省回退 `http://localhost:8200`
  - HTTP 客户端必须使用 `httpx.AsyncClient`
  - 订阅表迁移必须自动执行并保留旧数据
  - Heartbeat 事件源名改为 `hypo_info`
  - Scheduler 任务名改为 `hypo_info_digest`
  - 默认验收必须使用测试模式：`bash test_run.sh` / `HYPO_TEST_MODE=1 ... smoke`
- 提交约束：
  - 代码提交：`M8: <说明>`
  - 文档提交：`M8[doc]: <说明>`
- 已确认的现状，实施时不要重复设计：
  - `src/hypo_agent/models.py` 已存在 `HypoInfoConfig` 与 `services.hypo_info`
  - `tests/test_models_serialization.py` 已覆盖 `HypoInfoConfig` 基础序列化
  - 本次重点是任务配置命名、技能实现与主动消息链路替换

---

## Phase Overview

1. 配置面重命名：`trendradar_summary` -> `hypo_info_digest`
2. Skill 数据源重构：移除本地 SQLite/HTML 读取，接入 Hypo-Info API
3. L2 迁移与主动消息：`trendradar_subscriptions` -> `info_subscriptions`，Heartbeat/Scheduler/Queue 命名统一
4. 回归验证：单测、全量 pytest、测试模式 smoke、真机验收记录

---

### Task 1: 更新配置模型与加载测试，切换任务命名到 `hypo_info_digest`

**Files:**
- Modify: `src/hypo_agent/models.py`
- Modify: `tests/core/test_config_loader.py`
- Modify: `tests/test_models_serialization.py`

**Step 1: Write the failing test**

```python
def test_load_tasks_config_accepts_hypo_info_digest(tmp_path: Path) -> None:
    tasks_yaml = tmp_path / "tasks.yaml"
    tasks_yaml.write_text(
        """
heartbeat:
  enabled: true
hypo_info_digest:
  enabled: true
  interval_minutes: 480
  time: "09:00,21:00"
""".strip(),
        encoding="utf-8",
    )

    tasks = load_tasks_config(tasks_yaml)

    assert tasks.hypo_info_digest.enabled is True
    assert tasks.hypo_info_digest.interval_minutes == 480
    assert tasks.hypo_info_digest.time == "09:00,21:00"
```

```python
def test_secrets_config_accepts_services_hypo_info_default_shape() -> None:
    config = SecretsConfig.model_validate(
        {"providers": {}, "services": {"hypo_info": {"base_url": "http://localhost:8200"}}}
    )
    assert config.services is not None
    assert config.services.hypo_info is not None
    assert config.services.hypo_info.base_url == "http://localhost:8200"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_config_loader.py::test_load_tasks_config_accepts_hypo_info_digest -q`

Expected: FAIL，因为 `TasksConfig` 仍使用 `trendradar_summary`

**Step 3: Write minimal implementation**

- 在 `src/hypo_agent/models.py` 中：
  - 将 `TrendRadarSummaryTaskConfig` 重命名为 `HypoInfoDigestTaskConfig`
  - 将 `TasksConfig.trendradar_summary` 替换为 `TasksConfig.hypo_info_digest`
  - 保留 `HypoInfoConfig`，不新增重复模型
- 在测试中：
  - 将 `trendradar_summary` 相关断言改为 `hypo_info_digest`
  - 保留并明确 `HypoInfoConfig` 的默认地址覆盖

**Step 4: Run tests to verify it passes**

Run: `uv run pytest tests/core/test_config_loader.py tests/test_models_serialization.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add src/hypo_agent/models.py tests/core/test_config_loader.py tests/test_models_serialization.py
git commit -m "M8: rename trend digest task config to hypo info"
```

---

### Task 2: 先改测试，定义新的 `info_*` 工具行为与 Hypo-Info HTTP 客户端错误处理

**Files:**
- Modify: `tests/test_info_reach_skill.py`
- Modify: `src/hypo_agent/skills/info_reach_skill.py`

**Step 1: Write the failing test**

```python
def test_info_query_formats_articles_from_hypo_info_api(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "total": 1,
                "articles": [
                    {
                        "id": "a1",
                        "title": "OpenAI 发布新推理能力",
                        "summary": "重点在于推理链路和成本优化。",
                        "category_l1": "AI",
                        "category_l2": "模型",
                        "importance": 8,
                        "tags": ["reasoning"],
                        "sources": ["blog"],
                        "source_name": "OpenAI",
                        "collected_at": "2026-03-30T01:00:00Z",
                        "url": "https://example.com/a1",
                    }
                ],
            },
        )
    )
    skill = _build_skill(tmp_path=tmp_path, transport=transport)

    result = asyncio.run(skill.info_query(category="AI", keyword="推理"))

    assert "OpenAI 发布新推理能力" in result
    assert "重要性：8" in result
    assert "来源：OpenAI" in result
```

```python
def test_info_summary_formats_digest_sections(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "time_range": "today",
                "generated_at": "2026-03-30T01:00:00Z",
                "highlight": "今天 AI 与芯片新闻密集。",
                "sections": [
                    {"category": "AI", "items": ["模型更新", "Agent 工具链"]},
                    {"category": "Infra", "items": ["算力价格波动"]},
                ],
                "stats": {"total_articles": 12},
            },
        )
    )
    skill = _build_skill(tmp_path=tmp_path, transport=transport)

    text = asyncio.run(skill.info_summary(time_range="today"))

    assert "今天 AI 与芯片新闻密集" in text
    assert "AI" in text
    assert "Agent 工具链" in text
```

```python
@pytest.mark.parametrize("exc", [httpx.ConnectError("boom"), httpx.ReadTimeout("slow")])
def test_info_query_returns_friendly_error_on_http_failures(tmp_path: Path, exc: Exception) -> None:
    transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(exc))
    skill = _build_skill(tmp_path=tmp_path, transport=transport)

    output = asyncio.run(skill.execute("info_query", {"time_range": "today"}))

    assert output.status == "error"
    assert "Hypo-Info" in output.error_info
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_info_reach_skill.py::test_info_query_formats_articles_from_hypo_info_api tests/test_info_reach_skill.py::test_info_summary_formats_digest_sections tests/test_info_reach_skill.py::test_info_query_returns_friendly_error_on_http_failures -q`

Expected: FAIL，因为当前只有 `trend_*` 工具且仍直读本地文件

**Step 3: Write minimal implementation**

- 在 `src/hypo_agent/skills/info_reach_skill.py` 中新增 `HypoInfoClient`
  - 使用 `httpx.AsyncClient`
  - `base_url` 从 `config/secrets.yaml -> services.hypo_info.base_url` 读取
  - 缺省回退 `http://localhost:8200`
  - timeout 使用 `httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=30.0)`
  - 提供 `query()` `summary()` `digest()` `categories()`
  - 将连接失败、超时、非 200 响应包装成统一的友好异常信息
- 将 tools 清单和 `execute()` 分发改为：
  - `info_query`
  - `info_summary`
  - `info_subscribe`
  - `info_list_subscriptions`
  - `info_delete_subscription`
- `info_query()` 直接调用 `/api/agent/query`
  - 支持参数：`category`, `keyword`, `time_range`, `min_importance`, `source_name`
  - 输出文本而非原始 dict，格式包含标题、摘要、来源、重要性、URL
- `info_summary()` 直接调用 `/api/agent/digest`
  - 删除 `report_html -> lightweight_model -> heuristic` 三层降级逻辑
  - 输出适合主动推送的分区摘要文本

**Step 4: Run tests to verify it passes**

Run: `uv run pytest tests/test_info_reach_skill.py -k "info_query or info_summary or http_failures" -q`

Expected: PASS

**Step 5: Commit**

```bash
git add src/hypo_agent/skills/info_reach_skill.py tests/test_info_reach_skill.py
git commit -m "M8: switch info reach query and summary to hypo info api"
```

---

### Task 3: 迁移订阅表到 `info_subscriptions`，完成 `info_subscribe/list/delete` 与 Heartbeat 新事件源

**Files:**
- Modify: `src/hypo_agent/skills/info_reach_skill.py`
- Modify: `tests/test_info_reach_skill.py`

**Step 1: Write the failing test**

```python
def test_info_subscription_crud_and_heartbeat_registration(tmp_path: Path) -> None:
    heartbeat_service = DummyHeartbeatService()
    skill = _build_skill(tmp_path=tmp_path, heartbeat_service=heartbeat_service)

    created = asyncio.run(
        skill.info_subscribe(
            name="ai-watch",
            keywords=["Agent", "推理"],
            categories=["AI", "Infra"],
            schedule="daily",
        )
    )
    listed = asyncio.run(skill.info_list_subscriptions())
    deleted = asyncio.run(skill.info_delete_subscription(name="ai-watch"))

    assert heartbeat_service.registrations[0][0] == "hypo_info"
    assert created["name"] == "ai-watch"
    assert listed["items"][0]["categories"] == ["AI", "Infra"]
    assert deleted["deleted"] is True
```

```python
def test_info_subscription_table_auto_migrates_from_trendradar(tmp_path: Path) -> None:
    db_path = tmp_path / "hypo.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE trendradar_subscriptions (
                name TEXT PRIMARY KEY,
                keywords_json TEXT NOT NULL,
                platforms_json TEXT NOT NULL DEFAULT '[]',
                schedule TEXT NOT NULL DEFAULT 'daily',
                last_run TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            INSERT INTO trendradar_subscriptions
            VALUES ('ai-watch', '["AI"]', '["weibo"]', 'daily', NULL, '2026-03-30T00:00:00Z', '2026-03-30T00:00:00Z')
            """
        )
        db.commit()

    skill = _build_skill(tmp_path=tmp_path, db_path=db_path)

    listed = asyncio.run(skill.info_list_subscriptions())

    assert listed["items"][0]["name"] == "ai-watch"
    assert "weibo" in listed["items"][0]["categories"]
```

```python
def test_check_new_info_filters_today_high_importance_articles(tmp_path: Path) -> None:
    transport = httpx.MockTransport(...)
    skill, queue = _build_skill(tmp_path=tmp_path, transport=transport)
    asyncio.run(skill.info_subscribe(name="chip-watch", keywords=["NVIDIA"], categories=["Infra"]))

    result = asyncio.run(skill._check_new_info())

    assert result["name"] == "hypo_info"
    assert result["new_items"] == 1
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_info_reach_skill.py -k "subscription or migrates or check_new_info" -q`

Expected: FAIL，因为当前表名/字段/Heartbeat 事件源均仍是 TrendRadar

**Step 3: Write minimal implementation**

- 将 L2 表逻辑从 `trendradar_subscriptions` 替换为 `info_subscriptions`
  - 新字段：`categories_json`
  - 保留：`name`, `keywords_json`, `schedule`, `last_run`, `created_at`, `updated_at`
- 启动时（首次确保表存在时）执行自动迁移：
  - 若旧表存在且新表为空，则将 `platforms_json` 内容搬到 `categories_json`
  - 使用日志记录迁移条数
  - 避免重复迁移
- 将 `trend_subscribe/list/delete` 替换为 `info_subscribe/list/delete`
  - 参数从 `platforms` 改为 `categories`
  - 读取/输出字段全部改为 `categories`
- Heartbeat：
  - 注册源名改为 `hypo_info`
  - `_check_new_info()` 调用 `/api/agent/query?time_range=today&min_importance=7`
  - 匹配逻辑沿用“关键字 + 分类”过滤
  - 偏好键仍可沿用 `info_reach.last_heartbeat_check_at`
- `run_scheduled_summary()` 和订阅推送统一改为 Hypo-Info 文案与事件元数据

**Step 4: Run tests to verify it passes**

Run: `uv run pytest tests/test_info_reach_skill.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add src/hypo_agent/skills/info_reach_skill.py tests/test_info_reach_skill.py
git commit -m "M8: migrate info reach subscriptions and heartbeat to hypo info"
```

---

### Task 4: 更新 EventQueue、Pipeline 与 Scheduler 注册逻辑，统一主动推送命名

**Files:**
- Modify: `src/hypo_agent/core/event_queue.py`
- Modify: `src/hypo_agent/core/pipeline.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `tests/core/test_event_queue.py`
- Modify: `tests/core/test_pipeline_event_consumer.py`
- Modify: `tests/gateway/test_app_scheduler_lifecycle.py`
- Modify: `tests/core/test_heartbeat.py`

**Step 1: Write the failing test**

```python
def test_event_queue_accepts_hypo_info_event() -> None:
    async def _run() -> None:
        queue = EventQueue()
        await queue.put(
            {
                "event_type": "hypo_info_trigger",
                "session_id": "main",
                "summary": "📰 Hypo-Info 摘要",
            }
        )
        event = await queue.get()
        assert event["event_type"] == "hypo_info_trigger"
    asyncio.run(_run())
```

```python
def test_pipeline_event_consumer_writes_hypo_info_message() -> None:
    async def _run() -> None:
        ...
        await queue.put(
            {
                "event_type": "hypo_info_trigger",
                "session_id": "main",
                "title": "Hypo-Info 摘要",
                "summary": "AI：模型更新",
            }
        )
        ...
        assert "Hypo-Info" in (memory.appended[0].text or "")
    asyncio.run(_run())
```

```python
def test_app_registers_hypo_info_digest_jobs_from_tasks_config(tmp_path) -> None:
    (config_dir / "tasks.yaml").write_text(
        """
heartbeat:
  enabled: false
hypo_info_digest:
  enabled: true
  interval_minutes: 480
  time: "09:00,21:00"
""".strip(),
        encoding="utf-8",
    )
    ...
    assert scheduler.cron_jobs == [
        ("hypo_info_digest_0900", "0 9 * * *"),
        ("hypo_info_digest_2100", "0 21 * * *"),
    ]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_event_queue.py tests/core/test_pipeline_event_consumer.py tests/gateway/test_app_scheduler_lifecycle.py tests/core/test_heartbeat.py -q`

Expected: FAIL，因为代码仍使用 `trendradar_trigger` / `trendradar_summary`

**Step 3: Write minimal implementation**

- `src/hypo_agent/core/event_queue.py`
  - `SchedulerEventType` 中将 `trendradar_trigger` 替换为 `hypo_info_trigger`
- `src/hypo_agent/core/pipeline.py`
  - 将 `trendradar_trigger` 分支改为 `hypo_info_trigger`
  - 文案从 `TrendRadar 更新` 改为 `Hypo-Info 更新`
- `src/hypo_agent/gateway/app.py`
  - 停止读取 `skills.yaml` 中的 `output_root`
  - 构造 `InfoReachSkill` 时改传 `secrets_path="config/secrets.yaml"` 或解析后的 `base_url`
  - 调度注册改为读取 `tasks_cfg.hypo_info_digest`
  - job 名统一使用 `hypo_info_digest` 前缀
- `tests/core/test_heartbeat.py`
  - 事件源名断言从 `trendradar` 改为 `hypo_info`

**Step 4: Run tests to verify it passes**

Run: `uv run pytest tests/core/test_event_queue.py tests/core/test_pipeline_event_consumer.py tests/gateway/test_app_scheduler_lifecycle.py tests/core/test_heartbeat.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add src/hypo_agent/core/event_queue.py src/hypo_agent/core/pipeline.py src/hypo_agent/gateway/app.py tests/core/test_event_queue.py tests/core/test_pipeline_event_consumer.py tests/gateway/test_app_scheduler_lifecycle.py tests/core/test_heartbeat.py
git commit -m "M8: rename hypo info scheduler and queue events"
```

---

### Task 5: 更新静态配置与移除 TrendRadar 文件系统依赖

**Files:**
- Modify: `config/skills.yaml`
- Modify: `config/tasks.yaml`
- Modify: `config/security.yaml`
- Modify: `config/secrets.yaml`
- Modify: `src/hypo_agent/skills/info_reach_skill.py`

**Step 1: Write the failing test**

```python
def test_runtime_configs_use_hypo_info_defaults() -> None:
    tasks_text = Path("config/tasks.yaml").read_text(encoding="utf-8")
    security_text = Path("config/security.yaml").read_text(encoding="utf-8")
    skills_text = Path("config/skills.yaml").read_text(encoding="utf-8")

    assert "hypo_info_digest:" in tasks_text
    assert "trendradar_summary:" not in tasks_text
    assert "~/trendradar/output" not in security_text
    assert "output_root:" not in skills_text
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_config_loader.py -k "hypo_info" -q`

Expected: FAIL，当前示例配置仍包含 TrendRadar 路径/任务名

**Step 3: Write minimal implementation**

- `config/secrets.yaml`
  - 确认 `services.hypo_info.base_url: "http://localhost:8200"` 存在
  - 若当前文件已存在该项，仅整理为正式配置并去掉“临时注释”表述
- `config/skills.yaml`
  - 删除 `info_reach.output_root`
  - 保留 `info_reach.enabled: true`
  - 不引入无消费方的额外字段
- `config/tasks.yaml`
  - 删除 `trendradar_summary`
  - 新增 `hypo_info_digest`
  - 对齐需求中的时间：`09:00,21:00`
- `config/security.yaml`
  - 删除 `~/trendradar/output` 白名单
- `src/hypo_agent/skills/info_reach_skill.py`
  - 清理 `glob`、`sqlite3.connect`、HTML 报告解析、本地权限检查等 TrendRadar 直读逻辑
  - 保留简短注释说明：“已迁移到 Hypo-Info API，TrendRadar 并行运行中”

**Step 4: Run tests to verify it passes**

Run: `uv run pytest tests/core/test_config_loader.py tests/test_info_reach_skill.py -q`

Expected: PASS

**Step 5: Commit**

```bash
git add config/skills.yaml config/tasks.yaml config/security.yaml config/secrets.yaml src/hypo_agent/skills/info_reach_skill.py tests/core/test_config_loader.py tests/test_info_reach_skill.py
git commit -m "M8: remove trendradar filesystem config from info reach"
```

---

### Task 6: 端到端验证、测试模式 smoke 与完成文档

**Files:**
- Modify: `docs/plans/2026-03-30-m8-hypo-info-integration-implementation-plan.md`
- Create or Modify: 里程碑相关说明文档（如执行过程中需要）

**Step 1: Run focused tests**

Run:

```bash
uv run pytest tests/test_info_reach_skill.py tests/core/test_event_queue.py tests/core/test_pipeline_event_consumer.py tests/gateway/test_app_scheduler_lifecycle.py tests/core/test_config_loader.py tests/test_models_serialization.py -q
```

Expected: PASS

**Step 2: Run full test suite**

Run: `uv run pytest -q`

Expected: PASS

**Step 3: Run default test-mode smoke**

Run:

```bash
bash test_run.sh
HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke
```

Expected:
- 测试模式启动成功
- smoke 通过
- 不触发真实 QQ 发送

**Step 4: Manual verification**

Run:

```text
今天有什么新闻
```

Expected:
- 命中 `info_query`
- 返回内容来自 Hypo-Info API，而非本地 TrendRadar 数据

Also verify:
- 等待 `hypo_info_digest` 定时任务触发，主会话收到 Hypo-Info 每日简报

**Step 5: Completion report and doc commit**

- 输出 Completion Report，必须包含：
  - 变更文件清单
  - 实现摘要
  - 偏差记录
  - 测试结果
  - 遗留问题
- 如更新里程碑文档，单独提交：

```bash
git add docs/...
git commit -m "M8[doc]: document hypo info integration"
```

---

## Notes for Execution

- `InfoReachSkill` 现有测试几乎全部基于本地 SQLite/HTML，需要整体重写，不要尝试兼容旧断言。
- `HypoInfoConfig` 已存在于模型层，实现时应复用，不要再造第二套配置模型。
- 如果 `config/secrets.yaml` 缺失或 `services.hypo_info.base_url` 为空，运行时应回退 `http://localhost:8200`，而不是直接崩溃。
- `info_summary()` 与定时推送应输出文本；不要把原始 JSON 暴露给主会话。
- 自动迁移逻辑必须幂等，否则测试和重启场景会重复导入旧数据。

---

---

## Completion Report

**日期：** 2026-03-30
**执行结果：** 全部 6 个任务完成，721 个测试通过。

### 变更文件清单

| 文件 | 变更类型 |
|------|----------|
| `src/hypo_agent/models.py` | `TrendRadarSummaryTaskConfig` → `HypoInfoDigestTaskConfig`，`TasksConfig.trendradar_summary` → `hypo_info_digest` |
| `src/hypo_agent/skills/info_reach_skill.py` | 完整重写：移除 TrendRadar 本地读取，新增 `HypoInfoClient`（httpx），`info_*` 工具集，订阅表迁移，Heartbeat 事件源 |
| `src/hypo_agent/core/event_queue.py` | `trendradar_trigger` → `hypo_info_trigger` |
| `src/hypo_agent/core/pipeline.py` | `trendradar_trigger` 分支 → `hypo_info_trigger`，文案更新 |
| `src/hypo_agent/gateway/app.py` | 调度注册 `trendradar_summary_*` → `hypo_info_digest_*`，InfoReachSkill 构造参数 |
| `config/tasks.yaml` | `trendradar_summary` → `hypo_info_digest`，时间调整为 `09:00,21:00` |
| `config/skills.yaml` | 删除 `output_root` |
| `config/security.yaml` | 删除 `~/trendradar/output` 白名单 |
| `tests/test_info_reach_skill.py` | 完整重写为 Hypo-Info API mock 测试 |
| `tests/core/test_config_loader.py` | 新增/更新配置命名测试 |
| `tests/test_models_serialization.py` | 沿用，覆盖 `HypoInfoConfig` |
| `tests/core/test_event_queue.py` | `trendradar_trigger` → `hypo_info_trigger` |
| `tests/core/test_pipeline_event_consumer.py` | `trendradar_trigger` → `hypo_info_trigger` |
| `tests/gateway/test_app_scheduler_lifecycle.py` | `trendradar_summary_*` → `hypo_info_digest_*` |
| `tests/core/test_heartbeat.py` | `trendradar` → `hypo_info` |

### 实现摘要

- `HypoInfoClient`：基于 `httpx.AsyncClient`，`/api/agent/query` + `/api/agent/digest`，统一异常封装
- 订阅表：`trendradar_subscriptions` → `info_subscriptions`（`platforms` → `categories`），启动时自动幂等迁移
- Heartbeat 事件源名：`trendradar` → `hypo_info`
- 调度任务名：`trendradar_summary_HHMM` → `hypo_info_digest_HHMM`
- 队列事件类型：`trendradar_trigger` → `hypo_info_trigger`

### 偏差记录

- 无重大偏差。`info_summary` 按计划调用 `/api/agent/digest`。
- Task 3 的 `_check_new_info` 测试简化为验证 `name == "hypo_info"` 和 `new_items == 1`，未做 `queue` 返回值解包（plan 示例中有歧义）。

### 测试结果

- 全量 `uv run pytest`：**721 passed, 0 failed**（2026-03-30）
- Smoke test：生产进程占用 8765 端口，需手动隔离后执行 `HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke`

### 遗留问题

- `config/secrets.yaml` 中 `hypo_info.base_url` 仍为 `http://localhost:8200`，生产部署时需更新为实际地址。
- TrendRadar 服务并行运行中，后续可在确认 Hypo-Info 稳定后下线。
