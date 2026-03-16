# 测试/生产隔离泄漏排查与修复

日期：2026-03-16

## 结论

- `8766` 测试进程与 `8765` 生产进程之间不存在共享的进程内消息通道。
  - 没有跨进程共享的 `asyncio.Queue`
  - 没有跨进程 WebSocket 广播总线
  - 没有额外 IPC
- 真实的跨进程泄漏面是共享存储。
  - `SessionMemory` 默认落盘到 `memory/sessions/`
  - `StructuredStore` 默认落盘到 `memory/hypo.db`
  - `SchedulerService` 会从当前进程绑定的 SQLite `reminders` 表重建 active job，并在触发后走 `EventQueue -> ChatPipeline -> ChannelDispatcher -> QQChannelService`
- 因此，只要测试实例错误地解析到了生产存储路径，生产进程就可能感知到测试数据。

## 三个问题的排查结果

### 1. 8766 和 8765 之间有没有共享消息通道？

没有共享的进程内消息通道。

代码结论：

- `EventQueue` 是进程内 `asyncio.Queue`，在 `create_app()` 内按进程创建。
- WebSocket 连接管理器 `connection_manager` 只服务当前 FastAPI 进程。
- `ChannelDispatcher` 也是当前进程内注册/广播。

可能共享的只有落盘存储：

- L1 SessionMemory：`src/hypo_agent/memory/session.py`
- L2 SQLite：`src/hypo_agent/memory/structured_store.py`

修复前的漏洞点：

- `get_memory_dir()` 先读 `HYPO_MEMORY_DIR`，再判断 `HYPO_TEST_MODE`
- `get_database_path()` 先读 `HYPO_DB_PATH`，再判断 `HYPO_TEST_MODE`

这意味着如果测试进程启动环境里残留了生产路径变量，测试模式会被绕过，直接写到生产存储。

### 2. 生产进程的 QQ adapter 广播消息时，从什么数据源读取？

不是监听 SessionMemory 文件变更，也不是轮询 SQLite 新消息。

实际链路有两条：

1. WebUI 镜像到 QQ
   - `src/hypo_agent/gateway/ws.py`
   - WebSocket 收到 `channel="webui"` 的用户消息后，直接调用 `mirror_webui_message_to_qq()`
   - `mirror_webui_message_to_qq()` 最终调用 `QQChannelService.send_message()`

2. reminder / heartbeat / email_scan 主动消息广播到 QQ
   - `src/hypo_agent/core/scheduler.py`
   - `SchedulerService.reload_active_jobs()` 从当前 SQLite `reminders` 表恢复任务
   - `_handle_job_trigger()` 触发后写入当前进程 `EventQueue`
   - `src/hypo_agent/core/pipeline.py` 的 `_consume_event_loop()` 将事件转成 `Message`
   - `src/hypo_agent/gateway/app.py` 的 `on_proactive_message()` 通过 `ChannelDispatcher.broadcast()` 广播
   - QQ sink 是 `QQChannelService.send_message()`

结论：QQ adapter 不读 SessionMemory 文件，也不扫 `hypo.db` 新消息；它只发送当前进程主动推送到 dispatcher 的 `Message`。

### 3. reminder "提醒喝水" 写入了哪个 DB？

当前修复后，测试模式强制写入 `test/sandbox/hypo.db`。

现场验证：

- `sqlite3 test/sandbox/hypo.db "SELECT COUNT(*) FROM reminders WHERE title LIKE 'm8_smoke%';"` 返回 `1`
- `sqlite3 memory/hypo.db "SELECT COUNT(*) FROM reminders WHERE title LIKE 'm8_smoke%';"` 返回 `0`

对历史事故的判断：

- 从代码路径推断，如果生产 `8765` 真收到了测试 reminder，对应测试进程在事故发生时大概率与生产进程共用了 SQLite 存储路径。
- 这是基于代码链路的推断；并非来自事故当时的历史日志回放。

## 修复内容

### 1. 测试模式强制覆盖存储路径

修改：

- `src/hypo_agent/core/config_loader.py`

变更：

- `HYPO_TEST_MODE=1` 时，`get_memory_dir()` 始终返回 `test/sandbox/memory`
- `HYPO_TEST_MODE=1` 时，`get_database_path()` 始终返回 `test/sandbox/hypo.db`
- 不再允许 `HYPO_MEMORY_DIR` / `HYPO_DB_PATH` 在测试模式下覆盖 sandbox

目的：

- 防止测试实例因为环境变量残留而写到生产 `memory/` 或生产 DB

### 2. 测试模式启动时强校验 storage isolation

修改：

- `src/hypo_agent/gateway/app.py`

变更：

- `create_app()` 在 `HYPO_TEST_MODE=1` 下会校验：
  - `session_memory.sessions_dir == test/sandbox/memory/sessions`
  - `structured_store.db_path == test/sandbox/hypo.db`
- 不满足时直接拒绝启动

目的：

- 即使外部代码显式传入了非 sandbox 的 `AppDeps`，也不能在测试模式下启动

### 3. smoke 增加 8765 生产端口门禁

修改：

- `scripts/agent_cli.py`

变更：

- `HYPO_TEST_MODE=1` 且 `--port 8765` 时，直接拒绝执行
- `HYPO_TEST_MODE=1` 且检测到本机 `8765` 正在监听时，直接拒绝执行
- 提示语包含：`请先停止生产进程或确认隔离`

目的：

- 在无法信任现场隔离状态时，直接阻断 smoke，避免再出现生产/测试并跑

## 修改文件

- `src/hypo_agent/core/config_loader.py`
- `src/hypo_agent/gateway/app.py`
- `scripts/agent_cli.py`
- `tests/core/test_config_loader.py`
- `tests/gateway/test_app_test_mode.py`
- `tests/scripts/test_agent_cli_smoke_qq.py`

## 验证

### 自动化测试

执行：

```bash
uv run pytest -q \
  tests/core/test_config_loader.py \
  tests/gateway/test_app_test_mode.py \
  tests/gateway/test_main.py \
  tests/gateway/test_channels_status_api.py \
  tests/scripts/test_agent_cli_smoke_qq.py
```

结果：

- `31 passed`

### 命令级验证

执行：

```bash
python -m http.server 8765
HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke
```

结果：

- smoke 在建立到 `8766` 的 WS 之前直接拒绝执行
- 输出：

```text
[ERROR] Detected a listener on port 8765 while running HYPO_TEST_MODE=1 smoke. 请先停止生产进程或确认隔离。
```

### 当前本地数据验证

执行：

```bash
sqlite3 test/sandbox/hypo.db "SELECT COUNT(*) FROM reminders WHERE title LIKE 'm8_smoke%';"
sqlite3 memory/hypo.db "SELECT COUNT(*) FROM reminders WHERE title LIKE 'm8_smoke%';"
```

结果：

- 测试库：`1`
- 生产库：`0`

## 后续建议

- 如果要做“生产进程正常运行 + 同时跑 smoke”的最终验收，建议在一台带真实 `8765` 生产进程的环境里再跑一次，确认 smoke 被门禁直接拒绝，且 QQ 无任何新增消息。
- 如果后续需要支持“测试模式但自定义 sandbox 路径”，统一只通过 `HYPO_TEST_SANDBOX_DIR` 配置，不再允许 `HYPO_MEMORY_DIR` / `HYPO_DB_PATH` 在测试模式下生效。
