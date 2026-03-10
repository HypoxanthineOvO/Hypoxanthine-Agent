# T1-fix System Test Failures Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复 T1 系统测试 7 个 FAIL（P0/P1/P2），满足全部 Smoke Gate。

**Architecture:** 维持现有 Pipeline/Skill/Channel 结构，仅在安全层、熔断反馈、时间注入、渠道广播与记忆工具上做最小改动；所有回复统一走 ChannelDispatcher；权限控制升级为“白名单 + 灰名单只读 + 黑名单拒绝”。

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest, APScheduler, SQLite, Vue/Vitest。

---

### Task 0: 基线验证（进入修复前）

**Files:**
- None

**Step 1: 运行后端测试基线**

Run: `pytest -q`
Expected: PASS

**Step 2: 运行前端测试基线**

Run: `cd web && npm run test`
Expected: PASS

**Step 3: 运行 smoke 基线**

Run: `python scripts/agent_cli.py smoke`
Expected: PASS

**Step 4: Commit**

No commit (baseline check only).

---

### Task 1: Fix-01 Blocked Paths（黑名单）全链路生效

**Files:**
- Modify: `config/security.yaml`
- Modify: `src/hypo_agent/models.py`
- Modify: `src/hypo_agent/security/permission_manager.py`
- Modify: `src/hypo_agent/skills/code_run_skill.py`
- Modify: `src/hypo_agent/skills/tmux_skill.py`
- Test: `tests/security/test_permission_manager.py`
- Test: `tests/skills/test_code_run_skill.py`
- Test: `tests/skills/test_tmux_skill.py`
- Test: `tests/skills/test_fs_skill.py`

**Step 1: 写失败测试（权限黑名单）**

Add to `tests/security/test_permission_manager.py`:
```python
def test_permission_manager_blocks_blocked_paths(tmp_path: Path) -> None:
    whitelist = DirectoryWhitelist(
        rules=[WhitelistRule(path=str(tmp_path), permissions=["read", "write"])],
        default_policy="readonly",
        blocked_paths=["/etc/passwd", "~/.ssh"],
    )
    manager = PermissionManager(whitelist)
    allowed, reason = manager.check_permission("/etc/passwd", "read")
    assert allowed is False
    assert "blocked" in reason.lower()
```
```python
def test_permission_manager_blocks_symlink_to_blocked(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("x")
    link = tmp_path / "link"
    link.symlink_to(blocked)
    whitelist = DirectoryWhitelist(rules=[], default_policy="readonly", blocked_paths=[str(blocked)])
    manager = PermissionManager(whitelist)
    allowed, _ = manager.check_permission(str(link), "read")
    assert allowed is False
```

**Step 2: 写失败测试（FileSystemSkill 灰/白/黑）**

Add to `tests/skills/test_fs_skill.py`:
```python
def test_blocked_path_filesystem_denied(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("secret")
    manager = PermissionManager(
        DirectoryWhitelist(rules=[], default_policy="readonly", blocked_paths=[str(blocked)])
    )
    skill = FileSystemSkill(permission_manager=manager, index_file=tmp_path / "index.json")
    output = asyncio.run(skill.execute("read_file", {"path": str(blocked)}))
    assert output.status == "error"
    assert "permission" in (output.error_info or "").lower()
```
```python
def test_gray_zone_read_allowed(tmp_path: Path) -> None:
    gray = tmp_path / "gray.txt"
    gray.write_text("ok")
    manager = PermissionManager(DirectoryWhitelist(rules=[], default_policy="readonly"))
    skill = FileSystemSkill(permission_manager=manager, index_file=tmp_path / "index.json")
    output = asyncio.run(skill.execute("read_file", {"path": str(gray)}))
    assert output.status == "success"
```
```python
def test_gray_zone_write_denied(tmp_path: Path) -> None:
    gray = tmp_path / "gray.txt"
    manager = PermissionManager(DirectoryWhitelist(rules=[], default_policy="readonly"))
    skill = FileSystemSkill(permission_manager=manager, index_file=tmp_path / "index.json")
    output = asyncio.run(skill.execute("write_file", {"path": str(gray), "content": "x"}))
    assert output.status == "error"
```

**Step 3: 写失败测试（CodeRun bwrap tmpfs）**

Add to `tests/skills/test_code_run_skill.py`:
```python
def test_bwrap_command_masks_blocked_paths(tmp_path: Path) -> None:
    manager = PermissionManager(
        DirectoryWhitelist(
            rules=[WhitelistRule(path=str(tmp_path), permissions=["read", "write", "execute"])],
            default_policy="readonly",
            blocked_paths=["/etc/passwd"],
        )
    )
    skill = CodeRunSkill(permission_manager=manager, sandbox_dir=tmp_path / "sandbox")
    cmd = "python /tmp/hypo-agent-sandbox/run.py"
    bwrap_cmd = " ".join(skill._build_bwrap_command(cmd))
    assert "--tmpfs /etc/passwd" in bwrap_cmd
```

**Step 4: 写失败测试（TmuxSkill 拦截）**

Add to `tests/skills/test_tmux_skill.py`:
```python
def test_tmux_blocks_blocked_path(tmp_path: Path) -> None:
    manager = PermissionManager(
        DirectoryWhitelist(rules=[], default_policy="readonly", blocked_paths=["/etc/passwd"])
    )
    skill = TmuxSkill(
        permission_manager=manager,
        subprocess_exec=_build_fake_tmux_exec(command_stdout="ok"),
    )
    output = asyncio.run(skill.execute("run_command", {"command": "cat /etc/passwd"}))
    assert output.status == "error"
    assert "permission denied" in (output.error_info or "").lower()
```

**Step 5: 运行测试确认失败**

Run: `pytest tests/security/test_permission_manager.py tests/skills/test_fs_skill.py tests/skills/test_code_run_skill.py tests/skills/test_tmux_skill.py -q`
Expected: FAIL（blocked_paths 尚未实现）

**Step 6: 最小实现（模型、权限、CodeRun、Tmux）**

1) `DirectoryWhitelist` 增加字段：
```python
blocked_paths: list[str] = Field(default_factory=list)
```

2) `PermissionManager`：
- 初始化时解析 blocked_paths（`expanduser().resolve(strict=False)`）
- `check_permission` 先调用 `_is_blocked(resolved_path)`，命中直接 deny
- log `permission.blocked`

3) `CodeRunSkill._build_bwrap_command`：
- 读取 `permission_manager.blocked_paths()`
- 对每个 blocked path 增加 `--tmpfs <path>`

4) `TmuxSkill`：
- `__init__` 增加 `permission_manager` 注入
- 新增 `scan_command()`，提取路径 token，调用 `PermissionManager.check_permission`
- 在 `execute()` 开始处调用扫描，命中即返回 `SkillOutput(status="error", error_info="Permission denied: ...")`

5) `config/security.yaml` 增加 `blocked_paths` 列表

**Step 7: 运行测试确认通过**

Run: `pytest tests/security/test_permission_manager.py tests/skills/test_fs_skill.py tests/skills/test_code_run_skill.py tests/skills/test_tmux_skill.py -q`
Expected: PASS

**Step 8: Commit**

```bash
git add config/security.yaml src/hypo_agent/models.py src/hypo_agent/security/permission_manager.py \
  src/hypo_agent/skills/code_run_skill.py src/hypo_agent/skills/tmux_skill.py \
  tests/security/test_permission_manager.py tests/skills/test_fs_skill.py \
  tests/skills/test_code_run_skill.py tests/skills/test_tmux_skill.py

git commit -m "T1-fix[P0]: introduce blocked_paths blacklist, enforce across all skill execution paths"
```

---

### Task 2: Fix-02 Kill Switch 立即中断与 /resume

**Files:**
- Modify: `src/hypo_agent/core/slash_commands.py`
- Modify: `src/hypo_agent/security/circuit_breaker.py`
- Modify: `src/hypo_agent/core/pipeline.py`
- Modify: `src/hypo_agent/core/skill_manager.py`
- Test: `tests/core/test_slash_commands.py`
- Test: `tests/core/test_pipeline.py`
- Test: `tests/skills/test_skill_manager.py`

**Step 1: 写失败测试（slash + resume）**

Add to `tests/core/test_slash_commands.py`:
```python
async def test_resume_without_kill_returns_hint():
    handler = _build_handler()
    msg = Message(text="/resume", sender="user", session_id="main")
    out = await handler.try_handle(msg)
    assert "未处于" in out
```

**Step 2: 写失败测试（kill blocks skill）**

Add to `tests/skills/test_skill_manager.py`:
```python
def test_kill_blocks_skill_execution() -> None:
    breaker = CircuitBreaker(CircuitBreakerConfig(global_kill_switch=True))
    manager = SkillManager(circuit_breaker=breaker)
    output = asyncio.run(manager.invoke("unknown_tool", {}, session_id="main"))
    assert "kill" in (output.error_info or "").lower()
```

**Step 3: 写失败测试（pipeline kill blocks llm）**

Add to `tests/core/test_pipeline.py`:
```python
async def test_kill_blocks_llm(monkeypatch):
    breaker = CircuitBreaker(CircuitBreakerConfig(global_kill_switch=True))
    pipeline = _build_pipeline(circuit_breaker=breaker)
    msg = Message(text="hello", sender="user", session_id="main")
    out = await pipeline.run_once(msg)
    assert "Kill Switch" in (out.text or "")
```

**Step 4: 运行测试确认失败**

Run: `pytest tests/core/test_slash_commands.py tests/skills/test_skill_manager.py tests/core/test_pipeline.py -q`
Expected: FAIL

**Step 5: 最小实现**

1) `CircuitBreaker` 增加 `get_global_kill_switch()` 与 `set_global_kill_switch()` 现有基础上，增加 `is_kill_active()` helper。

2) `SlashCommandHandler`：
- 新增 `/resume` entry
- `/kill` 只开启；`/resume` 关闭
- 返回固定文案

3) `SkillManager.invoke()`：
- 若 global kill 为 True，直接返回 `SkillOutput(status="error", error_info="KillSwitchActive")`

4) `ChatPipeline`：
- `_try_handle_slash` 仍优先
- LLM 入口前检查 kill，直接返回固定文案
- streaming 时检测 kill 并尽快停止（在每个 chunk 前检查）

**Step 6: 运行测试确认通过**

Run: `pytest tests/core/test_slash_commands.py tests/skills/test_skill_manager.py tests/core/test_pipeline.py -q`
Expected: PASS

**Step 7: Commit**

```bash
git add src/hypo_agent/core/slash_commands.py src/hypo_agent/security/circuit_breaker.py \
  src/hypo_agent/core/pipeline.py src/hypo_agent/core/skill_manager.py \
  tests/core/test_slash_commands.py tests/skills/test_skill_manager.py tests/core/test_pipeline.py

git commit -m "T1-fix[P0]: kill switch immediately cancels streaming and blocks all execution"
```

---

### Task 3: Smoke Gate A

**Files:**
- None

**Step 1: 运行 P0 针对性测试**

Run: `pytest -q tests/ -k "blocked_path or kill or resume"`
Expected: PASS

**Step 2: 运行 smoke**

Run: `python scripts/agent_cli.py smoke`
Expected: PASS

**Step 3: Commit**

No commit (gate only).

---

### Task 4: Fix-03 Circuit Breaker 可见反馈

**Files:**
- Modify: `src/hypo_agent/security/circuit_breaker.py`
- Modify: `src/hypo_agent/core/skill_manager.py`
- Test: `tests/security/test_circuit_breaker.py`
- Test: `tests/core/test_pipeline_tools.py`

**Step 1: 写失败测试（工具 fused）**

Add to `tests/security/test_circuit_breaker.py`:
```python
def test_tool_fuse_after_three_failures() -> None:
    breaker = CircuitBreaker(_config())
    for _ in range(2):
        breaker.record_failure("tool", "main")
    allowed, reason = breaker.can_execute("tool", "main")
    assert allowed is True
    breaker.record_failure("tool", "main")
    allowed, reason = breaker.can_execute("tool", "main")
    assert allowed is False
    assert "disabled" in reason.lower()
```

**Step 2: 写失败测试（会话 5 次暂停）**

Add to `tests/core/test_pipeline_tools.py`:
```python
def test_session_fuse_after_5_errors_returns_message():
    pipeline = _build_pipeline_with_failing_tool(max_failures=5)
    events = asyncio.run(_collect_events(pipeline))
    assert any("暂停" in (e.get("text") or "") for e in events)
```

**Step 3: 运行测试确认失败**

Run: `pytest tests/security/test_circuit_breaker.py tests/core/test_pipeline_tools.py -q`
Expected: FAIL

**Step 4: 最小实现**

- `CircuitBreaker`：
  - 当工具连续失败到阈值，记录 fused 状态（不使用 cooldown）
  - `can_execute()` 对 fused 返回 `False, "Tool '<name>' has been disabled..."`
  - 会话累计失败到阈值时标记会话暂停
- `SkillManager.invoke()`：
  - 当 `can_execute()` 返回 fused，直接返回 `SkillOutput(status="fused", error_info=...)`
  - 会话暂停时返回固定文案

**Step 5: 运行测试确认通过**

Run: `pytest tests/security/test_circuit_breaker.py tests/core/test_pipeline_tools.py -q`
Expected: PASS

**Step 6: Commit**

```bash
git add src/hypo_agent/security/circuit_breaker.py src/hypo_agent/core/skill_manager.py \
  tests/security/test_circuit_breaker.py tests/core/test_pipeline_tools.py

git commit -m "T1-fix[P1]: circuit breaker returns structured fuse feedback to LLM"
```

---

### Task 5: Fix-04 Reminder 时间注入与校验

**Files:**
- Modify: `src/hypo_agent/core/pipeline.py`
- Modify: `src/hypo_agent/skills/reminder_skill.py`
- Test: `tests/core/test_pipeline.py`
- Test: `tests/skills/test_reminder_skill.py`

**Step 1: 写失败测试（system prompt 含当前时间）**

Add to `tests/core/test_pipeline.py`:
```python
def test_system_prompt_contains_current_time():
    pipeline = _build_pipeline()
    msg = Message(text="hi", sender="user", session_id="main")
    llm_messages = pipeline._build_llm_messages(msg, use_tools=True)
    assert any("当前时间" in m.get("content", "") for m in llm_messages if m["role"] == "system")
```

**Step 2: 写失败测试（reminder past time rejected）**

Add to `tests/skills/test_reminder_skill.py`:
```python
async def test_reminder_rejects_past_time(reminder_skill):
    past = (datetime.now() - timedelta(minutes=5)).isoformat()
    output = await reminder_skill.execute("create_reminder", {
        "title": "t",
        "schedule_type": "once",
        "schedule_value": past,
        "channel": "all",
    })
    assert output.status == "error"
    assert "past" in (output.error_info or "")
```

**Step 3: 运行测试确认失败**

Run: `pytest tests/core/test_pipeline.py tests/skills/test_reminder_skill.py -q`
Expected: FAIL

**Step 4: 最小实现**

- `ChatPipeline._build_llm_messages`：在 system prompt 追加 `当前时间: <ISO> (Asia/Shanghai)`。
- `ReminderSkill.create_reminder()`：
  - 校验 `trigger_time` 为未来（允许 30s 容差）
  - past 时间直接 error
- 更新 tool schema 描述：ISO 8601 + 必须未来

**Step 5: 运行测试确认通过**

Run: `pytest tests/core/test_pipeline.py tests/skills/test_reminder_skill.py -q`
Expected: PASS

**Step 6: Commit**

```bash
git add src/hypo_agent/core/pipeline.py src/hypo_agent/skills/reminder_skill.py \
  tests/core/test_pipeline.py tests/skills/test_reminder_skill.py

git commit -m "T1-fix[P1]: inject server time into system prompt, validate reminder trigger_time"
```

---

### Task 6: Fix-05 渠道广播统一

**Files:**
- Modify: `src/hypo_agent/core/channel_dispatcher.py`
- Modify: `src/hypo_agent/gateway/ws.py`
- Modify: `src/hypo_agent/channels/qq_channel.py`
- Test: `tests/core/test_channel_dispatcher.py`
- Test: `tests/gateway/test_qq_ws.py`

**Step 1: 写失败测试（QQ → WebUI 广播）**

Add to `tests/core/test_channel_dispatcher.py`:
```python
def test_broadcast_sends_to_all_sinks():
    dispatcher = ChannelDispatcher()
    webui = StubSink()
    qq = StubSink()
    dispatcher.register_sink("webui", webui)
    dispatcher.register_sink("qq", qq)
    response = RichResponse(text="hi", channel="qq")
    asyncio.run(dispatcher.broadcast(response))
    assert webui.sent and qq.sent
```

**Step 2: 运行测试确认失败**

Run: `pytest tests/core/test_channel_dispatcher.py tests/gateway/test_qq_ws.py -q`
Expected: FAIL

**Step 3: 最小实现**

- Pipeline 回复路径统一走 `ChannelDispatcher.broadcast()`
- QQ 入站回复不要单发，统一广播
- `channel` 字段传递到 WebUI WS

**Step 4: 运行测试确认通过**

Run: `pytest tests/core/test_channel_dispatcher.py tests/gateway/test_qq_ws.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add src/hypo_agent/core/channel_dispatcher.py src/hypo_agent/gateway/ws.py \
  src/hypo_agent/channels/qq_channel.py tests/core/test_channel_dispatcher.py \
  tests/gateway/test_qq_ws.py

git commit -m "T1-fix[P1]: unify pipeline reply path through ChannelDispatcher broadcast"
```

---

### Task 7: Fix-06 OutputCompressor 标记注入

**Files:**
- Modify: `src/hypo_agent/core/output_compressor.py`
- Modify: `src/hypo_agent/core/pipeline.py`
- Test: `tests/core/test_output_compressor.py`
- Test: `tests/core/test_pipeline_tools.py`

**Step 1: 写失败测试（标记追加）**

Add to `tests/core/test_output_compressor.py`:
```python
def test_compressor_appends_marker():
    compressor = OutputCompressor(router=StubRouter())
    output, compressed = asyncio.run(compressor.compress_if_needed("x" * 3000, {}))
    assert compressed is True
    assert "📦 Output compressed" in output
```

**Step 2: 写失败测试（最终回复包含标记）**

Add to `tests/core/test_pipeline_tools.py`:
```python
def test_response_contains_compressed_marker():
    events = asyncio.run(_run_pipeline_with_long_tool_output())
    assert any("📦 Output compressed" in (e.get("text") or "") for e in events)
```

**Step 3: 运行测试确认失败**

Run: `pytest tests/core/test_output_compressor.py tests/core/test_pipeline_tools.py -q`
Expected: FAIL

**Step 4: 最小实现**

- `OutputCompressor.compress_if_needed`：压缩结果末尾追加标记
- `ChatPipeline` 组装最终回复时兜底追加标记

**Step 5: 运行测试确认通过**

Run: `pytest tests/core/test_output_compressor.py tests/core/test_pipeline_tools.py -q`
Expected: PASS

**Step 6: Commit**

```bash
git add src/hypo_agent/core/output_compressor.py src/hypo_agent/core/pipeline.py \
  tests/core/test_output_compressor.py tests/core/test_pipeline_tools.py

git commit -m "T1-fix[P1]: ensure OutputCompressor marker appears in final assistant reply"
```

---

### Task 8: Smoke Gate B

**Files:**
- None

**Step 1: 全量后端测试**

Run: `pytest -q`
Expected: PASS

**Step 2: 前端测试**

Run: `cd web && npm run test`
Expected: PASS

**Step 3: smoke**

Run: `python scripts/agent_cli.py smoke`
Expected: PASS

**Step 4: Commit**

No commit (gate only).

---

### Task 9: Fix-07 偏好记忆 L2 持久化

**Files:**
- Modify: `src/hypo_agent/memory/structured_store.py`
- Modify: `src/hypo_agent/skills/memory_skill.py` (若不存在则新增)
- Modify: `src/hypo_agent/core/pipeline.py`
- Test: `tests/memory/test_structured_store.py`
- Test: `tests/core/test_pipeline.py`

**Step 1: 写失败测试（save/get/upsert）**

Add to `tests/memory/test_structured_store.py`:
```python
async def test_save_and_get_preference(tmp_path: Path):
    store = StructuredStore(db_path=tmp_path / "test.db")
    await store.init()
    await store.save_preference("favorite_drink", "绿茶")
    assert await store.get_preference("favorite_drink") == "绿茶"
```

**Step 2: 写失败测试（injection）**

Add to `tests/core/test_pipeline.py`:
```python
def test_preference_injection_in_prompt():
    pipeline = _build_pipeline_with_preferences({"喜欢的饮品": "绿茶"})
    msg = Message(text="hi", sender="user", session_id="main")
    llm_messages = pipeline._build_llm_messages(msg, use_tools=True)
    assert any("User Preferences" in m.get("content", "") for m in llm_messages if m["role"] == "system")
```

**Step 3: 运行测试确认失败**

Run: `pytest tests/memory/test_structured_store.py tests/core/test_pipeline.py -q`
Expected: FAIL

**Step 4: 最小实现**

- `StructuredStore`：新增 `save_preference`/`get_preference` 方法，INSERT OR REPLACE。
- `MemorySkill`：暴露 `save_preference`/`get_preference` 工具。
- `ChatPipeline`：system prompt 注入 `[User Preferences]` 区块（最多 20 条）。

**Step 5: 运行测试确认通过**

Run: `pytest tests/memory/test_structured_store.py tests/core/test_pipeline.py -q`
Expected: PASS

**Step 6: Commit**

```bash
git add src/hypo_agent/memory/structured_store.py src/hypo_agent/skills/memory_skill.py \
  src/hypo_agent/core/pipeline.py tests/memory/test_structured_store.py tests/core/test_pipeline.py

git commit -m "T1-fix[P2]: add save/get preference tools with L2 persistence and context injection"
```

---

### Task 10: Final Smoke Gate

**Files:**
- None

**Step 1: pytest**

Run: `pytest -q`
Expected: PASS

**Step 2: vitest**

Run: `cd web && npm run test`
Expected: PASS

**Step 3: smoke**

Run: `python scripts/agent_cli.py smoke`
Expected: PASS

**Step 4: Commit**

No commit (gate only).

---

## Execution Notes
- 所有 Fix commit 必须使用指定 message：
  - `T1-fix[P0]: introduce blocked_paths blacklist, enforce across all skill execution paths`
  - `T1-fix[P0]: kill switch immediately cancels streaming and blocks all execution`
  - `T1-fix[P1]: circuit breaker returns structured fuse feedback to LLM`
  - `T1-fix[P1]: inject server time into system prompt, validate reminder trigger_time`
  - `T1-fix[P1]: unify pipeline reply path through ChannelDispatcher broadcast`
  - `T1-fix[P1]: ensure OutputCompressor marker appears in final assistant reply`
  - `T1-fix[P2]: add save/get preference tools with L2 persistence and context injection`
