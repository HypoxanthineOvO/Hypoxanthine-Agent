# M5: 安全架构 + FileSystemSkill Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 完成 Permission Manager、SkillManager 权限接线、CodeRunSkill 的 bwrap 沙箱、以及 FileSystemSkill（智能读写/目录索引）并通过可重复测试验证，确保 Agent 文件操作默认受限且可观测。

**Architecture:** 使用 `SecurityConfig.directory_whitelist.rules + default_policy` 作为唯一权限来源，`PermissionManager` 负责路径 `resolve()` 与权限判定；`SkillManager.invoke()` 维持 `CircuitBreaker -> PermissionManager -> skill.execute -> breaker record` 链路；`CodeRunSkill` 改为直接 `asyncio.create_subprocess_exec`，优先通过 bwrap 构建隔离执行环境；新增 `FileSystemSkill` 提供 read/write/list/scan/index 操作，并将目录索引持久化到 `memory/knowledge/directory_index.yaml`。

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, asyncio, structlog, PyMuPDF, python-pptx, python-docx, PyYAML, pytest

**Skill References:** `@superpowers:test-driven-development` `@superpowers:verification-before-completion`

---

## 审阅修订（2026-03-05）

1. `_infer_operation` 仅用于 SkillManager 外层 PM 预检；`scan_directory` 写索引文件时在 `FileSystemSkill` 内部直接 I/O 写入固定索引路径，不依赖该推断结果。
2. `scan_directory` 执行前必须 `mkdir(parents=True, exist_ok=True)` 确保 `memory/knowledge/` 存在，避免首次扫描失败。
3. `CodeRunSkill` 测试迁移后不得再引用 `TmuxSkill` mock/stub，全部改为 subprocess/bwrap 路径测试。
4. `_build_default_deps()` 中 PM 必须同时注入三处：`SkillManager(permission_manager=...)`、`CodeRunSkill(permission_manager=...)`、`FileSystemSkill(permission_manager=...)`。

---

### Task 1: RED - 新白名单 Schema 契约测试

**Files:**
- Modify: `tests/test_models_serialization.py`
- Modify: `tests/gateway/test_settings.py`

**Step 1: 写失败测试（`rules + default_policy`）**

```python
def test_security_config_whitelist_rules_schema():
    security = SecurityConfig.model_validate(
        {
            "directory_whitelist": {
                "rules": [
                    {"path": "./docs", "permissions": ["read"]},
                    {"path": "./memory/knowledge", "permissions": ["read", "write"]},
                ],
                "default_policy": "readonly",
            },
            "circuit_breaker": {},
        }
    )
    assert security.directory_whitelist.default_policy == "readonly"
    assert security.directory_whitelist.rules[0].path == "./docs"
```

```python
def test_load_gateway_settings_reads_new_whitelist_schema(tmp_path: Path):
    # 使用 rules/default_policy 写入 yaml，断言 load_gateway_settings 可正确解析
```

**Step 2: 运行测试确认 RED**

Run: `pytest tests/test_models_serialization.py tests/gateway/test_settings.py -v`
Expected: FAIL（`DirectoryWhitelist` 仍是 `read/write/execute` 三列表结构）。

**Step 3: Commit RED**

```bash
git add tests/test_models_serialization.py tests/gateway/test_settings.py
git commit -m "M5: add failing tests for whitelist rules schema"
```

### Task 2: GREEN - 更新模型与安全配置加载

**Files:**
- Modify: `src/hypo_agent/models.py`
- Modify: `src/hypo_agent/gateway/settings.py`
- Modify: `src/hypo_agent/gateway/app.py`
- Modify: `config/security.yaml`

**Step 1: 实现新模型**

```python
class WhitelistRule(BaseModel):
    path: str
    permissions: list[Literal["read", "write", "execute"]] = Field(default_factory=list)

class DirectoryWhitelist(BaseModel):
    rules: list[WhitelistRule] = Field(default_factory=list)
    default_policy: Literal["readonly"] = "readonly"
```

**Step 2: 更新默认安全配置与解析逻辑**

- `_default_security()` 使用：

```python
{
  "directory_whitelist": {"rules": [], "default_policy": "readonly"},
  "circuit_breaker": {}
}
```

- `config/security.yaml` 改为：

```yaml
directory_whitelist:
  rules:
    - path: "./docs"
      permissions: [read]
  default_policy: readonly
```

**Step 3: 运行测试确认 GREEN**

Run: `pytest tests/test_models_serialization.py tests/gateway/test_settings.py -v`
Expected: PASS。

**Step 4: Commit GREEN**

```bash
git add src/hypo_agent/models.py src/hypo_agent/gateway/settings.py src/hypo_agent/gateway/app.py config/security.yaml
git commit -m "M5: migrate whitelist model to rules schema"
```

### Task 3: RED - PermissionManager 行为测试

**Files:**
- Create: `tests/security/test_permission_manager.py`

**Step 1: 写失败测试**

覆盖场景：
- 白名单内 `read/write/execute` 各自允许。
- 白名单外默认只读：`read` 允许，`write/execute` 拒绝。
- 路径穿越：`allowed/../outside.txt` 的 `write` 被拒绝。
- symlink 跟随：白名单内符号链接指向白名单外时，按真实路径拒绝 `write`。

```python
def test_permission_manager_denies_symlink_escape(tmp_path: Path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir(); outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")
    (allowed / "link.txt").symlink_to(outside / "secret.txt")
    pm = PermissionManager(
        DirectoryWhitelist(
            rules=[WhitelistRule(path=str(allowed), permissions=["read", "write"])],
            default_policy="readonly",
        )
    )
    allowed_flag, reason = pm.check_permission(str(allowed / "link.txt"), "write")
    assert allowed_flag is False
    assert "outside whitelist" in reason.lower()
```

**Step 2: 运行测试确认 RED**

Run: `pytest tests/security/test_permission_manager.py -v`
Expected: FAIL（`permission_manager.py` 尚未实现）。

**Step 3: Commit RED**

```bash
git add tests/security/test_permission_manager.py
git commit -m "M5: add failing tests for permission manager"
```

### Task 4: GREEN - 实现 PermissionManager + 安全日志

**Files:**
- Create: `src/hypo_agent/security/permission_manager.py`
- Modify: `src/hypo_agent/security/__init__.py`

**Step 1: 实现 `PermissionManager`**

核心接口：

```python
def check_permission(
    self,
    path: str,
    operation: Literal["read", "write", "execute"],
) -> tuple[bool, str]:
    ...
```

关键点：
- 使用 `Path(path).resolve(strict=False)` 获取真实路径。
- 规则路径同样 `resolve(strict=False)` 后匹配。
- 匹配规则按路径长度降序（更具体目录优先）。
- 白名单外：`default_policy=readonly` 时只放行 `read`。

**Step 2: 增加 structlog 事件**

- `permission.check.allowed`
- `permission.check.denied`

记录字段：`path`, `resolved_path`, `operation`, `reason`。

**Step 3: 运行测试确认 GREEN**

Run: `pytest tests/security/test_permission_manager.py -v`
Expected: PASS。

**Step 4: Commit GREEN**

```bash
git add src/hypo_agent/security/permission_manager.py src/hypo_agent/security/__init__.py
git commit -m "M5: implement permission manager with path resolution checks"
```

### Task 5: RED - SkillManager 权限接线测试

**Files:**
- Modify: `tests/skills/test_skill_manager.py`
- Modify: `tests/gateway/test_sessions_api.py`

**Step 1: 写失败测试**

覆盖场景：
- 需要权限的 skill（`required_permissions=["filesystem"]`）在 PM 拒绝时，`invoke()` 返回 `SkillOutput(status="error")` 且不执行 skill。
- PM 放行时正常执行。
- 不声明权限的 skill 不触发 PM。
- `AppDeps` 可携带 `permission_manager`（测试构建 app 时不报错）。

```python
def test_skill_manager_blocks_when_permission_denied():
    manager = SkillManager(circuit_breaker=AllowBreaker(), permission_manager=DeniedPM())
    manager.register(FileLikeSkill())
    out = asyncio.run(manager.invoke("read_file", {"path": "/tmp/x"}, session_id="s1"))
    assert out.status == "error"
    assert "permission" in out.error_info.lower()
```

**Step 2: 运行测试确认 RED**

Run: `pytest tests/skills/test_skill_manager.py tests/gateway/test_sessions_api.py -v`
Expected: FAIL（`SkillManager` 与 `AppDeps` 尚未接入 PM）。

**Step 3: Commit RED**

```bash
git add tests/skills/test_skill_manager.py tests/gateway/test_sessions_api.py
git commit -m "M5: add failing tests for skill manager permission checks"
```

### Task 6: GREEN - SkillManager 与 AppDeps 集成 PermissionManager

**Files:**
- Modify: `src/hypo_agent/core/skill_manager.py`
- Modify: `src/hypo_agent/gateway/app.py`

**Step 1: 扩展 SkillManager 构造与 invoke 链路**

```python
class SkillManager:
    def __init__(..., circuit_breaker=None, permission_manager: PermissionManager | None = None):
        ...
```

执行顺序：
1. `CircuitBreaker.can_execute`
2. 定位 skill
3. 若 `skill.required_permissions` 非空且 `params["path"]` 存在，则推断操作并调用 `permission_manager.check_permission`
4. `skill.execute`
5. `record_success/failure`

实现 `_infer_operation(tool_name: str) -> Literal["read", "write", "execute"]`：
- `write/update` 前缀或包含 `write` -> `write`
- `execute/run` -> `execute`
- 其余 -> `read`

**Step 2: 扩展依赖注入**

- `AppDeps` 新增 `permission_manager` 字段。
- `_build_default_deps()` 根据 `security.directory_whitelist` 创建 PM，并传给 `SkillManager`。
- `app.state.permission_manager` 与 `app.state.deps.permission_manager` 同步挂载。

**Step 3: 运行测试确认 GREEN**

Run: `pytest tests/skills/test_skill_manager.py tests/gateway/test_sessions_api.py tests/gateway/test_settings.py -v`
Expected: PASS。

**Step 4: Commit GREEN**

```bash
git add src/hypo_agent/core/skill_manager.py src/hypo_agent/gateway/app.py
git commit -m "M5: wire permission manager into skill manager and app deps"
```

### Task 7: RED - CodeRunSkill bwrap 契约测试

**Files:**
- Modify: `tests/skills/test_code_run_skill.py`

**Step 1: 重写失败测试（去 tmux 依赖）**

覆盖场景：
- `_build_bwrap_command()` 包含 `--ro-bind / /`、`--dev /dev`、`--proc /proc`、`--unshare-all --share-net`。
- 对 PM 白名单中含 `write` 的路径生成 `--bind path path`。
- bwrap 可用时走 `code_run.bwrap.exec`。
- bwrap 不可用时 fallback 直接执行并记录 `code_run.bwrap.fallback`。

```python
def test_build_bwrap_command_includes_rw_overrides(tmp_path):
    skill = CodeRunSkill(permission_manager=pm_with_rw_paths(...), sandbox_dir=tmp_path, ...)
    cmd = skill._build_bwrap_command("python /tmp/hypo-agent-sandbox/a.py")
    assert cmd[:3] == ["bwrap", "--ro-bind", "/"]
    assert "--bind" in cmd
```

**Step 2: 运行测试确认 RED**

Run: `pytest tests/skills/test_code_run_skill.py -v`
Expected: FAIL（当前仍依赖 `TmuxSkill`）。

**Step 3: Commit RED**

```bash
git add tests/skills/test_code_run_skill.py
git commit -m "M5: add failing tests for code run bwrap sandbox"
```

### Task 8: GREEN - 实现 CodeRunSkill bwrap 执行链路

**Files:**
- Modify: `src/hypo_agent/skills/code_run_skill.py`
- Modify: `src/hypo_agent/gateway/app.py`

**Step 1: 移除对 `TmuxSkill` 的依赖**

- 使用 `asyncio.create_subprocess_exec` 执行。
- `run_code` 仍写入 `sandbox_dir` 临时文件后执行。

**Step 2: 新增 bwrap 构建方法**

```python
def _build_bwrap_command(self, command: str) -> list[str]:
    return [
        "bwrap",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--unshare-all",
        "--share-net",
        "--bind", "/tmp/hypo-agent-sandbox", "/tmp/hypo-agent-sandbox",
        ...
        "bash", "-lc", command,
    ]
```

**Step 3: bwrap 不可用 fallback**

- `shutil.which("bwrap") is None` 时直接执行 `bash -lc <command>`。
- 记录 `code_run.bwrap.fallback` 警告日志。

**Step 4: 运行测试确认 GREEN**

Run: `pytest tests/skills/test_code_run_skill.py tests/skills/test_skill_manager.py -v`
Expected: PASS。

**Step 5: Commit GREEN**

```bash
git add src/hypo_agent/skills/code_run_skill.py src/hypo_agent/gateway/app.py
git commit -m "M5: migrate code run skill to bwrap sandbox execution"
```

### Task 9: RED - FileSystemSkill（read/write/list）测试

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/skills/test_fs_skill.py`
- Modify: `config/skills.yaml`

**Step 1: 增加依赖声明**

在 `pyproject.toml` 的 `dependencies` 增加：
- `pymupdf`
- `python-pptx`
- `python-docx`

**Step 2: 写失败测试**

覆盖：
- `read_file` 文本读取与 `MAX_FILE_CHARS=16000` 截断。
- `.pdf`（PyMuPDF）文本提取。
- `.docx`、`.pptx` 文本提取。
- 图片文件返回元信息（尺寸、格式、大小）。
- `write_file` 白名单内可写，白名单外拒绝。
- `list_directory` 深度遍历与 200 条截断。
- `required_permissions = ["filesystem"]`。

```python
def test_write_file_denied_outside_whitelist(tmp_path):
    skill = FileSystemSkill(permission_manager=pm_only_docs(...))
    out = asyncio.run(skill.execute("write_file", {"path": str(tmp_path / "x.txt"), "content": "a"}))
    assert out.status == "error"
```

**Step 3: 运行测试确认 RED**

Run: `pytest tests/skills/test_fs_skill.py -v`
Expected: FAIL（`fs_skill.py` 尚未实现）。

**Step 4: Commit RED**

```bash
git add pyproject.toml tests/skills/test_fs_skill.py config/skills.yaml
git commit -m "M5: add failing tests for filesystem skill read write list"
```

### Task 10: GREEN - 实现 FileSystemSkill（read/write/list）+ 注册

**Files:**
- Create: `src/hypo_agent/skills/fs_skill.py`
- Modify: `src/hypo_agent/skills/__init__.py`
- Modify: `src/hypo_agent/gateway/app.py`

**Step 1: 实现工具与权限检查**

工具：
- `read_file(path)`
- `write_file(path, content)`
- `list_directory(path, depth=1)`

关键行为：
- 所有路径先 `resolve(strict=False)`。
- 每个工具执行前用 PM 检查 `read/write`。
- 文本类后缀直接读，按 16000 字符截断。
- 不支持格式返回 `stat` 信息 + 提示。

**Step 2: 增加结构化日志**

- `fs.read`（`path`, `size`, `format`）
- `fs.write`（`path`, `size`）
- `fs.list`（`path`, `depth`, `count`）

**Step 3: 应用层注册**

- `config/skills.yaml` 增加 `filesystem.enabled` 开关。
- `_build_default_deps()` 在启用时注册 `FileSystemSkill(permission_manager=...)`。
- `_build_default_deps()` 中 `CodeRunSkill` 也同步传入 `permission_manager=...`（三处注入之一）。

**Step 4: 运行测试确认 GREEN**

Run: `pytest tests/skills/test_fs_skill.py tests/skills/test_skill_manager.py -v`
Expected: PASS。

**Step 5: Commit GREEN**

```bash
git add src/hypo_agent/skills/fs_skill.py src/hypo_agent/skills/__init__.py src/hypo_agent/gateway/app.py
git commit -m "M5: implement filesystem skill read write list"
```

### Task 11: RED - FileSystemSkill 目录索引测试

**Files:**
- Modify: `tests/skills/test_fs_skill.py`

**Step 1: 写失败测试（scan/get/update/merge）**

覆盖：
- `scan_directory(path, depth=2)` 生成目录树并写入 `memory/knowledge/directory_index.yaml`。
- `get_directory_index()` 返回 index 内容。
- `update_directory_description(path, description)` 可更新指定节点描述。
- 再次 scan 时保留已有 description（合并更新）。

```python
def test_scan_preserves_existing_description(tmp_path):
    # 先写入带 description 的 index，再执行 scan，断言 description 未丢失
```

**Step 2: 运行测试确认 RED**

Run: `pytest tests/skills/test_fs_skill.py -v`
Expected: FAIL（索引工具尚未实现）。

**Step 3: Commit RED**

```bash
git add tests/skills/test_fs_skill.py
git commit -m "M5: add failing tests for filesystem directory index"
```

### Task 12: GREEN - 实现 scan/get/update 索引能力

**Files:**
- Modify: `src/hypo_agent/skills/fs_skill.py`

**Step 1: 实现索引工具**

新增：
- `scan_directory(path, depth=2)`
- `get_directory_index()`
- `update_directory_description(path, description)`

索引文件：
- 固定路径 `memory/knowledge/directory_index.yaml`
- 写入前确保父目录 `memory/knowledge/` 存在（`mkdir(parents=True, exist_ok=True)`）
- 结构含 `directories` 与 `last_scan`
- 合并策略：若节点已存在 `description` 且新扫描为空，则保留旧值

**Step 2: 增加日志**

- `fs.scan`
- `fs.index.update`

**Step 3: 运行测试确认 GREEN**

Run: `pytest tests/skills/test_fs_skill.py -v`
Expected: PASS。

**Step 4: Commit GREEN**

```bash
git add src/hypo_agent/skills/fs_skill.py
git commit -m "M5: add filesystem directory index scan and update tools"
```

### Task 13: REFACTOR - 可观测性断言与回归验证

**Files:**
- Modify: `tests/security/test_permission_manager.py`
- Modify: `tests/skills/test_code_run_skill.py`
- Modify: `tests/skills/test_fs_skill.py`

**Step 1: 增加日志事件断言**

通过 monkeypatch 替换模块级 logger（或使用 structlog testing 处理器）验证事件名：
- `permission.check.allowed` / `permission.check.denied`
- `code_run.bwrap.exec` / `code_run.bwrap.fallback`
- `fs.read` / `fs.write` / `fs.list` / `fs.scan` / `fs.index.update`

**Step 2: 跑 M5 相关测试集合**

Run:
`pytest tests/security/test_permission_manager.py tests/skills/test_skill_manager.py tests/skills/test_code_run_skill.py tests/skills/test_fs_skill.py tests/gateway/test_settings.py tests/test_models_serialization.py -v`

Expected: PASS。

**Step 3: 跑全量后端测试**

Run: `pytest -q`
Expected: PASS（无回归）。

**Step 4: Commit REFACTOR**

```bash
git add tests/security/test_permission_manager.py tests/skills/test_code_run_skill.py tests/skills/test_fs_skill.py
git commit -m "M5: add observability assertions and finalize test coverage"
```

### Task 14: 文档与里程碑收尾（单独提交）

**Files:**
- Modify: `docs/architecture.md`
- Create or Modify: `docs/runbooks/security-sandbox.md`

**Step 1: 更新文档**

- 补充 M5 实际落地：PermissionManager、bwrap 沙箱、FileSystemSkill、directory index。
- 记录 `security.yaml` 新 schema 与运维注意事项（bwrap 未安装 fallback 风险）。

**Step 2: 文档单独提交（遵循仓库约定）**

```bash
git add docs/architecture.md docs/runbooks/security-sandbox.md
git commit -m "M5[doc]: document permission manager, bwrap sandbox, and filesystem skill"
```

---

## 执行清单（顺序不可打乱）

1. 先完成 Task 1-2（schema 迁移），避免后续 PM/Skill 初始化因模型不匹配失败。
2. 再完成 Task 3-6（权限判断 + SkillManager 接线），建立统一权限边界。
3. 然后完成 Task 7-8（CodeRun bwrap），移除 tmux 依赖路径。
4. 最后完成 Task 9-12（FileSystemSkill 全量能力）并在 Task 13 做回归封板。
5. 文档必须用 Task 14 单独提交。

## 风险点与应对

1. **bwrap 在 CI/本机缺失**：使用 fallback，但测试必须断言 warning 日志，避免静默降级。
2. **路径解析在不存在目标时行为差异**：统一 `resolve(strict=False)`，并针对“父目录存在/不存在”都加测试。
3. **目录索引合并覆盖 description**：先写保留策略测试，再实现 merge，禁止后修。
4. **重构 CodeRunSkill 影响已有行为**：保留原 `run_code` 输入输出结构，确保 pipeline/tool-calling 不变，且测试不再耦合 tmux。
