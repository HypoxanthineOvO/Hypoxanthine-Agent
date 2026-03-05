# M5 Security Sandbox Runbook

## 1. Security 配置结构

`config/security.yaml` 中目录白名单使用如下 schema：

```yaml
directory_whitelist:
  rules:
    - path: "/home/hypoxanthine/projects"
      permissions: [read, write, execute]
    - path: "/home/hypoxanthine/documents"
      permissions: [read]
    - path: "/tmp/hypo-agent-sandbox"
      permissions: [read, write, execute]
  default_policy: readonly
```

说明：

- `rules`：按目录声明权限，支持 `read/write/execute`。
- `default_policy: readonly`：白名单外路径仅允许读，拒绝写与执行。
- 权限判断使用 `Path.resolve(strict=False)`，会跟随 symlink 并消除 `..`。

## 2. Skill 执行权限链路

Skill 调用链路：

1. `CircuitBreaker.can_execute`
2. `PermissionManager.check_permission`（仅对声明 `required_permissions` 的 skill 且参数含 `path` 的工具）
3. `skill.execute`
4. `record_success / record_failure`

`FileSystemSkill` 同时在 skill 内部做 PM 检查，避免绕过 SkillManager 时越权写入。

## 3. CodeRun bwrap 沙箱

`CodeRunSkill` 默认优先使用 bwrap：

- `--ro-bind / /`
- writable 白名单目录 `--bind <path> <path>`
- `/tmp/hypo-agent-sandbox` 固定 rw
- `--dev /dev --proc /proc --unshare-all --share-net`

bwrap 不可用时：

- 回退到直接 `bash -lc` 执行
- 记录结构化日志事件 `code_run.bwrap.fallback`

## 4. Directory Index 维护

`FileSystemSkill.scan_directory` 将扫描结果写入：

- `memory/knowledge/directory_index.yaml`

行为：

- 首次写入会自动创建 `memory/knowledge/`
- 重复扫描会合并更新结构并保留已有 `description`
- `update_directory_description` 可人工更新目录描述字段

## 5. 观测与排障

关键日志事件：

- 权限：`permission.check.allowed` / `permission.check.denied`
- 文件系统：`fs.read` / `fs.write` / `fs.list` / `fs.scan` / `fs.index.update`
- 代码执行：`code_run.bwrap.exec` / `code_run.bwrap.fallback`

若出现“工具可调用但无结果”：

1. 检查是否命中 `permission.check.denied`
2. 检查 `code_run.bwrap.fallback` 是否频繁出现（环境未安装 bwrap）
3. 检查 `memory/knowledge/directory_index.yaml` 是否可写
