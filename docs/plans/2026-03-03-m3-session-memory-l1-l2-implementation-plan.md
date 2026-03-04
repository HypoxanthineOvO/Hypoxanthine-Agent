# M3: 会话记忆（L1 + L2 + WebUI 集成）Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 Hypo-Agent 增加可持续会话上下文（L1 + L2），并打通 WebUI 会话列表/历史加载与会话切换，确保刷新或重启后对话不丢失。

**Architecture:** L1 使用 `SessionMemory`（内存缓冲 + `.jsonl` append-only）维护每个 `session_id` 的最近 N 条消息并支持懒加载；Pipeline 在每次 LLM 调用前注入历史消息、调用后落盘用户与助手消息。L2 使用 `StructuredStore`（`aiosqlite`）维护 `sessions`/`preferences`/`token_usage` 三张表，并通过 `ModelRouter` 的 `model_stream_success` 事件落库 token 用量。Gateway 新增 REST API 读取会话与历史消息；WebUI 增加会话侧边栏、新建/切换会话和历史回填。

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, LiteLLM, aiosqlite, SQLite, pytest, Vue 3, TypeScript, Vitest

---

### Task 1: RED - 定义 L1 会话记忆契约测试（缓冲、序列化、恢复）

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Create: `tests/memory/test_session_memory.py`

**Step 1: 写失败测试，覆盖核心行为**

```python
from datetime import UTC, datetime

from hypo_agent.models import Message
from hypo_agent.memory.session import SessionMemory


def test_session_memory_appends_and_restores_jsonl(tmp_path):
    store = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
    store.append(
        Message(
            text="你好",
            sender="user",
            session_id="main",
            timestamp=datetime(2026, 3, 3, 10, 0, tzinfo=UTC),
        )
    )
    store.append(Message(text="在的", sender="assistant", session_id="main"))

    restored = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
    messages = restored.get_messages("main")

    assert [m.sender for m in messages] == ["user", "assistant"]
    assert messages[0].text == "你好"


def test_session_memory_keeps_only_recent_n_in_buffer(tmp_path):
    store = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=3)
    for i in range(6):
        store.append(Message(text=f"m{i}", sender="user", session_id="s1"))

    recent = store.get_recent_messages("s1")
    assert [m.text for m in recent] == ["m3", "m4", "m5"]


def test_session_memory_lists_sessions_by_updated_at(tmp_path):
    store = SessionMemory(sessions_dir=tmp_path / "sessions", buffer_limit=20)
    store.append(Message(text="a", sender="user", session_id="s1"))
    store.append(Message(text="b", sender="user", session_id="s2"))

    sessions = store.list_sessions()
    assert sessions[0]["session_id"] == "s2"
    assert sessions[1]["session_id"] == "s1"
```

**Step 2: 运行测试确认 RED**

Run: `pytest tests/memory/test_session_memory.py -v`
Expected: FAIL（`hypo_agent.memory.session` 不存在）。

**Step 3: Commit RED**

```bash
git add tests/memory/test_session_memory.py
git commit -m "M3: add failing tests for L1 session memory"
```

### Task 2: GREEN - 实现 L1 会话记忆（内存 + .jsonl）

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Create: `src/hypo_agent/memory/session.py`
- Modify: `src/hypo_agent/memory/__init__.py`

**Step 1: 实现 `SessionMemory` 最小可用版本**

```python
from collections import deque
from pathlib import Path

from hypo_agent.models import Message


class SessionMemory:
    def __init__(self, sessions_dir: Path | str = "memory/sessions", buffer_limit: int = 20):
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.buffer_limit = buffer_limit
        self._buffers: dict[str, deque[Message]] = {}
        self._loaded: set[str] = set()

    def append(self, message: Message) -> None:
        self._ensure_loaded(message.session_id)
        self._append_to_buffer(message)
        with self._session_file(message.session_id).open("a", encoding="utf-8") as f:
            f.write(message.model_dump_json())
            f.write("\n")
```

需要完整实现的方法：
- `_session_file(session_id)`
- `_ensure_loaded(session_id)`（读取 `.jsonl` 并 `Message.model_validate_json`）
- `_append_to_buffer(message)`（基于 `deque(maxlen=buffer_limit)`）
- `get_recent_messages(session_id, limit=None)`
- `get_messages(session_id)`
- `list_sessions()`（按最新时间降序）

**Step 2: 运行 L1 测试确认 GREEN**

Run: `pytest tests/memory/test_session_memory.py -v`
Expected: PASS。

**Step 3: Commit GREEN**

```bash
git add src/hypo_agent/memory/session.py src/hypo_agent/memory/__init__.py
git commit -m "M3: implement L1 session memory with jsonl persistence"
```

### Task 3: RED - 定义 Pipeline 历史注入/写回契约测试

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Modify: `tests/core/test_pipeline.py`

**Step 1: 新增失败测试，覆盖历史注入与 sender->role 映射**

```python
def test_pipeline_injects_recent_history_before_inbound():
    class StubMemory:
        def get_recent_messages(self, session_id, limit=None):
            return [
                Message(text="旧问题", sender="user", session_id=session_id),
                Message(text="旧回答", sender="assistant", session_id=session_id),
                Message(text=None, sender="assistant", session_id=session_id),  # should be filtered
            ]

        def append(self, message):
            pass

    class StubRouter:
        async def call(self, model_name, messages):
            assert messages == [
                {"role": "user", "content": "旧问题"},
                {"role": "assistant", "content": "旧回答"},
                {"role": "user", "content": "新问题"},
            ]
            return "新回答"
```

**Step 2: 新增失败测试，覆盖 `stream_reply` 成功后写入 user/assistant**

```python
def test_pipeline_stream_reply_persists_user_and_assistant_messages():
    ...
    assert memory.appended[0].sender == "user"
    assert memory.appended[1].sender == "assistant"
    assert memory.appended[1].text == "Hello"
```

**Step 3: 运行测试确认 RED**

Run: `pytest tests/core/test_pipeline.py -v`
Expected: FAIL（`ChatPipeline` 还未接入 memory）。

**Step 4: Commit RED**

```bash
git add tests/core/test_pipeline.py
git commit -m "M3: add failing pipeline tests for session history injection"
```

### Task 4: GREEN - 改造 Pipeline 以支持 L1 历史注入与持久化

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Modify: `src/hypo_agent/core/pipeline.py`

**Step 1: 在 `ChatPipeline` 注入会话记忆依赖**

```python
class ChatPipeline:
    def __init__(self, router: ChatModelRouter, chat_model: str, session_memory: SessionMemory, history_window: int = 20):
        self.router = router
        self.chat_model = chat_model
        self.session_memory = session_memory
        self.history_window = history_window
```

**Step 2: 实现历史消息转换规则（仅 text + sender）**

```python
def _to_llm_message(self, message: Message) -> dict[str, str] | None:
    text = (message.text or "").strip()
    if not text:
        return None
    if message.sender == "user":
        role = "user"
    elif message.sender == "assistant":
        role = "assistant"
    else:
        return None
    return {"role": role, "content": text}
```

**Step 3: `run_once` / `stream_reply` 执行前写入 user，执行后写入 assistant**

关键点：
- `self.session_memory.append(inbound)` 在调用 LLM 前执行。
- `stream_reply` 聚合 chunk 到 `full_text`，`assistant_done` 前写入 `Message(text=full_text, sender="assistant")`。
- `llm_messages = history + [current user]`，history 来自 `get_recent_messages(session_id, limit=history_window)`。

**Step 4: 更新 `build_default_pipeline()` 默认参数**

- 默认 `history_window=20`。
- 默认 `SessionMemory("memory/sessions", buffer_limit=20)`。

**Step 5: 运行测试确认 GREEN**

Run: `pytest tests/core/test_pipeline.py -v`
Expected: PASS。

**Step 6: Commit GREEN**

```bash
git add src/hypo_agent/core/pipeline.py
git commit -m "M3: inject session history into chat pipeline"
```

### Task 5: RED - 定义 L2 Structured Store（aiosqlite）契约测试

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Create: `tests/memory/test_structured_store.py`

**Step 1: 编写失败测试，覆盖 schema 与 CRUD/persistence**

```python
import asyncio

from hypo_agent.memory.structured_store import StructuredStore


def test_structured_store_sessions_preferences_and_token_usage(tmp_path):
    db_path = tmp_path / "hypo.db"

    async def _run():
        store = StructuredStore(db_path=db_path)
        await store.init()
        await store.upsert_session("s1")
        await store.set_preference("language", "zh-CN")
        await store.record_token_usage(
            session_id="s1",
            requested_model="Gemini3Pro",
            resolved_model="Gemini3Pro",
            input_tokens=12,
            output_tokens=8,
            total_tokens=20,
        )

        sessions = await store.list_sessions()
        pref = await store.get_preference("language")
        usages = await store.list_token_usage("s1")

        assert sessions[0]["session_id"] == "s1"
        assert pref == "zh-CN"
        assert usages[0]["total_tokens"] == 20

    asyncio.run(_run())
```

再写一条“重建实例后可读到旧数据”的持久化测试。

**Step 2: 运行测试确认 RED**

Run: `pytest tests/memory/test_structured_store.py -v`
Expected: FAIL（模块不存在，且未引入 `aiosqlite`）。

**Step 3: Commit RED**

```bash
git add tests/memory/test_structured_store.py
git commit -m "M3: add failing tests for L2 structured sqlite store"
```

### Task 6: GREEN - 实现 Structured Store 与 SQLite 表结构

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Modify: `pyproject.toml`
- Create: `src/hypo_agent/memory/structured_store.py`
- Modify: `src/hypo_agent/memory/__init__.py`

**Step 1: 引入依赖**

```toml
dependencies = [
  ...,
  "aiosqlite>=0.20.0,<1.0.0"
]
```

**Step 2: 实现 `StructuredStore`（异步）**

```python
class StructuredStore:
    def __init__(self, db_path: Path | str = "memory/hypo.db"):
        self.db_path = Path(db_path)
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def init(self) -> None:
        ...  # CREATE TABLE IF NOT EXISTS sessions/preferences/token_usage

    async def upsert_session(self, session_id: str) -> None: ...
    async def list_sessions(self) -> list[dict[str, Any]]: ...
    async def set_preference(self, key: str, value: str) -> None: ...
    async def get_preference(self, key: str) -> str | None: ...
    async def record_token_usage(...): ...
    async def list_token_usage(self, session_id: str | None = None) -> list[dict[str, Any]]: ...
```

表定义（最低要求）：
- `sessions(session_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)`
- `preferences(pref_key TEXT PRIMARY KEY, pref_value TEXT NOT NULL, updated_at TEXT NOT NULL)`
- `token_usage(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, requested_model TEXT NOT NULL, resolved_model TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER, created_at TEXT NOT NULL)`

**Step 3: 运行 L2 测试确认 GREEN**

Run: `pytest tests/memory/test_structured_store.py -v`
Expected: PASS。

**Step 4: Commit GREEN**

```bash
git add pyproject.toml src/hypo_agent/memory/structured_store.py src/hypo_agent/memory/__init__.py
git commit -m "M3: implement L2 structured sqlite store"
```

### Task 7: RED - 定义 ModelRouter `model_stream_success` 事件回调测试

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Modify: `tests/core/test_model_router.py`

**Step 1: 添加失败测试，要求 stream 成功后触发回调并携带 session_id/token**

```python
def test_model_router_emits_stream_success_event_with_usage(runtime_config):
    emitted = []

    async def on_stream_success(event):
        emitted.append(event)

    async def fake_acompletion(**kwargs):
        async def _gen():
            yield {"choices": [{"delta": {"content": "ok"}}]}
            yield {"choices": [{"delta": {"content": "!"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
        return _gen()

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion, on_stream_success=on_stream_success)
    ... # run stream(session_id="s1")

    assert emitted[0]["event"] == "model_stream_success"
    assert emitted[0]["session_id"] == "s1"
    assert emitted[0]["total_tokens"] == 5
```

**Step 2: 运行测试确认 RED**

Run: `pytest tests/core/test_model_router.py -v`
Expected: FAIL（当前 `ModelRouter` 不支持事件回调或 `session_id` 参数）。

**Step 3: Commit RED**

```bash
git add tests/core/test_model_router.py
git commit -m "M3: add failing tests for model stream success events"
```

### Task 8: GREEN - 在 Router/Pipeline/App 打通 token 统计落库

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Modify: `src/hypo_agent/core/model_router.py`
- Modify: `src/hypo_agent/core/pipeline.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `tests/core/test_pipeline.py`
- Modify: `tests/gateway/test_main.py`

**Step 1: 扩展 `ModelRouter.stream` 签名与成功事件回调**

```python
async def stream(self, model_name: str, messages: list[dict[str, Any]], *, session_id: str | None = None) -> AsyncIterator[str]:
    ...
    payload = {
        "event": "model_stream_success",
        "session_id": session_id,
        "requested_model": model_name,
        "resolved_model": candidate,
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["total_tokens"],
    }
    self.logger.info("model_stream_success", **payload)
    await self._emit_stream_success(payload)
```

**Step 2: Pipeline 调用 `router.stream(..., session_id=inbound.session_id)`**

- 更新 `ChatModelRouter` Protocol。
- 更新测试 stub 的 `stream` 方法签名。

**Step 3: `create_app` 注入 `SessionMemory + StructuredStore` 并绑定回调**

```python
async def on_stream_success(event: dict[str, Any]) -> None:
    session_id = event.get("session_id")
    if not session_id:
        return
    await structured_store.upsert_session(session_id)
    await structured_store.record_token_usage(...)
```

并将实例放到：
- `app.state.session_memory`
- `app.state.structured_store`
- `app.state.pipeline`

**Step 4: 跑测试确认 GREEN**

Run: `pytest tests/core/test_model_router.py tests/core/test_pipeline.py tests/gateway/test_main.py -v`
Expected: PASS。

**Step 5: Commit GREEN**

```bash
git add src/hypo_agent/core/model_router.py src/hypo_agent/core/pipeline.py src/hypo_agent/gateway/app.py tests/core/test_model_router.py tests/core/test_pipeline.py tests/gateway/test_main.py
git commit -m "M3: persist stream token usage from model router events"
```

### Task 9: RED - 定义会话 REST API 契约测试

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Create: `tests/gateway/test_sessions_api.py`

**Step 1: 写失败测试，覆盖两个端点**

```python
from fastapi.testclient import TestClient

from hypo_agent.gateway.app import create_app
from hypo_agent.models import Message


def test_get_sessions_returns_session_list(tmp_path):
    app = create_app(auth_token="t", pipeline=DummyPipeline(), sessions_dir=tmp_path / "sessions", db_path=tmp_path / "hypo.db")
    app.state.session_memory.append(Message(text="hi", sender="user", session_id="s1"))

    with TestClient(app) as client:
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        assert resp.json()[0]["session_id"] == "s1"


def test_get_session_messages_returns_history(tmp_path):
    ...
    resp = client.get("/api/sessions/s1/messages")
    assert len(resp.json()) == 2
```

**Step 2: 运行测试确认 RED**

Run: `pytest tests/gateway/test_sessions_api.py -v`
Expected: FAIL（路由不存在）。

**Step 3: Commit RED**

```bash
git add tests/gateway/test_sessions_api.py
git commit -m "M3: add failing tests for session history REST APIs"
```

### Task 10: GREEN - 实现 `/api/sessions` 与 `/api/sessions/{id}/messages`

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Create: `src/hypo_agent/gateway/sessions_api.py`
- Modify: `src/hypo_agent/gateway/app.py`

**Step 1: 新建 API Router**

```python
router = APIRouter(prefix="/api")

@router.get("/sessions")
async def list_sessions(request: Request):
    memory = request.app.state.session_memory
    return memory.list_sessions()

@router.get("/sessions/{session_id}/messages")
async def list_session_messages(session_id: str, request: Request):
    memory = request.app.state.session_memory
    return [m.model_dump(mode="json") for m in memory.get_messages(session_id)]
```

**Step 2: 在 `create_app` 注册新 router**

```python
app.include_router(ws_router)
app.include_router(sessions_api_router)
```

**Step 3: 运行测试确认 GREEN**

Run: `pytest tests/gateway/test_sessions_api.py tests/gateway/test_ws_echo.py -v`
Expected: PASS。

**Step 4: Commit GREEN**

```bash
git add src/hypo_agent/gateway/sessions_api.py src/hypo_agent/gateway/app.py
git commit -m "M3: add session list and history REST endpoints"
```

### Task 11: RED - 定义 WebUI 会话侧边栏/历史回填测试

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Modify: `web/src/views/__tests__/ChatView.spec.ts`
- Modify: `web/src/composables/__tests__/useChatSocket.spec.ts`

**Step 1: `useChatSocket` 测试先失败：支持动态 session 与消息替换**

```ts
it("uses latest session id when sending", () => {
  const sessionId = ref("s1");
  const socket = useChatSocket({ url, token, sessionId });
  ...
  sessionId.value = "s2";
  socket.sendText("hello");
  expect(JSON.parse(ws.sent[0]).session_id).toBe("s2");
});
```

**Step 2: `ChatView` 测试先失败：页面加载历史 + 侧边栏切换**

```ts
it("loads sessions and message history on mount", async () => {
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => [{ session_id: "s1" }] })
    .mockResolvedValueOnce({ ok: true, json: async () => [{ text: "old", sender: "user", session_id: "s1" }] })
  );
  ...
  expect(wrapper.text()).toContain("old");
});
```

**Step 3: 运行测试确认 RED**

Run: `npm --prefix web run test -- src/composables/__tests__/useChatSocket.spec.ts src/views/__tests__/ChatView.spec.ts`
Expected: FAIL（UI 未实现会话管理）。

**Step 4: Commit RED**

```bash
git add web/src/composables/__tests__/useChatSocket.spec.ts web/src/views/__tests__/ChatView.spec.ts
git commit -m "M3: add failing frontend tests for session sidebar and history"
```

### Task 12: GREEN - 实现 WebUI 会话列表、新建/切换、历史加载

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Modify: `web/src/composables/useChatSocket.ts`
- Modify: `web/src/views/ChatView.vue`
- Modify: `web/src/types/message.ts`
- Modify: `web/src/App.vue`

**Step 1: `useChatSocket` 支持动态 session + 替换历史消息**

- `sessionId` 参数改为 `Ref<string>`。
- `sendText` 使用 `sessionId.value`。
- 忽略非当前会话的 WS 事件。
- 暴露 `replaceMessages(next: Message[])`。

**Step 2: `ChatView` 接入 REST 历史**

- 增加 `apiBase` prop（默认 `VITE_API_BASE` 或由 `wsUrl` 推导）。
- `onMounted` 请求 `/api/sessions`，选中最近会话并加载 `/api/sessions/{id}/messages`。
- 左侧新增会话列表与“新建对话”按钮。
- 切换会话时调用 `replaceMessages` 显示历史。

**Step 3: 保持现有发送/流式体验**

- 连接状态与发送逻辑不变。
- 对当前会话流式 chunk 继续拼接。

**Step 4: 运行前端测试确认 GREEN**

Run: `npm --prefix web run test -- src/composables/__tests__/useChatSocket.spec.ts src/views/__tests__/ChatView.spec.ts`
Expected: PASS。

**Step 5: Commit GREEN**

```bash
git add web/src/composables/useChatSocket.ts web/src/views/ChatView.vue web/src/types/message.ts web/src/App.vue web/src/composables/__tests__/useChatSocket.spec.ts web/src/views/__tests__/ChatView.spec.ts
git commit -m "M3: add webui session switch and history restore"
```

### Task 13: REFACTOR - 统一验证与回归

**Skill refs:** `@superpowers/verification-before-completion`

**Files:**
- Modify (if needed): 当前分支已改文件

**Step 1: 后端全量测试**

Run: `pytest -v`
Expected: PASS。

**Step 2: 前端测试**

Run: `npm --prefix web run test`
Expected: PASS。

**Step 3: 端到端最小手工验证（本地）**

1. 启动服务与 WebUI。  
2. 在 `session-A` 发两轮消息。  
3. 刷新页面，确认 `session-A` 历史仍在。  
4. 新建 `session-B` 并发送消息。  
5. 在侧边栏来回切换，确认两会话历史独立。  
6. 检查 `memory/sessions/*.jsonl` 与 `memory/hypo.db` 有新增记录。

**Step 4: Commit REFACTOR（如有）**

```bash
git add -A
git commit -m "M3: polish memory integration and tests"
```

