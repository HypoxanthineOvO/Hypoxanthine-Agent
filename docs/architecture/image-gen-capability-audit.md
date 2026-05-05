# gpt_image_cli 能力审计报告

> C2 M0 — 2026-05-04 | 探针: `scripts/audit_image_gen_cli.py`

## 审计摘要

| 维度 | 发现数 | Critical | Warning | Info |
|------|--------|----------|---------|------|
| CLI 可用性 | 2 | 0 | 0 | 2 |
| generate 参数 | 12 | 0 | 0 | 12 |
| edit 参数 | 4 | 0 | 0 | 4 |
| batch 参数 | 4 | 0 | 0 | 4 |
| **合计** | **22** | **0** | **0** | **22** |

**结论**: CLI 完全可用，功能超出预期。原生支持图片编辑（`edit` 子命令），无需额外实现 image-to-image 能力。

## CLI 结构

`gpt_image_cli` 是 `image_gen.py` 的入口，暴露 3 个子命令：

| 子命令 | 用途 | 关键独有参数 |
|--------|------|-------------|
| `generate` | 文字生图 | `--prompt`, `--n`, `--size`, `--quality`, `--negative`, `--style`, `--scene` 等 |
| `edit` | 图片编辑 | `--image`, `--mask`, `--input-fidelity`（继承 generate 全部参数） |
| `generate-batch` | 批量生图 | `--input`(JSONL), `--concurrency`, `--max-attempts`, `--fail-fast` |

## generate 子命令参数（29 个）

### 核心参数
| 参数 | 类型 | 说明 |
|------|------|------|
| `--model` | string | 模型选择，强制 `gpt-image-2` |
| `--prompt` | string | 图片描述文字 |
| `--prompt-file` | path | 从文件读取 prompt |
| `--n` | int | 生成图片数量 |
| `--size` | string | 尺寸：`1024x1024`, `1536x1024`, `1024x1536`, `auto` |
| `--quality` | string | 质量：`low`, `medium`, `high`, `auto` |
| `--background` | string | 背景：`auto`, `opaque`（gpt-image-2 不支持 `transparent`） |
| `--output-format` | string | 输出格式：`png`, `jpeg`, `webp` |
| `--output-compression` | int | 压缩级别 |
| `--moderation` | string | 内容审核策略 |
| `--out` | path | 输出文件路径 |
| `--out-dir` | path | 输出目录 |
| `--force` | flag | 覆盖已有文件 |
| `--dry-run` | flag | 试运行不实际调用 API |

### Prompt 增强参数
| 参数 | 说明 |
|------|------|
| `--use-case` | 使用场景 |
| `--scene` | 场景描述 |
| `--subject` | 主体描述 |
| `--style` | 风格（如 watercolor, photorealistic） |
| `--composition` | 构图 |
| `--lighting` | 光照 |
| `--palette` | 调色板 |
| `--materials` | 材质 |
| `--text` | 图中文字 |
| `--constraints` | 额外约束 |
| `--negative` | **负面提示**（排除元素） |
| `--augment` / `--no-augment` | 是否启用 prompt 增强 |

### 后处理参数
| 参数 | 说明 |
|------|------|
| `--downscale-max-dim` | 缩放最大维度 |
| `--downscale-suffix` | 缩放后文件名后缀 |

## edit 子命令参数（32 个）

继承 generate 全部 29 个参数，额外新增：

| 参数 | 类型 | 说明 |
|------|------|------|
| `--image` | path | **必填**。源图片路径（image-to-image） |
| `--mask` | path | 遮罩图片（inpainting 区域） |
| `--input-fidelity` | string/float | 输入保真度，控制编辑幅度 |

## generate-batch 子命令参数

继承 generate 全部参数，额外新增：

| 参数 | 类型 | 说明 |
|------|------|------|
| `--input` | path | **必填**。JSONL 输入文件 |
| `--concurrency` | int | 并行生成数 |
| `--max-attempts` | int | 每个 prompt 最大重试次数 |
| `--fail-fast` | flag | 遇到错误立即停止 |

## 配置状态

| 检查项 | 状态 |
|--------|------|
| CLI 在 PATH 上 | ✅ `/home/heyx/.local/bin/gpt_image_cli` |
| env 配置存在 | ✅ `~/.config/gpt_image_cli/env` |
| env 权限正确 | ✅ mode `0o600` |

## 关键发现

1. **CLI 原生支持图片编辑** — `edit` 子命令的 `--image` + `--mask` + `--input-fidelity` 提供了完整的 image-to-image 和 inpainting 能力。M2 的参考图/编辑实现可以完全依赖 CLI。
2. **负面提示支持** — `--negative` 参数可用于排除不需要的元素，是高质量生图的关键能力。
3. **丰富的 prompt 增强** — `--style`, `--scene`, `--subject` 等 10 个增强参数可在 Agent 侧自动提取。
4. **批量生图能力** — `generate-batch` 支持 JSONL 输入和并发，适合批量生成场景。
5. **`--dry-run`** — 可用于测试命令构建而不实际调用 API。

## 对 M1-M4 的建议

- **M1**: 直接使用 `generate` 子命令。`--dry-run` 可用于测试命令构建逻辑。
- **M2**: 使用 `edit` 子命令处理参考图编辑。`--image` 接收本地路径，需实现 URL 下载到临时目录的预处理。
- **M3**: CLI 生成的图片保存到本地文件，通过 `Attachment(type="image")` 回传渠道。
- **M4**: 可扩展为使用 `generate-batch` 支持批量生成历史。
