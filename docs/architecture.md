# 3. 架构设计

### 3.0 架构总览

Hypo-Agent 参考 OpenClaw 的单网关模式，但做了以下简化与增强：

- 简化鉴权（单用户，无复杂设备配对）
- 增强记忆（三层分层 + 用户可编辑）
- 增加多模型路由器（配置驱动的任务-模型映射）
- 增加分层熔断机制
- 技术栈从 Node.js 转为 Python (FastAPI)

```mermaid
graph TD
    subgraph WebUI["WebUI (PWA)"]
        Chat["Chat View"]
        Dash["Dashboard"]
        CfgUI["Config Editor"]
        MemUI["Memory Editor"]
    end

    subgraph HypoAgent["Hypo-Agent Process (Python / FastAPI)"]
        GW["Gateway<br>(FastAPI WebSocket)"]
        Adapter["Channel Adapter Interface"]
        Auth["Auth (Token-based)"]
        MsgQueue["Message Queue<br>(Async Queue)"]

        subgraph Core["Agent Core"]
            Pipeline["Message Pipeline"]
            Router["Model Router<br>(Config-driven)"]
            SkillMgr["Skill Manager"]
            CB["Circuit Breaker<br>(Tool / Session / Global)"]
        end

        subgraph Models["LLM Providers"]
            Cheap["Qwen / DeepSeek<br>(Preprocessing)"]
            Mid["Gemini Pro<br>(Chat)"]
            Strong["Claude<br>(Reasoning)"]
            Code["Claude Sonnet/Opus<br>(Coding)"]
        end

        subgraph Memory["Memory System"]
            L1["L1: Session Context<br>(In-memory + .jsonl)"]
            L2["L2: Structured Store<br>(SQLite)"]
            L3["L3: Semantic Memory<br>(Markdown + Vector DB)"]
        end

        subgraph Scheduler["Scheduler"]
            Cron["APScheduler<br>(Cron / Interval)"]
            TaskList["Task Config<br>(YAML)"]
        end

        Logger["Structured Logger<br>+ Token Tracker"]
    end

    subgraph Skills["Skill Modules"]
        TmuxSkill["TmuxSkill<br>(libtmux)"]
        ReminderSkill["ReminderSkill"]
        FSSkill["FileSystemSkill<br>(Sandboxed)"]
        CodingBridge["CodingBridgeSkill<br>(Future)"]
        NotionSkill["NotionSkill<br>(Future)"]
    end

    WebUI <-->|WebSocket| GW
    GW <--> Auth
    GW --> Adapter
    Adapter --> MsgQueue
    MsgQueue --> Pipeline

    Pipeline <--> Router
    Router --> Models
    Pipeline <--> SkillMgr
    Pipeline <--> CB

    Pipeline <--> L1
    Pipeline <--> L2
    Pipeline <--> L3

    Cron -->|Trigger| MsgQueue
    Cron --- TaskList

    SkillMgr --> Skills
    Pipeline --> Logger
```

### 3.1 Gateway 层

| 属性 | 设计 |
| --- | --- |
| 服务器 | FastAPI + WebSocket，长驻进程运行在 Genesis |
| 鉴权 | 简化为 Token-based Auth（单用户无需设备配对） |
| 消息格式 | 统一的内部消息结构（含 text / image / file / audio 独立字段） |
| 适配器 | 抽象 `ChannelAdapter` 接口，V1 实现 `WebUIAdapter`，后续可插入 `FeishuAdapter` / `QQAdapter` |

**相对 OpenClaw 的简化**：去掉了复杂的设备配对、challenge 签名、TypeBox 验证等重量机制。单用户场景下，一个 Token 足以保障安全。

### 3.2 Agent Core

Agent Core 是整个系统的“大脑”，包含四个核心组件：

**① Message Pipeline（消息管线）**

一条消息的完整流转：

```mermaid
graph LR
    A["Receive"] --> B["Preprocess<br>(Cheap Model)"]
    B --> C["Memory Injection<br>(Retrieve Context)"]
    C --> D["Model Router<br>(Select Model)"]
    D --> E["LLM Reasoning"]
    E --> F{"Need Tool?"}
    F -->|Yes| G["Skill Execution<br>(via Skill Manager)"]
    G --> H["Result Assembly"]
    F -->|No| H
    H --> I["Response + Log"]
```

- **Preprocess**：用廉价模型对消息进行意图分类、提取关键实体、判断是否需要调用工具。
- **Memory Injection**：根据当前上下文从三层记忆中检索相关信息，注入到 Prompt 中。
- **LLM Reasoning**：由 Model Router 选定的模型执行推理。
- **Skill Execution**：如果 LLM 返回工具调用请求，通过 Skill Manager 执行，结果回馈给 LLM 继续推理（标准的 ReAct 循环）。

**② Model Router（多模型路由器）**

- 配置文件驱动（`models.yaml`），定义 `task_type → model` 的映射规则。
- Pipeline 的 Preprocess 阶段识别出任务类型后，Router 自动选择对应模型。
- 提供 `/model status` 指令查看当前模型分配与用量。
- **相对 OpenClaw 的增强**：OpenClaw 通常只配置单一模型，Hypo-Agent 的多模型路由是核心差异化特性。

**③ Skill Manager（技能管理器）**

- 维护已注册的 Skill 列表，负责 Skill 的加载、权限校验和调用分发。
- 每个 Skill 继承 `BaseSkill` 接口，声明自己的名称、描述、所需权限和可用工具列表。
- LLM 的工具调用请求统一经过 Skill Manager 分发，且受 Circuit Breaker 监控。

**④ Circuit Breaker（分层熔断器）**

- 包裹在 Skill Manager 外层，监控每次工具/Skill 调用。
- 三层逻辑：工具级（3次失败禁用）→ 会话级（累计 5 次暂停）→ 全局 Kill Switch。
- **相对 OpenClaw 的增强**：OpenClaw 无内置熔断机制，这是 Hypo-Agent 的安全红线。

### 3.3 三层记忆系统

```mermaid
graph TD
    subgraph L1["L1: Session Context"]
        L1A["In-memory conversation buffer"]
        L1B[".jsonl session files"]
    end
    subgraph L2["L2: Structured Store"]
        L2A["SQLite DB"]
        L2B["Task records, preferences,<br>skill registry, token stats"]
    end
    subgraph L3["L3: Semantic Memory"]
        L3A["Markdown Files<br>(Persona, lessons, env overview)"]
        L3B["Vector DB<br>(ChromaDB / sqlite-vec)"]
    end

    Pipeline["Agent Pipeline"] --> L1
    Pipeline --> L2
    Pipeline -->|Semantic Search| L3
    L3A -->|Embed & Index| L3B

    WebUI["WebUI Memory Editor"] -.->|Read/Write| L1B
    WebUI -.->|Read/Write| L2A
    WebUI -.->|Read/Write| L3A
```

| 层级 | 存储 | 内容 | 检索方式 |
| --- | --- | --- | --- |
| L1 会话 | 内存 + .json | 当前对话上下文 | 直接读取 |
| L2 结构化 | SQLite | 任务记录、偏好 KV、Token 统计、Skill 注册表 | SQL 查询 |
| L3 语义 | Markdown + 向量 DB | 人设、经验教训、环境概述、历史摘要 | 向量相似度 + 关键字 |

**所有层级的数据均可通过 WebUI 或直接编辑文件进行修改。**

**相对 OpenClaw 的增强**：OpenClaw 为双层（.jsonl + SQLite/Markdown），Hypo-Agent 拆分为三层，将结构化数据（SQLite）和语义记忆（Markdown + Vector）明确分离，职责更清晰。

### 3.4 Scheduler（定时调度器）

- 基于 APScheduler，支持 Cron 表达式和固定间隔触发。
- 定时任务配置在 `tasks.yaml` 中，用户可通过 WebUI 编辑。
- 触发时将任务以内部消息格式插入 Message Queue，与用户消息使用同一套 Pipeline 处理。
- **V1 采用串行队列**：Heartbeat 任务与用户对话排队执行，避免并发复杂性。

**相对 OpenClaw 的简化**：OpenClaw 支持 Webhook、邮件触发、语音唤醒等多种激活方式，Hypo-Agent V1 仅保留 Cron 触发，保持简单。

### 3.5 Skill 系统

每个 Skill 继承 `BaseSkill` 接口：

```
class BaseSkill:
    name: str                    # 技能名称
    description: str             # 描述（会被注入 LLM 的 system prompt）
    required_permissions: list   # 所需权限声明
    tools: list[Tool]            # 可用工具列表

    async def execute(tool_name, params) -> Result
```

**M5 内置 Skills**：

| Skill | 职责 | 权限 |
| --- | --- | --- |
| **TmuxSkill** | 在 tmux 会话中执行命令，支持超时与输出截断保护 | `required_permissions=[]`（M5 暂不做 PM 校验） |
| **CodeRunSkill** | 将 Python / shell 代码写入临时文件后执行，优先使用 bwrap 沙箱，缺失时 fallback 直执并告警 | `required_permissions=[]`（通过 PM 白名单生成 bwrap rw 绑定） |
| **FileSystemSkill** | 智能文件读取/写入/目录列表 + 目录树索引（`directory_index.yaml`） | `required_permissions=["filesystem"]` |

**M5 Tool Calling 执行路径**：

1. Router 以 `tools` 参数调用 LLM（LiteLLM function calling）
2. Pipeline 进入 ReAct 循环，解析 `tool_calls`
3. SkillManager 统一分发工具调用，执行链路为 `CircuitBreaker.can_execute -> PermissionManager.check(按需) -> skill.execute -> record_success/failure`
4. 工具结果回灌到 ReAct messages，直到模型结束或触发最大轮次限制

**后期扩展**：

- `CodingBridgeSkill`：对接 Hypo-Coder / Codex / Claude Code
- `NotionSkill`：接入 Notion API

### 3.6 安全架构

```mermaid
graph TD
    SkillCall["Skill 调用请求"] --> CB["Circuit Breaker"]
    CB --> PM["Permission Manager"]
    PM -->|Check| Whitelist["Directory Whitelist<br>(Read/Write/Execute)"]
    PM -->|Check| SysGuard["System Config Guard<br>(Password Required)"]
    PM -->|Pass| Exec["Execute Skill"]
    PM -->|Deny| Reject["Reject + Log"]
    CB -->|Fuse Blown| Halt["Halt + Notify User"]
    KillSwitch["Kill Switch<br>(WebUI / API)"] -->|Emergency| Halt
```

- **Permission Manager**：根据 Skill 声明的权限和全局安全策略（`security.yaml`）进行校验。
- **Directory Whitelist**：配置文件使用 `rules + default_policy` schema（白名单外默认只读）。
- **System Config Guard**：修改系统配置类操作需用户显式确认 + 密码。

M5 实际落地细节：

1. `PermissionManager.check_permission(path, operation)` 使用 `Path.resolve(strict=False)`，跟随 symlink 并消除 `..`，防止路径穿越。
2. `CodeRunSkill` 通过 bwrap 构建隔离执行环境：
   - `--ro-bind / /`
   - 对白名单中具 `write` 权限目录添加 `--bind <path> <path>`
   - `/tmp/hypo-agent-sandbox` 固定 rw
   - `--dev /dev --proc /proc --unshare-all --share-net`
3. bwrap 缺失时不阻塞：记录 `code_run.bwrap.fallback` 警告并退回 `bash -lc` 直接执行。
4. 观测事件已接入：
   - `permission.check.allowed` / `permission.check.denied`
   - `fs.read` / `fs.write` / `fs.list` / `fs.scan` / `fs.index.update`
   - `code_run.bwrap.exec` / `code_run.bwrap.fallback`

### 3.7 WebUI 架构

| 页面 | 职责 | 数据源 |
| --- | --- | --- |
| Chat View | 对话界面，Markdown 渲染 + 代码高亮 + 富媒体 | WebSocket 实时流 |
| Dashboard | 运行状态、Token 统计、活跃 Skill、最近任务 | REST API 轮询 |
| Config Editor | 模型路由、Skill 配置、安全策略、定时任务编辑 | REST API |
| Memory Editor | 三层记忆的浏览、搜索、编辑 | REST API |

前端与后端通信：

- 对话流：WebSocket（实时双向）
- 配置/记忆/仪表盘：REST API（读写操作）

### 3.8 与 OpenClaw 的关键差异对比

| 维度 | OpenClaw | Hypo-Agent |
| --- | --- | --- |
| 技术栈 | Node.js / TypeScript | Python / FastAPI |
| 模型路由 | 单模型为主 | 多模型配置驱动路由 |
| 记忆 | 双层（.jsonl + SQLite/MD） | 三层（Session + SQLite + MD/Vector） |
| 鉴权 | 设备配对 + Challenge 签名 | Token-based（单用户简化） |
| 安全 | 无内置熔断 | 三层熔断 + 权限沙箱 + Kill Switch |
| 前端 | 三方消息平台为主 | 自建 WebUI（Chat + Dashboard + Config + Memory） |
| 调度 | Cron + Webhook + 邮件 + 语音 | V1 仅 Cron，保持简单 |

### 3.9 会话管理（Session Model）

*引入自旧系统的 Thread ID / Task ID 隔离机制，增加主/副会话设计。*

L1 会话记忆采用**主会话 + 副会话**模型：

```mermaid
graph TD
    Main["Main Session<br>(持久主线对话)"] -->|Fork| Sub1["Sub-Session: Heartbeat Task<br>(定时任务上下文)"]
    Main -->|Fork| Sub2["Sub-Session: Tmux Debug<br>(终端调试上下文)"]
    Main -->|Fork| Sub3["Sub-Session: Workflow X<br>(固定流程上下文)"]
    Sub1 -->|Summary| Main
    Sub2 -->|Summary| Main
    Sub3 -->|Summary| Main
```

- **主会话（Main Session）**：用户与 Agent 的持久对话主线，始终存在，承载日常交流和上下文连续性。WebUI 的 Chat View 默认展示主会话。
- **副会话（Sub-Session）**：由特定任务 Fork 出的独立上下文，拥有独立的 session_id 和消息缓冲区。典型场景：
    - Heartbeat 定时任务触发时自动创建副会话
    - WorkflowSkill 执行固定流程时创建副会话
    - 用户主动开启一个专项任务（如"帮我调试这个项目"）
- **回流机制**：副会话结束时，用小模型生成摘要，回流到主会话的上下文中（而非把所有细节灌回去）。
- **WebUI 展示**：主会话为默认视图，副会话以可折叠的侧边栏/标签页形式展示（类似旧系统设计的"单收件箱 + 可折叠后台任务"）。

### 3.10 Error Parser Chain（错误压缩中间件）

*引入自旧系统 Milestone 3 的 Error Parser 子模型链路。*

当 Skill 返回的输出超过阈值（默认 500 行或 8000 字符）时，Pipeline 自动触发压缩流程：

```mermaid
graph LR
    A["Skill Output<br>(Raw)"] --> B{"Length > Threshold?"}
    B -->|No| C["Pass Through"]
    B -->|Yes| D["Cheap Model<br>(Compress & Summarize)"]
    D --> E["Compressed Output<br>+ Key Errors Extracted"]
    E --> C
    C --> F["Feed to Main LLM"]
```

- 作为 Pipeline 的可配置中间件，默认启用。
- 压缩模型使用 Qwen/DeepSeek 等廉价模型，提取关键错误信息和摘要。
- 原始输出同时写入日志，不丢失任何信息。
- **核心价值**：避免海量终端输出（编译报错、测试日志等）浪费主模型的 context window 和 Token。

### 3.11 Memory GC（记忆垃圾回收）

*引入自旧系统的"后台闲时 GC 进程"设计。*

作为 Heartbeat 的一个内置定时任务（如每天凌晨执行）：

1. **扫描** L1 已结束的副会话日志和过期的主会话历史。
2. **提取** 有价值的信息（踩坑记录、关键决策、用户偏好变更等），用小模型压缩为结构化摘要。
3. **写入** L3 语义记忆（Markdown 文件），并触发向量索引更新。
4. **清理** 已处理的 L1 历史文件，保持短期记忆精简。

这确保 Agent 的长期记忆不断积累有价值的知识，而不是无限膨胀的原始对话记录。

### 3.12 SkillOutput 标准契约

*引入自旧系统的 `SkillOutput` 设计，去掉 routing_directive，保留结构化返回。*

所有 Skill 的返回值统一为 `SkillOutput` 结构：

```python
@dataclass
class SkillOutput:
    status: str          # "success" | "error" | "partial" | "timeout"
    result: Any          # 实际返回内容（文本、文件路径、结构化数据等）
    error_info: str      # 错误信息（status != success 时填写）
    metadata: dict       # 附加元数据（执行耗时、Token 消耗等）
```

- `status` 用于 Circuit Breaker 的错误计数判定。
- `metadata` 用于日志和可观测性仪表盘。
- `error_info` 用于 Error Parser Chain 的输入。
- 统一的返回结构使得所有 Skill 对 Pipeline 来说行为一致，降低集成成本。

### 3.13 WorkflowSkill（可注册固定流程引擎）

*将旧系统的 DAG 状态机改造为可插拔的 Skill，通过配置注册固定流程。*

WorkflowSkill 是一个特殊的 Skill，内部维护一个轻量状态机，按配置文件定义的步骤顺序执行：

```yaml
# workflows/coding_check.yaml
name: coding_check
description: "拉代码 → 跑测试 → 分析错误 → 报告"
steps:
  - name: pull_code
    skill: TmuxSkill
    command: "cd project_dir && git pull"
    on_error: abort
  - name: run_tests
    skill: TmuxSkill
    command: "cd project_dir && pytest"
    on_error: continue
  - name: analyze
    skill: ErrorParserChain
    input_from: run_tests.output
  - name: report
    action: reply_to_user
    template: "项目 project_dir 检查完毕：\nanalyze.output"
```

- 每个 Workflow 通过 YAML 配置注册，放在 `workflows/` 目录下，启动时自动加载。
- Workflow 执行时自动创建副会话（3.9），不污染主对话。
- 每一步的 `on_error` 控制流转：`abort`（终止）、`continue`（跳过继续）、`retry`（重试）。
- LLM 可以通过 Skill Manager 调用已注册的 Workflow，也可以由 Heartbeat 定时触发。
- **与纯 ReAct 的分工**：日常对话和灵活任务用 ReAct；固定、可重复、需要确定性的多步流程用 WorkflowSkill。

### 3.14 当前仓库目录（M0 基线）

当前代码采用 `src/hypo_agent/` package layout（不是 flat layout）。

- 导入根：`hypo_agent`
- 示例导入：`from hypo_agent.models import Message`

```text
hypo-agent/
├── config/                        # YAML 配置
│   ├── models.yaml
│   ├── skills.yaml
│   ├── security.yaml
│   ├── tasks.yaml
│   └── persona.yaml
├── workflows/                     # Workflow 配置
├── memory/
│   ├── sessions/
│   ├── knowledge/
│   └── hypo.db
├── src/
│   └── hypo_agent/
│       ├── __init__.py
│       ├── models.py              # Pydantic 模型定义
│       ├── gateway/
│       │   └── __init__.py
│       ├── core/
│       │   ├── __init__.py
│       │   └── logging.py
│       ├── memory/
│       │   └── __init__.py
│       ├── skills/
│       │   └── __init__.py
│       ├── scheduler/
│       │   └── __init__.py
│       └── security/
│           └── __init__.py
├── web/                           # M1 将初始化 Vue 3 + Vite + TS
├── tests/
│   ├── conftest.py
│   └── test_models_serialization.py
├── logs/
├── pyproject.toml
└── environment.yml
```

说明：M0 已完成骨架与模型层，M1 主要在 `src/hypo_agent/gateway/` 与 `web/` 目录推进功能实现。

### 3.15 M0 已定义数据模型清单（5 个核心 Pydantic 模型）

模型文件：`src/hypo_agent/models.py`

| 模型 | 职责 | 关键字段 |
| --- | --- | --- |
| `Message` | WebSocket 与内部消息统一载体 | `text/image/file/audio`, `sender`, `timestamp`, `session_id` |
| `SkillOutput` | Skill 统一返回契约 | `status`, `result`, `error_info`, `metadata` |
| `ModelConfig` | 模型路由配置结构 | `default_model`, `models`, `task_type_to_model` |
| `SecurityConfig` | 安全配置结构 | `directory_whitelist`, `circuit_breaker` |
| `PersonaConfig` | 助手人设配置结构 | `name`, `aliases`, `personality`, `speaking_style` |

补充：`SecurityConfig` 在实现中由两个子模型组成：`DirectoryWhitelist` 与 `CircuitBreakerConfig`。
