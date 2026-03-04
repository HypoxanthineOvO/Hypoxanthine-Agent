# M4: Skill 系统基础 + TmuxSkill + CodeRunSkill Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在保持现有 WebSocket 流式对话的前提下，为 Hypo-Agent 增加可配置 Skill 基础设施、三层熔断、以及可执行终端命令/代码的 `TmuxSkill` 与 `CodeRunSkill`，并完成 ReAct 工具调用闭环。

**Architecture:** 新增 `BaseSkill` + `SkillManager` 作为工具注册/发现/分发层，统一向 LLM 暴露 OpenAI tools schema；`CircuitBreaker` 作为 Skill 执行外层保护（工具级、会话级、全局 Kill Switch），由 `SkillManager.invoke()` 统一接入 `can_execute` 与 `record_success/failure`；`ChatPipeline` 扩展为最多 5 轮 ReAct（LLM 申请工具 -> Skill 执行 -> 结果回填），继续通过 WebSocket 发送 `assistant_chunk/assistant_done`，并在工具执行期间发送 `tool_call_start/tool_call_result`。

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, LiteLLM, tmux CLI, asyncio, pytest

---

### 关键设计决策（审阅确认版）

1. CircuitBreaker 集成采用 **方案 A**：`SkillManager` 持有 `CircuitBreaker`，`invoke()` 内部负责 `can_execute` + `record_success/failure`，`Pipeline` 不直接操作熔断状态。
2. 依赖注入沿用 M3 模式：扩展 `AppDeps`，新增 `skill_manager` 与 `circuit_breaker` 字段；`create_app()` 只消费 `AppDeps` 并挂入 `app.state`。
3. `TmuxSkill` 自动化测试分层：`pytest` 单元测试统一 mock `asyncio.create_subprocess_exec`，不依赖真实 tmux；真实 tmux 仅作为本地手动集成检查点。

---

### Task 1: RED - 定义 BaseSkill / SkillManager 契约测试

**Files:**
- Create: `tests/skills/test_skill_manager.py`
- Modify: `tests/test_models_serialization.py`

**Step 1: 写失败测试（Skill 注册、tools 汇聚、调用分发、SkillOutput 校验）**

```python
from hypo_agent.core.skill_manager import SkillManager
from hypo_agent.models import SkillOutput
from hypo_agent.skills.base import BaseSkill


class EchoSkill(BaseSkill):
    name = "echo"
    description = "echo skill"
    required_permissions = []

    @property
    def tools(self):
        return [{"type": "function", "function": {"name": "echo", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}}]

    async def execute(self, tool_name, params):
        return SkillOutput(status="success", result={"echo": params["text"]})


def test_skill_manager_registers_and_dispatches():
    mgr = SkillManager()
    mgr.register(EchoSkill())

    tools = mgr.get_tools_schema()
    assert tools[0]["function"]["name"] == "echo"
```

**Step 2: 运行测试确认 RED**

Run: `pytest tests/skills/test_skill_manager.py tests/test_models_serialization.py -v`
Expected: FAIL（`skill_manager` / `base` 尚未实现）。

**Step 3: Commit RED**

```bash
git add tests/skills/test_skill_manager.py tests/test_models_serialization.py
git commit -m "M4: add failing tests for base skill and manager"
```

### Task 2: GREEN - 实现 BaseSkill / SkillManager + skills.yaml 启用加载

**Files:**
- Create: `src/hypo_agent/skills/base.py`
- Create: `src/hypo_agent/core/skill_manager.py`
- Modify: `src/hypo_agent/skills/__init__.py`
- Modify: `config/skills.yaml`

**Step 1: 实现 `BaseSkill` 抽象类（统一 execute 契约）**

```python
class BaseSkill(ABC):
    name: str
    description: str
    required_permissions: list[str]

    @property
    @abstractmethod
    def tools(self) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    async def execute(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> SkillOutput:
        ...
```

**Step 2: 实现 `SkillManager` 最小闭环**

关键能力：
- `register(skill: BaseSkill)`
- `register_many(skills: list[BaseSkill])`
- `get_tools_schema() -> list[dict[str, Any]]`
- `find_enabled_skills(path="config/skills.yaml") -> set[str]`
- `invoke(tool_name, params, session_id=None) -> SkillOutput`

说明：
- 先实现无熔断最小调用链（T2）；熔断接线在 T4 完成并补测试，避免跨任务前置依赖。

**Step 3: 在 `skills.yaml` 增加 M4 目标技能开关（仅配置，不做权限）**

```yaml
default_timeout_seconds: 30
skills:
  tmux:
    enabled: true
    timeout_seconds: 30
  code_run:
    enabled: true
    timeout_seconds: 30
```

**Step 4: 运行测试确认 GREEN**

Run: `pytest tests/skills/test_skill_manager.py tests/test_models_serialization.py -v`
Expected: PASS。

**Step 5: Commit GREEN**

```bash
git add src/hypo_agent/skills/base.py src/hypo_agent/core/skill_manager.py src/hypo_agent/skills/__init__.py config/skills.yaml
git commit -m "M4: implement base skill and skill manager"
```

### Task 3: RED - 定义 Circuit Breaker 三层熔断契约测试

**Files:**
- Create: `tests/security/test_circuit_breaker.py`
- Create: `tests/gateway/test_kill_switch_api.py`

**Step 1: 写失败测试（工具级/会话级/全局开关与恢复）**

```python
def test_tool_level_breaker_opens_after_3_failures():
    cb = CircuitBreaker(config, now_fn=fake_now)
    cb.record_failure(tool_name="run_command", session_id="s1")
    cb.record_failure(tool_name="run_command", session_id="s1")
    cb.record_failure(tool_name="run_command", session_id="s1")
    assert cb.is_tool_blocked("run_command") is True
```

补充用例：
- `cooldown_seconds` 到期后自动恢复。
- 同 session 累计失败 5 次后 `is_session_blocked(session_id)=True`。
- `set_global_kill_switch(True)` 后 `can_execute(...)` 立即拒绝。
- `POST /api/kill-switch` 可开关，且返回当前状态。

**Step 2: 运行测试确认 RED**

Run: `pytest tests/security/test_circuit_breaker.py tests/gateway/test_kill_switch_api.py -v`
Expected: FAIL（`circuit_breaker.py` 与接口不存在）。

**Step 3: Commit RED**

```bash
git add tests/security/test_circuit_breaker.py tests/gateway/test_kill_switch_api.py
git commit -m "M4: add failing tests for circuit breaker and kill switch api"
```

### Task 4: GREEN - 实现 CircuitBreaker + Kill Switch API

**Files:**
- Create: `src/hypo_agent/security/circuit_breaker.py`
- Modify: `src/hypo_agent/security/__init__.py`
- Create: `src/hypo_agent/gateway/kill_switch_api.py`
- Modify: `src/hypo_agent/core/skill_manager.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/gateway/main.py`
- Modify: `tests/gateway/test_main.py`
- Modify: `tests/skills/test_skill_manager.py`

**Step 1: 实现熔断状态机**

核心接口：
- `can_execute(tool_name, session_id) -> tuple[bool, str]`
- `record_success(tool_name, session_id)`
- `record_failure(tool_name, session_id)`
- `set_global_kill_switch(enabled: bool)` / `get_global_kill_switch()`

行为：
- 工具级：连续失败达到 `tool_level_max_failures` 进入 cooldown。
- 会话级：累计失败达到 `session_level_max_failures` 进入 cooldown。
- 全局级：开启后直接拒绝所有调用。

**Step 2: 挂载 Kill Switch API**

`POST /api/kill-switch` 请求体：

```json
{"enabled": true}
```

响应体：

```json
{"enabled": true}
```

并通过 `app.state.deps.circuit_breaker` 暴露给 SkillManager 与 Kill Switch API 共用。

同时在本任务完成 `SkillManager` 熔断接线（方案 A）：
- `SkillManager.__init__(..., circuit_breaker: CircuitBreaker | None = None)`
- `invoke()` 前执行 `can_execute(tool_name, session_id)`，拒绝时返回 `SkillOutput(status="error", error_info=...)`
- 调用成功后 `record_success(...)`，失败后 `record_failure(...)`
- 在 `tests/skills/test_skill_manager.py` 增加熔断拦截与计数测试

**Step 3: 运行测试确认 GREEN**

Run: `pytest tests/security/test_circuit_breaker.py tests/gateway/test_kill_switch_api.py tests/gateway/test_main.py tests/skills/test_skill_manager.py -v`
Expected: PASS。

**Step 4: Commit GREEN**

```bash
git add src/hypo_agent/security/circuit_breaker.py src/hypo_agent/security/__init__.py src/hypo_agent/core/skill_manager.py src/hypo_agent/gateway/kill_switch_api.py src/hypo_agent/gateway/app.py src/hypo_agent/gateway/main.py tests/gateway/test_main.py tests/skills/test_skill_manager.py
git commit -m "M4: implement circuit breaker and kill switch api"
```

### Task 5: RED - 定义 TmuxSkill 契约测试（执行、超时、截断）

**Files:**
- Create: `tests/skills/test_tmux_skill.py`

**Step 1: 写失败测试**

覆盖：
- `run_command("echo hi")` 返回 `SkillOutput(status="success")` 且包含 stdout。
- 指定 `timeout=1` 时长命令返回 `status="timeout"`。
- 超长输出（>8000）被截断并带提示（metadata 或 result 文本里带 `truncated` 标记）。
- 所有上述用例统一 mock `asyncio.create_subprocess_exec`，断言命令拼接与超时/截断处理逻辑。

**Step 2: 运行测试确认 RED**

Run: `pytest tests/skills/test_tmux_skill.py -v`
Expected: FAIL（`TmuxSkill` 尚未实现）。

**Step 3: Commit RED**

```bash
git add tests/skills/test_tmux_skill.py
git commit -m "M4: add failing tests for tmux skill"
```

### Task 6: GREEN - 实现 TmuxSkill

**Files:**
- Create: `src/hypo_agent/skills/tmux_skill.py`
- Modify: `src/hypo_agent/skills/__init__.py`

**Step 1: 实现 `tools` schema 与 `execute` 分发**

`tools` 暴露函数：
- `run_command(command, session_name?, timeout?)`

**Step 2: 在 tmux session 执行命令并采集 stdout/stderr**

实现建议：
- 若 session 不存在，自动 `tmux new-session -d -s <session_name>`。
- 每次命令在新 window/pane 运行，stdout/stderr 落到临时文件，再读取返回。
- 默认 timeout=30 秒，超时后返回 `SkillOutput(status="timeout")`。
- 将 subprocess 调用封装为可替换执行器（默认 `asyncio.create_subprocess_exec`），便于单测 mock。

**Step 3: 输出保护**

- 常量 `MAX_OUTPUT_CHARS = 8000`。
- 超过阈值时截断并在结果中附加 `"[truncated ...]"` + `metadata["truncated"]=True`。

**Step 4: 运行测试确认 GREEN（无 tmux 依赖）**

Run: `pytest tests/skills/test_tmux_skill.py -v`
Expected: PASS。

**Step 5: 手动集成检查（本地可选）**

Run: `tmux -V && pytest tests/skills/test_tmux_skill.py -k manual --run-manual -v`
Expected: 在安装 tmux 的环境中通过人工检查用例；CI 默认不执行。

**Step 6: Commit GREEN**

```bash
git add src/hypo_agent/skills/tmux_skill.py src/hypo_agent/skills/__init__.py
git commit -m "M4: implement tmux skill with timeout and truncation"
```

### Task 7: RED - 定义 CodeRunSkill 契约测试（Python/Shell、沙箱、复用 Tmux）

**Files:**
- Create: `tests/skills/test_code_run_skill.py`

**Step 1: 写失败测试**

覆盖：
- `run_code("print('ok')", "python")` 返回 stdout 含 `ok`。
- `run_code("echo ok", "shell")` 返回 stdout 含 `ok`。
- 非法 language 返回 `SkillOutput(status="error")`。
- 代码文件写入 `/tmp/hypo-agent-sandbox/`。
- 超时/截断行为透传自 TmuxSkill。
- 单元测试使用 stub `TmuxSkill`（或 mock `run_command`），不依赖真实 tmux。

**Step 2: 运行测试确认 RED**

Run: `pytest tests/skills/test_code_run_skill.py -v`
Expected: FAIL（`CodeRunSkill` 尚未实现）。

**Step 3: Commit RED**

```bash
git add tests/skills/test_code_run_skill.py
git commit -m "M4: add failing tests for code run skill"
```

### Task 8: GREEN - 实现 CodeRunSkill（临时文件 + TmuxSkill 执行）

**Files:**
- Create: `src/hypo_agent/skills/code_run_skill.py`
- Modify: `src/hypo_agent/skills/__init__.py`

**Step 1: 实现 `tools` schema 与 execute**

`tools` 暴露函数：
- `run_code(code, language?)`（language 默认 `python`）

**Step 2: 实现文件落地与命令映射**

- 沙箱目录固定：`/tmp/hypo-agent-sandbox/`
- `python` -> `python <temp_file>.py`
- `shell` -> `bash <temp_file>.sh`
- 通过注入/组合 `TmuxSkill` 调用 `run_command`。

**Step 3: 运行测试确认 GREEN**

Run: `pytest tests/skills/test_code_run_skill.py tests/skills/test_tmux_skill.py -v`
Expected: PASS。

**Step 4: Commit GREEN**

```bash
git add src/hypo_agent/skills/code_run_skill.py src/hypo_agent/skills/__init__.py
git commit -m "M4: implement code run skill using tmux backend"
```

### Task 9: RED - 定义 ModelRouter tools 参数 + Pipeline ReAct 测试

**Files:**
- Modify: `tests/core/test_model_router.py`
- Create: `tests/core/test_pipeline_tools.py`
- Modify: `tests/gateway/test_ws_echo.py`

**Step 1: 在 ModelRouter 测试增加 tools 透传断言**

```python
async def fake_acompletion(**kwargs):
    assert "tools" in kwargs
    assert kwargs["tools"][0]["function"]["name"] == "run_command"
```

分别覆盖：
- `call(..., tools=...)` 透传。
- `stream(..., tools=...)` 透传。

**Step 2: 新增 Pipeline ReAct 失败测试**

覆盖：
- 单轮：LLM 直接回答（无工具）。
- 多轮：先工具调用，再回填结果后最终回答。
- 触发熔断：工具调用被拒绝并产出错误事件。
- 最大轮次：达到 `max_react_rounds=5` 后停止并返回安全兜底。

**Step 3: WebSocket 事件透传测试补充**

- 当 Pipeline 产生 `tool_call_start/tool_call_result` 时，`ws.py` 原样转发。

**Step 4: 运行测试确认 RED**

Run: `pytest tests/core/test_model_router.py tests/core/test_pipeline_tools.py tests/gateway/test_ws_echo.py -v`
Expected: FAIL（Router/Pipeline 尚未支持 tools + ReAct）。

**Step 5: Commit RED**

```bash
git add tests/core/test_model_router.py tests/core/test_pipeline_tools.py tests/gateway/test_ws_echo.py
git commit -m "M4: add failing tests for react loop and tool calling"
```

### Task 10: GREEN - 集成 ReAct 工具调用闭环（Router + Pipeline + App Wiring）

**Files:**
- Modify: `src/hypo_agent/core/model_router.py`
- Modify: `src/hypo_agent/core/pipeline.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `src/hypo_agent/gateway/ws.py`
- Modify: `src/hypo_agent/core/__init__.py`（若存在导出需求）

**Step 1: 扩展 `ModelRouter.stream` / `call` 接口**

最小改动目标：
- 两者都支持 `tools: list[dict[str, Any]] | None = None`。
- 向 `litellm.acompletion` 透传 `tools`。
- 保持原有 fallback/日志行为。

**Step 2: 在 `ChatPipeline` 实现 ReAct 循环**

新增依赖与参数：
- `skill_manager: SkillManager`
- `max_react_rounds: int = 5`

循环逻辑：
1. 请求 LLM（携带 tools schema）。
2. 若返回工具调用：
- `yield {"type": "tool_call_start", ...}`
- `SkillManager.invoke(...)`
- `yield {"type": "tool_call_result", ...}`
- 将 tool result 作为 `tool` 消息回填再进下一轮。
3. 若返回最终文本：流式输出 `assistant_chunk`，结束时发 `assistant_done`。
4. 超过最大轮次：返回安全提示并结束。

**Step 3: 在 App 层完成依赖注入**

- 扩展 `AppDeps`：新增 `skill_manager` 与 `circuit_breaker` 字段。
- `_build_default_deps()` 统一创建 `SessionMemory`、`StructuredStore`、`CircuitBreaker`、`SkillManager`。
- 启动时通过 `AppDeps` 注入 `CircuitBreaker` + `SkillManager`。
- 按 `skills.yaml` 仅注册启用技能（M4 至少 `tmux`、`code_run`）。
- `create_app()` 将 `deps` 挂到 `app.state`，供 ws 与 kill-switch API 统一访问，避免分散状态注入。

**Step 4: 运行测试确认 GREEN**

Run: `pytest tests/core/test_model_router.py tests/core/test_pipeline.py tests/core/test_pipeline_tools.py tests/gateway/test_ws_echo.py -v`
Expected: PASS。

**Step 5: Commit GREEN**

```bash
git add src/hypo_agent/core/model_router.py src/hypo_agent/core/pipeline.py src/hypo_agent/gateway/app.py src/hypo_agent/gateway/ws.py
git commit -m "M4: integrate react tool calling into pipeline"
```

### Task 11: REFACTOR + 全量验证 + 里程碑收口

**Files:**
- Modify: `docs/architecture.md`（如需同步 M4 实现细节）
- Modify: `docs/runbooks/`（如需新增 kill-switch/tmux 运维说明）

**Step 1: 清理重复逻辑与命名统一**

- 统一 tool event payload 字段（`tool_name`, `tool_call_id`, `status`, `session_id`）。
- 收敛超时/错误文本常量，避免散落字符串。

**Step 2: 运行全量后端测试**

Run: `pytest -q`
Expected: 全量 PASS（在当前基线上应 >= 40 + M4 新增测试）。

**Step 3: 运行前端测试（回归）**

Run: `cd web && npm run test -- --run`
Expected: PASS（现有 8+ 测试无回归）。

**Step 4: Commit REFACTOR**

```bash
git add src tests
git commit -m "M4: refactor tool calling flow and stabilize tests"
```

**Step 5: 文档单独提交（遵循仓库约定）**

```bash
git add docs/architecture.md docs/runbooks
git commit -m "M4[doc]: document skill system and circuit breaker operations"
```
