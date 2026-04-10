# SKILL.md 中文风格约定

本文定义 Hypo-Agent 内 `SKILL.md` 的统一写法，目标是采用“中文骨架 + English 术语”的风格，兼顾可读性、触发效果与实现一致性。

## 目标

- 正文以中文组织，让维护者和后续扩展者快速理解。
- 保留关键 English 术语，例如 `workflow`、`backend`、`sandbox`、`schema`、`polling`、`guardrails`，避免与代码实现脱节。
- 保持不同 skill 的章节结构基本一致，降低阅读切换成本。

## Frontmatter 约定

- `name` 保持稳定，作为 skill 标识。
- `description` 使用中文主干，必要时保留关键 English 术语。
- `compatibility`、`allowed-tools`、`metadata` 不做风格化改写，优先保证机器可读与触发稳定。
- `metadata.hypo.triggers` 继续保留中英混合关键词。

## 正文章节骨架

默认按下面顺序组织正文；如果某个 skill 不需要某一节，可以省略，但不要随意更换已有章节名称。

1. `定位 (Positioning)`
2. `适用场景 (Use When)`
3. `工具与接口 (Tools)`
4. `标准流程 (Workflow)`
5. `参数约定 (Parameters)`，仅在工具参数复杂时出现
6. `边界与风险 (Guardrails)`
7. `常见模式 (Playbooks)`，仅在该 skill 有稳定套路时出现

## 写作规则

- 先写“这个 skill 是做什么的”，再写“什么时候用/不要用”。
- 中文句式优先，关键实现名词保留 English。
- 用 `backticks` 标出 tool name、field name、CLI command、profile 名称与协议名。
- 多用动作导向表达，例如“先检查”“再调用”“避免直接刷新”，少写空泛描述。
- `Guardrails` 必须明确，特别是 destructive action、production 风险、权限边界与隐私边界。
- 如果 skill 存在明显边界，例如 `exec` vs `code-run`、`info-portal` vs `info-reach`，要显式写出来。

## 推荐措辞

- 用“适合”“优先”“仅在……时”表达选择建议。
- 用“不要默认”“避免直接”“先确认再执行”表达保护性约束。
- 用“read-first”“test mode”“one-shot command”这类术语保持和工程语境一致。

## 不推荐写法

- 整篇只罗列工具，不解释选择顺序。
- 全文纯英文，导致和仓库中文文档割裂。
- 全文纯中文，把 `backend`、`schema`、`sandbox` 等实现概念硬翻译得失真。
- 对高风险 skill 不写 `Guardrails`。
