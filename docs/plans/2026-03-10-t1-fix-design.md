# T1-fix 设计文档（P0/P1/P2）

**背景**：T1 系统测试 29 项中 7 项 FAIL（T1-06/08/11/12/15/21/27）。本设计文档给出 P0→P1→P2 的修复方案，遵循既定约束（默认 `default_policy=readonly`、QQ 广播、输出压缩标记等）。

**目标**：
- 修复 7 项 FAIL，保持现有架构不变。
- 不削弱通用读取能力（保留灰名单只读），新增黑名单防护。
- 完整覆盖 T1 指定测试与 Smoke Gate。

---

## 设计 A：P0 安全红线与 Kill Switch

### A1. 三级路径策略（白名单/灰名单/黑名单）
- **白名单**：`security.yaml` 的 `allowed_paths`，按 read/write/execute 规则。
- **灰名单**：非白名单且非黑名单，遵循 `default_policy=readonly`（只读允许，写拒绝）。
- **黑名单**：`security.yaml` 新增 `blocked_paths`，读写一律禁止。

**blocked_paths 默认项**：
```
/etc/passwd
/etc/shadow
/etc/sudoers
/etc/gshadow
~/.ssh
~/.gnupg
~/.bash_history
~/.zsh_history
~/.python_history
/root
```

**匹配规则**：
- `os.path.realpath(target)` 解析符号链接后判断。
- `~` 展开为当前用户 home。
- 目录递归匹配（前缀匹配）。

### A2. PermissionManager 校验顺序
```
real_path = realpath(path)
if matches_blocked(real_path): DENY + warning
elif matches_allowed(real_path): apply allow rules
else: default_policy (readonly)
```

### A3. 三条执行路径统一接入
- **FileSystemSkill**：沿用 `PermissionManager.check_permission`，黑名单生效即可。
- **CodeRunSkill (bwrap)**：保留 `--ro-bind / /`；对 `blocked_paths` 追加 `--tmpfs <path>` 覆盖（不追求“不可见”语义）。
- **TmuxSkill**：新增 `scan_command()`，扫描路径参数（`/`、`~`、`./` 前缀）。
  - 默认按“读”检查；`rm/mv/cp/tee/dd/>/>>` 对目标路径按“写”检查。
  - 命中黑名单立即拒绝（`Permission denied: <path> is in blocked_paths`）。
  - 安全命令白名单（无路径参数）：`echo/pwd/whoami/date/uptime/ps/top/free/df/git status/git log/git branch`。

### A4. Kill Switch 一致性
- `/kill` 触发：立即设置 `CircuitBreaker.global_kill = True`，中断当前 LLM streaming，返回固定文案（不走 LLM）。
- kill 状态下：SkillManager 与 ChatPipeline 入口短路并返回固定文案。
- 允许零 token 斜杠指令：`/help /kill /resume /skills /model status /token /reminders`。
- 新增 `/resume` 解除 kill。
- `/help` 文案追加 `/kill` `/resume`。

**P0 测试覆盖**：blocked_path（FS/CodeRun/Tmux/符号链接/灰名单读写/白名单写/安全命令豁免），kill switch（streaming、skill、slash、resume）。

---

## 设计 B：P1 功能性缺陷

### B1. Circuit Breaker 可见反馈
- 工具连续 3 次失败：返回 `SkillOutput(status="fused", error_info=...)`，并禁用该工具。
- 会话累计 5 次错误：直接返回固定暂停文案（不走 LLM）。
- 已禁用工具再次调用：直接 `fused`，不再计入会话错误。

### B2. Reminder 时间漂移
- system prompt 注入当前服务器时间：
  `当前时间: 2026-03-10T00:15:00+08:00 (Asia/Shanghai)`。
- `create_reminder` 校验 `trigger_time` 必须未来（允许 30 秒容差），过去直接 error。
- tool schema description 指明 ISO 8601 + 必须未来。

### B3. ChannelDispatcher 统一广播
- 所有回复（WebUI/QQ/Scheduler）统一走 `ChannelDispatcher.broadcast()`。
- 单个 sink 失败不阻断，warning 日志记录。
- 回复携带 `channel` 字段标记来源：`qq/webui/system`。

### B4. OutputCompressor 标记
- 压缩结果末尾追加 `[📦 Output compressed ...]`。
- 组装阶段兜底注入标记（若 LLM 省略）。

**P1 测试覆盖**：
- 熔断 3 次 fused、5 次会话暂停、fused 工具不计数。
- system prompt 时间注入 + reminder 过去时间拒绝。
- QQ↔WebUI 双向广播、单 sink 失败不中断。
- 压缩标记出现在 tool_result 与最终回复。

---

## 设计 C：P2 偏好记忆落 L2

- 新增 `save_preference(key, value)` 与 `get_preference(key)` 工具。
- `preferences` 表 schema：`key TEXT PRIMARY KEY, value TEXT, updated_at TEXT`（INSERT OR REPLACE）。
- system prompt 明确：用户表达偏好/习惯/个人信息必须调用 `save_preference`。
- Memory Injection 时注入 `[User Preferences]`（最近 20 条；空表不注入）。

**P2 测试覆盖**：save/get/upsert/injection/empty。

---

## 风险与缓解
- **黑名单遮盖语义不一致**：`--tmpfs /etc/passwd` 可能触发 `IsADirectoryError/PermissionError`，但目标是阻断内容访问，允许该语义。
- **工具熔断文案被忽略**：fused 状态作为 `tool_result`，确保 LLM 看见；同时保持输出短小避免压缩。
- **时间注入格式**：统一 ISO 8601 + 时区，避免 LLM 解析偏差。

---

## Smoke Gate
- P0 完成后：`pytest -q tests/ -k "blocked_path or kill or resume"` + `python scripts/agent_cli.py smoke`
- P1 完成后：`pytest -q` + `cd web && npm run test` + `python scripts/agent_cli.py smoke`
- P2 完成后：Final Smoke Gate 同上

---

## 不在本次范围
- 端到端 NapCat 实机连通性优化（仅保证广播逻辑正确）。
- 额外的安全沙箱强化（seccomp/更严格 mount）。
