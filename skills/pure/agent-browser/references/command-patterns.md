# Agent Browser Command Patterns

这份 reference 收录 `agent-browser` 的常用命令模板，供 Skill 在需要时按场景套用。

## 基本约定

- inspect / read 类命令默认使用 `--json`
- 同一轮任务复用同一个 `--session <name>`
- 不使用 `&&` 串联命令；每一步单独执行

## 打开页面

```bash
agent-browser --json --session <name> open <url>
```

适用：
- 第一步进入页面
- 页面跳转后重新建立当前状态

典型返回：
- `data.title`
- `data.url`

## 获取交互式快照

```bash
agent-browser --json --session <name> snapshot -i
```

适用：
- 找出当前页面可点击/可输入元素
- 后续通过 `@e1` 这类 ref 做精确操作

典型返回：
- `data.refs`
- `data.snapshot`

## 点击元素

```bash
agent-browser --json --session <name> click @e2
```

适用：
- 点击按钮、链接、菜单项

点击后建议：
- 如果页面可能异步变化，先再执行一次 `wait`
- 然后重新执行 `snapshot -i`

## 等待页面稳定

```bash
agent-browser --json --session <name> wait 1000
agent-browser --json --session <name> wait @e3
```

适用：
- 等待时间
- 等待元素出现

## 填写表单

```bash
agent-browser --json --session <name> fill @e3 "example@example.com"
agent-browser --json --session <name> type @e4 "hello world"
```

区别：
- `fill` 更适合清空后重填
- `type` 更像真实逐字输入

## 读取文本

```bash
agent-browser --json --session <name> get text @e1
agent-browser --json --session <name> get title
agent-browser --json --session <name> get url
```

适用：
- 抽取局部文本
- 确认页面标题和当前 URL

## 截图

```bash
agent-browser --json --session <name> screenshot /tmp/page.png
```

适用：
- 用户明确要求截图
- 需要保存页面视觉证据

注意：
- 这是写文件操作，路径要明确可控

## 推荐最小流程

读取 JS 页面内容：

```bash
agent-browser --json --session agent-browser open https://example.com
agent-browser --json --session agent-browser snapshot -i
```

点击后再读页面：

```bash
agent-browser --json --session agent-browser click @e2
agent-browser --json --session agent-browser wait 1000
agent-browser --json --session agent-browser snapshot -i
```
