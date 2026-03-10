# M2: LLM 基础对话（含流式响应）Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 M1 的 WebSocket echo 闭环升级为真实 LiteLLM 对话链路，支持模型路由、fallback、以及 WebSocket 逐 chunk 流式输出。

**Architecture:** 新增配置加载层，将 `config/models.yaml`（无敏感）与 `config/secrets.yaml`（敏感）合并为可调用的运行时模型配置；`ModelRouter` 负责调用 LiteLLM 并执行 fallback 与 provider 跳过策略；`Pipeline` 负责将单条 `Message` 转换为 LLM 输入并输出完整/流式回复；Gateway `/ws` 改为走 Pipeline 流；前端 composable 将 chunk 事件拼接为单条 assistant 消息实时渲染。

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, LiteLLM async API (`acompletion`), structlog, pytest, Vue 3, TypeScript, Vitest

---

### Task 1: RED - 固化 M2 配置契约与新模型结构

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Modify: `tests/test_models_serialization.py`
- Create: `tests/core/test_config_loader.py`

**Step 1: 在序列化测试里新增 ModelConfig/SecretsConfig 结构断言（先失败）**

```python
from hypo_agent.models import (
    ModelConfig,
    ProviderConfig,
    SecretsConfig,
)


def test_model_config_new_shape_round_trip():
    config = ModelConfig.model_validate(
        {
            "default_model": "Gemini3Pro",
            "task_routing": {"chat": "Gemini3Pro", "lightweight": "DeepseekV3_2"},
            "models": {
                "Gemini3Pro": {
                    "provider": "Hiapi",
                    "litellm_model": "openai/gemini-2.5-pro",
                    "fallback": "DeepseekV3_2",
                },
                "ClaudeSonnet": {
                    "provider": None,
                    "litellm_model": None,
                    "fallback": "Gemini3Pro",
                },
            },
        }
    )

    assert config.task_routing["chat"] == "Gemini3Pro"
    assert config.models["ClaudeSonnet"].provider is None


def test_secrets_config_round_trip():
    secrets = SecretsConfig.model_validate(
        {
            "providers": {
                "Hiapi": {
                    "api_base": "https://hiapi.online/v1",
                    "api_key": "sk-test",
                }
            }
        }
    )

    assert secrets.providers["Hiapi"].api_base.endswith("/v1")
```

**Step 2: 新建配置加载器契约测试（先失败）**

```python
from pathlib import Path

import pytest

from hypo_agent.core.config_loader import load_runtime_model_config


def test_load_runtime_model_config_merges_models_and_secrets(tmp_path: Path):
    models_yaml = tmp_path / "models.yaml"
    models_yaml.write_text(
        """
default_model: Gemini3Pro
task_routing:
  chat: Gemini3Pro
models:
  Gemini3Pro:
    provider: Hiapi
    litellm_model: openai/gemini-2.5-pro
    fallback: DeepseekV3_2
  DeepseekV3_2:
    provider: Volcengine
    litellm_model: openai/ep-20251215171209-4z5qk
    fallback: null
""".strip(),
        encoding="utf-8",
    )

    secrets_yaml = tmp_path / "secrets.yaml"
    secrets_yaml.write_text(
        """
providers:
  Hiapi:
    api_base: https://hiapi.online/v1
    api_key: sk-hiapi
  Volcengine:
    api_base: https://ark.cn-beijing.volces.com/api/v3
    api_key: volc-key
""".strip(),
        encoding="utf-8",
    )

    runtime = load_runtime_model_config(models_yaml, secrets_yaml)

    assert runtime.default_model == "Gemini3Pro"
    assert runtime.models["Gemini3Pro"].api_base == "https://hiapi.online/v1"
    assert runtime.models["DeepseekV3_2"].api_key == "volc-key"


def test_load_runtime_model_config_requires_existing_secrets_file(tmp_path: Path):
    models_yaml = tmp_path / "models.yaml"
    models_yaml.write_text("default_model: Gemini3Pro\ntask_routing: {}\nmodels: {}", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        load_runtime_model_config(models_yaml, tmp_path / "missing-secrets.yaml")
```

**Step 3: 运行 RED 测试**

Run: `pytest tests/test_models_serialization.py tests/core/test_config_loader.py -v`
Expected: FAIL，提示新字段模型或 `hypo_agent.core.config_loader` 尚不存在。

**Step 4: Commit RED**

```bash
git add tests/test_models_serialization.py tests/core/test_config_loader.py
git commit -m "test(config): define m2 model and secrets loading contracts"
```

### Task 2: GREEN - 实现配置模型、配置加载器与配置文件迁移

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Modify: `src/hypo_agent/models.py`
- Create: `src/hypo_agent/core/config_loader.py`
- Modify: `config/models.yaml`
- Create: `config/secrets.yaml.example`
- Create (local only, no commit): `config/secrets.yaml`
- Modify: `.gitignore`

**Step 1: 在 `models.py` 增加新配置模型**

```python
class SingleModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    litellm_model: str | None = None
    fallback: str | None = None


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_model: str
    task_routing: dict[str, str] = Field(default_factory=dict)
    models: dict[str, SingleModelConfig] = Field(default_factory=dict)


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_base: str
    api_key: str


class SecretsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
```

**Step 2: 实现加载器，将 models+secrets 合并为运行时模型配置**

```python
class ResolvedModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None
    litellm_model: str | None
    fallback: str | None
    api_base: str | None = None
    api_key: str | None = None


class RuntimeModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_model: str
    task_routing: dict[str, str]
    models: dict[str, ResolvedModelConfig]


def load_runtime_model_config(
    models_path: Path | str = "config/models.yaml",
    secrets_path: Path | str = "config/secrets.yaml",
) -> RuntimeModelConfig:
    ...
```

合并规则：
- 仅从 `models.yaml` 读取模型拓扑（default/task_routing/models）。
- 对每个 model：
  - 若 `provider is None`，保留 `api_base/api_key=None`。
  - 若有 provider，则从 `secrets.providers[provider]` 注入 `api_base/api_key`。
  - 若 provider 未在 secrets 中出现，抛 `ValueError`（避免静默错配）。

**Step 3: 按 M2 目标更新配置文件**

`config/models.yaml` 更新为：

```yaml
default_model: Gemini3Pro

task_routing:
  chat: Gemini3Pro
  lightweight: DeepseekV3_2

models:
  Gemini3Pro:
    provider: Hiapi
    litellm_model: "openai/gemini-2.5-pro"
    fallback: DeepseekV3_2

  DeepseekV3_1:
    provider: Volcengine
    litellm_model: "openai/ep-20250828163824-jxmm7"
    fallback: DeepseekV3_2

  DeepseekV3_2:
    provider: Volcengine
    litellm_model: "openai/ep-20251215171209-4z5qk"
    fallback: QwenPlus

  QwenPlus:
    provider: Dashscope
    litellm_model: "openai/qwen-plus"
    fallback: null

  ClaudeSonnet:
    provider: null
    litellm_model: null
    fallback: Gemini3Pro

  Gpt4o:
    provider: null
    litellm_model: null
    fallback: Gemini3Pro
```

创建 `config/secrets.yaml.example`：

```yaml
providers:
  Hiapi:
    api_base: "https://hiapi.online/v1"
    api_key: "sk-xxx"

  Volcengine:
    api_base: "https://ark.cn-beijing.volces.com/api/v3"
    api_key: "xxx"

  Dashscope:
    api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: "sk-xxx"
```

创建 `config/secrets.yaml`（本地开发用，占位或真实私钥）并加入 `.gitignore`：

```gitignore
config/secrets.yaml
```

**Step 4: 运行 GREEN 测试**

Run: `pytest tests/test_models_serialization.py tests/core/test_config_loader.py -v`
Expected: PASS。

**Step 5: Commit GREEN（不提交 secrets.yaml）**

```bash
git add src/hypo_agent/models.py src/hypo_agent/core/config_loader.py config/models.yaml config/secrets.yaml.example .gitignore
git commit -m "feat(config): add m2 model schema and secrets merge loader"
```

### Task 3: RED - 定义 ModelRouter 调用、fallback、skip 规则

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Create: `tests/core/test_model_router.py`

**Step 1: 编写 `call()` 相关失败测试**

```python
import asyncio
from types import SimpleNamespace

import pytest

from hypo_agent.core.model_router import ModelRouter


def test_model_router_call_uses_primary_model_first(runtime_config):
    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
        )

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)
    text = asyncio.run(router.call("Gemini3Pro", [{"role": "user", "content": "hi"}]))

    assert text == "hello"
    assert calls[0]["model"] == "openai/gemini-2.5-pro"
```

**Step 2: 编写 fallback + null provider 跳过测试（先失败）**

```python
def test_model_router_fallback_on_failure(runtime_config):
    async def fake_acompletion(**kwargs):
        if kwargs["model"] == "openai/gemini-2.5-pro":
            raise RuntimeError("boom")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="fallback ok"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    router = ModelRouter(runtime_config, acompletion_fn=fake_acompletion)
    text = asyncio.run(router.call("Gemini3Pro", [{"role": "user", "content": "hi"}]))

    assert text == "fallback ok"


def test_model_router_skips_null_provider_model(runtime_config_with_null_head):
    ...
```

**Step 3: 编写 `stream()` 流式输出契约测试（先失败）**

```python
async def fake_stream_acompletion(**kwargs):
    async def _gen():
        yield {"choices": [{"delta": {"content": "He"}}]}
        yield {"choices": [{"delta": {"content": "llo"}}]}
    return _gen()
```

断言：`stream()` 依次产出 `"He"`, `"llo"`。

**Step 4: 运行 RED 测试**

Run: `pytest tests/core/test_model_router.py -v`
Expected: FAIL（`ModelRouter` 尚不存在）。

**Step 5: Commit RED**

```bash
git add tests/core/test_model_router.py
git commit -m "test(router): define litellm call, stream and fallback contracts"
```

### Task 4: GREEN - 实现 `ModelRouter` + token usage 日志

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Create: `src/hypo_agent/core/model_router.py`
- Modify: `src/hypo_agent/core/__init__.py`

**Step 1: 实现 `ModelRouter.call()`**

```python
class ModelRouter:
    def __init__(self, config: RuntimeModelConfig, acompletion_fn=acompletion):
        self.config = config
        self._acompletion = acompletion_fn
        self.logger = structlog.get_logger("hypo_agent.model_router")

    async def call(self, model_name: str, messages: list[dict[str, Any]]) -> str:
        ...  # fallback chain + provider/null skip + usage logging
```

核心行为：
- 先尝试 `model_name`，失败后沿 `fallback` 链继续。
- 对 `provider is None` 或 `litellm_model is None` 节点直接跳过。
- 每次成功调用后输出结构化日志：`input_tokens`, `output_tokens`, `total_tokens`, `resolved_model`。
- 若整条链失败，抛 `RuntimeError` 并带尝试轨迹。

**Step 2: 实现 `ModelRouter.stream()`**

```python
async def stream(self, model_name: str, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
    ...
```

核心行为：
- 使用 `acompletion(..., stream=True)`。
- 解析并 yield chunk 文本。
- 如果主模型在首 chunk 前失败，允许 fallback 到下一个模型。
- 若已开始输出再异常，记录日志并抛出（避免重放混乱）。

**Step 3: 运行 GREEN 测试**

Run: `pytest tests/core/test_model_router.py -v`
Expected: PASS。

**Step 4: Commit GREEN**

```bash
git add src/hypo_agent/core/model_router.py src/hypo_agent/core/__init__.py
git commit -m "feat(router): add litellm model router with fallback and streaming"
```

### Task 5: RED - 定义最小 Pipeline 契约（无历史）

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Create: `tests/core/test_pipeline.py`

**Step 1: 编写 `run_once()` 契约测试（先失败）**

```python
from hypo_agent.models import Message
from hypo_agent.core.pipeline import ChatPipeline


def test_pipeline_calls_router_with_single_user_message():
    class StubRouter:
        async def call(self, model_name, messages):
            assert model_name == "Gemini3Pro"
            assert messages == [{"role": "user", "content": "你好"}]
            return "你好，我在。"

    pipeline = ChatPipeline(router=StubRouter(), chat_model="Gemini3Pro")
    reply = asyncio.run(
        pipeline.run_once(Message(text="你好", sender="user", session_id="s1"))
    )

    assert reply.sender == "assistant"
    assert reply.text == "你好，我在。"
```

**Step 2: 编写 `stream_reply()` 契约测试（先失败）**

断言：pipeline 输出事件格式固定为：

```json
{"type":"assistant_chunk","text":"...","sender":"assistant","session_id":"s1"}
{"type":"assistant_done","sender":"assistant","session_id":"s1"}
```

**Step 3: 运行 RED 测试**

Run: `pytest tests/core/test_pipeline.py -v`
Expected: FAIL（`pipeline.py` 尚不存在）。

**Step 4: Commit RED**

```bash
git add tests/core/test_pipeline.py
git commit -m "test(pipeline): define minimal chat and stream event contracts"
```

### Task 6: GREEN - 实现 Pipeline 并接入 Gateway `/ws`

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Create: `src/hypo_agent/core/pipeline.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/gateway/ws.py`
- Modify: `tests/gateway/test_ws_echo.py`
- Modify: `tests/gateway/test_main.py`

**Step 1: 实现 `ChatPipeline`**

```python
class ChatPipeline:
    def __init__(self, router: ModelRouter, chat_model: str):
        self.router = router
        self.chat_model = chat_model

    async def run_once(self, inbound: Message) -> Message:
        ...

    async def stream_reply(self, inbound: Message) -> AsyncIterator[dict[str, Any]]:
        ...
```

约束：
- M2 不注入历史，LLM 输入始终仅当前用户消息。
- 空文本输入抛 `ValueError`。

**Step 2: 在 app 启动时构建默认 pipeline（可注入 mock）**

```python
def create_app(auth_token: str, pipeline: ChatPipeline | None = None) -> FastAPI:
    app = FastAPI(...)
    app.state.pipeline = pipeline or build_default_pipeline()
```

`build_default_pipeline()` 读取：
- `config/models.yaml`
- `config/secrets.yaml`
并组装 `ModelRouter + ChatPipeline`。

**Step 3: 替换 ws echo 为流式 pipeline 调用**

```python
@router.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    ...
    inbound = Message.model_validate(payload)
    pipeline = ws.app.state.pipeline
    async for event in pipeline.stream_reply(inbound):
        await ws.send_json(event)
```

**Step 4: 更新 gateway 测试到新协议**

- `tests/gateway/test_ws_echo.py` 改为：
  - token 校验保持不变。
  - 有效消息时接收 `assistant_chunk` + `assistant_done`。
  - 无效消息仍 4400。
- `tests/gateway/test_main.py`：`create_app` 调用参数变化时同步断言。

**Step 5: 运行 GREEN 测试**

Run: `pytest tests/core/test_pipeline.py tests/gateway/test_ws_echo.py tests/gateway/test_main.py -v`
Expected: PASS。

**Step 6: Commit GREEN**

```bash
git add src/hypo_agent/core/pipeline.py src/hypo_agent/gateway/app.py src/hypo_agent/gateway/ws.py tests/core/test_pipeline.py tests/gateway/test_ws_echo.py tests/gateway/test_main.py
git commit -m "feat(gateway): replace echo websocket with streaming chat pipeline"
```

### Task 7: RED - 定义前端 chunk 拼接行为

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Modify: `web/src/composables/__tests__/useChatSocket.spec.ts`
- Modify: `web/src/views/__tests__/ChatView.spec.ts`

**Step 1: 新增 composable 失败测试**

```ts
it("merges assistant chunks into one message", () => {
  const socket = useChatSocket({ ... });
  socket.connect();
  const ws = MockWebSocket.instances[0]!;
  ws.emitOpen();

  ws.emitMessage(
    JSON.stringify({
      type: "assistant_chunk",
      text: "你",
      sender: "assistant",
      session_id: "session-1",
    }),
  );
  ws.emitMessage(
    JSON.stringify({
      type: "assistant_chunk",
      text: "好",
      sender: "assistant",
      session_id: "session-1",
    }),
  );
  ws.emitMessage(
    JSON.stringify({
      type: "assistant_done",
      sender: "assistant",
      session_id: "session-1",
    }),
  );

  expect(socket.messages.value.at(-1)?.text).toBe("你好");
});
```

**Step 2: 更新 ChatView 失败测试以发送 chunk 协议**

断言 markdown 仍可正确渲染拼接后的文本。

**Step 3: 运行 RED 测试**

Run: `npm --prefix web run test -- web/src/composables/__tests__/useChatSocket.spec.ts web/src/views/__tests__/ChatView.spec.ts`
Expected: FAIL（现有逻辑把 chunk 当独立消息 push）。

**Step 4: Commit RED**

```bash
git add web/src/composables/__tests__/useChatSocket.spec.ts web/src/views/__tests__/ChatView.spec.ts
git commit -m "test(web): define assistant stream chunk merge behavior"
```

### Task 8: GREEN - 实现前端流式拼接与协议类型

**Skill refs:** `@superpowers/test-driven-development`

**Files:**
- Modify: `web/src/composables/useChatSocket.ts`
- Modify: `web/src/types/message.ts`
- Modify: `web/src/views/ChatView.vue`

**Step 1: 扩展 ws inbound 事件类型**

```ts
export type IncomingWsEvent =
  | {
      type: "assistant_chunk";
      text: string;
      sender: "assistant";
      session_id: string;
    }
  | {
      type: "assistant_done";
      sender: "assistant";
      session_id: string;
    };
```

**Step 2: 在 `useChatSocket` 中实现 chunk 聚合状态**

```ts
let streamingAssistantIndex: number | null = null;
```

规则：
- 收到 `assistant_chunk`：
  - 若当前无活动 chunk，push 一条新 assistant 消息并记录 index。
  - 若有活动 chunk，追加文本到该消息。
- 收到 `assistant_done`：清空活动 index。
- 兼容完整 `Message` 事件（用于回归安全）。

**Step 3: 视图文案从 Echo 调整为 LLM Streaming（可选但建议）**

将标题 `Gateway Echo Console` 调整为 `Gateway LLM Console`，避免误导。

**Step 4: 运行 GREEN 测试**

Run: `npm --prefix web run test -- web/src/composables/__tests__/useChatSocket.spec.ts web/src/views/__tests__/ChatView.spec.ts`
Expected: PASS。

**Step 5: Commit GREEN**

```bash
git add web/src/composables/useChatSocket.ts web/src/types/message.ts web/src/views/ChatView.vue
git commit -m "feat(web): render assistant responses in streaming chunks"
```

### Task 9: REFACTOR + 全量验证

**Skill refs:** `@superpowers/verification-before-completion`

**Files:**
- Modify: `tests/gateway/test_settings.py`
- Modify: `src/hypo_agent/gateway/settings.py`
- Optional Modify: `docs/runbooks/*`（如需补充 secrets 配置说明）

**Step 1: 清理旧字段命名兼容（`task_type_to_model` -> `task_routing`）**

将仍依赖旧字段的读取逻辑统一迁移，避免 M2/M1 混用。

**Step 2: 添加配置错误路径测试（缺 provider secret、fallback 环）**

Run: `pytest tests/core/test_config_loader.py tests/core/test_model_router.py -v`
Expected: PASS 且覆盖错误分支。

**Step 3: 执行后端全量测试**

Run: `pytest -q`
Expected: 全部 PASS（在当前基线上应 >= 14，并新增 M2 用例）。

**Step 4: 执行前端全量测试**

Run: `npm --prefix web run test`
Expected: PASS（在当前基线上应 >= 4，并新增 M2 用例）。

**Step 5: 手工冒烟（可选但建议）**

Run:

```bash
python -m hypo_agent.gateway.main
npm --prefix web run dev
```

检查：
- WebSocket 连接成功。
- 输入一条文本后，assistant 气泡逐步增长（chunk 渲染）。

**Step 6: Commit REFACTOR**

```bash
git add -A
git commit -m "refactor(m2): finalize llm streaming chat path and config hardening"
```
