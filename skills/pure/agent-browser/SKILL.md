---
name: "agent-browser"
description: "浏览器自动化 workflow：打开网页、获取可交互 snapshot、点击、填写表单、截图与读取 JS 渲染内容。用户需要真实 browser interaction、DOM refs、页面操作或截图时使用。"
compatibility: "linux"
allowed-tools: "exec_command"
metadata:
  hypo.category: "pure"
  hypo.backend: "exec"
  hypo.exec_profile: "cli-json"
  hypo.triggers: "browser,浏览器,网页,页面,网页交互,网页操作,点击,点一下,按钮,链接,表单,截图,打开网页,js渲染,dom,页面元素,snapshot"
  hypo.risk: "medium"
  hypo.dependencies: "agent-browser"
  hypo.cli_package: "agent-browser"
  hypo.cli_commands: "agent-browser"
  hypo.io_format: "json-stdio"
---
# Agent Browser 使用指南

## 定位 (Positioning)

`agent-browser` 是真实浏览器自动化 workflow，用于打开网页、读取 JS 渲染后的页面内容、生成带 `ref` 的交互式 snapshot、执行点击/填写/等待/截图等动作。

## 适用场景 (Use When)

- 用户需要真实 `browser interaction`，而不是普通网页搜索。
- 页面依赖 JavaScript 渲染，`agent-search` 或直接 `web_read` 不够用。
- 用户要点击按钮、填写表单、等待页面状态变化、抓取交互元素或截图。

## 与 Agent Search 的边界

- 普通公开信息检索、事实核验、读静态页面，优先使用 `agent-search`。
- 只有当需要真实浏览器状态、页面交互、DOM refs 或 screenshot 时，才切到 `agent-browser`。

## 工具与接口 (Tools)

- 通过 `exec_command` 调用 `agent-browser` CLI。
- 默认所有 inspect 类调用都加 `--json`，这样 stdout 返回结构化 JSON。
- 同一轮浏览器任务中，使用稳定的 `--session <name>` 维持页面状态；推荐值如 `--session agent-browser`.
- 常用命令模板见 `references/command-patterns.md`。

## 标准流程 (Workflow)

1. 先用 `agent-browser --json --session <name> open <url>` 打开页面。
2. 再用 `agent-browser --json --session <name> snapshot -i` 获取可交互元素和 `ref`。
3. 后续点击、填写、获取文本、截图等操作都复用同一个 `--session`。
4. 页面较慢时，先调用 `wait`，再做 `snapshot` 或后续动作。
5. 输出时总结关键页面状态和操作结果，不要直接堆完整 JSON。

## 参数约定 (Parameters)

- 打开页面：
  `agent-browser --json --session <name> open <url>`
- 获取交互式 snapshot：
  `agent-browser --json --session <name> snapshot -i`
- 点击 ref：
  `agent-browser --json --session <name> click @e2`
- 填写输入框：
  `agent-browser --json --session <name> fill @e3 "value"`
- 读取文本：
  `agent-browser --json --session <name> get text @e1`
- 截图：
  `agent-browser --json --session <name> screenshot <path>`

## 边界与风险 (Guardrails)

- 不要用 shell chaining，例如 `&&`；每一步用单独的 `exec_command`，靠 `--session` 维持浏览器状态。
- 默认保持 read-first。未经用户明确要求，不要提交表单、下单、登录、删除内容或触发其他破坏性动作。
- 页面交互前优先先做 `snapshot -i`，不要盲点选择器。
- 涉及登录态、cookie、下载文件或跨站操作时，要明确说明风险和当前步骤。
