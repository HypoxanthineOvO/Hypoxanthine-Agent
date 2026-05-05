# C2 图片生成验收 Runbook

> C2 M4 — 2026-05-04

## 验收范围

本 runbook 覆盖 C2「GPT Image 2 图片生成」功能的端到端验收。

## 本机验收 Gates（必选）

### 1. 单元测试

```bash
uv run pytest tests/skills/test_image_gen_skill.py tests/skills/test_image_gen_channel_intent.py tests/skills/test_image_gen_e2e.py tests/scripts/test_audit_image_gen_cli.py -v
```

**预期**: 全部通过（56+ tests）

### 2. 回归测试

```bash
uv run pytest tests/ -q
```

**预期**: 全部通过，无新增失败

### 3. CLI 可用性检查

```bash
gpt_image_cli --help
gpt_image_cli generate --help
gpt_image_cli edit --help
```

**预期**: 三个子命令均可用

### 4. 契约探针

```bash
uv run python scripts/audit_image_gen_cli.py
```

**预期**: 4 维度，22+ 发现，0 Critical

### 5. 配置检查

```bash
grep -A2 "image_gen:" config/skills.yaml
```

**预期**: `image_gen.enabled: true`

## 渠道 Smoke 测试（Opt-in）

> ⚠️ 以下测试需要真实的渠道配置和外部服务，仅在用户明确 opt-in 时执行。

### QQ 渠道

```bash
# 需要 QQ 渠道配置
pytest tests/ -k "channel_smoke" -m "channel_smoke" --channel=qq
```

**验证**:
- [ ] 图片成功发送到 QQ 群/私聊
- [ ] 图片可正常显示
- [ ] 多图发送正常

### 微信渠道

```bash
pytest tests/ -k "channel_smoke" -m "channel_smoke" --channel=wechat
```

**验证**:
- [ ] 图片成功发送到微信
- [ ] 图片可正常显示

### 飞书渠道

```bash
pytest tests/ -k "channel_smoke" -m "channel_smoke" --channel=feishu
```

**验证**:
- [ ] 图片成功发送到飞书
- [ ] 图片可正常显示

## 功能验证清单

### 文字生图 (generate_image)

- [ ] 基本文字描述生图成功
- [ ] 指定 size 参数生效
- [ ] 指定 quality 参数生效
- [ ] 指定 n 参数生成多图
- [ ] style 参数生效
- [ ] negative 参数生效
- [ ] 输出文件保存到 `output/imagegen/`
- [ ] Attachment(type="image") 正确返回

### 图片编辑 (edit_image)

- [ ] 本地路径参考图编辑成功
- [ ] URL 参考图下载后编辑成功
- [ ] mask 参数（inpainting）生效
- [ ] input_fidelity 参数生效
- [ ] 不存在的路径返回清晰错误

### 错误处理

- [ ] CLI 不可用 → 告知用户运行 `gpt_image_cli setup`
- [ ] 网络超时 → 自动重试 2 次后告知
- [ ] Content policy → 不重试，直接告知
- [ ] 无效参数 → 立即失败，错误可读

### 意图检测

- [ ] 中文「帮我画一只猫」→ generate_image
- [ ] 英文「generate an image of a sunset」→ generate_image
- [ ] 「给这张图片加上帽子」→ edit_image
- [ ] 无关文本 → None

### 历史记录

- [ ] 成功生成后记录写入
- [ ] 失败生成后记录写入
- [ ] 按 session_id 查询正常
- [ ] 记录持久化（重启后可读）

### OperationEvent

- [ ] image_generation 事件正确发射
- [ ] image_delivery 事件正确发射
- [ ] payload 序列化正确

## 验收标准

- 本机 gates 全部通过
- 无回归（1109+ tests pass）
- CLI 可用且配置正确
- 契约探针无 Critical 发现
