# M13.3 QQ Rendering Regression Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix four M13.3 regressions: table blocks not rendering as images, inline code backticks leaking into QQ plaintext, pipeline output being auto-compressed, and QQ mirror messages being truncated with a WebUI redirect suffix.

**Architecture:** Keep the existing rendering pipeline intact and apply minimal fixes at the actual fault points: the HTML render template, QQ plaintext renderer, pipeline/app wiring for output compression, and QQ/WebUI mirror transport formatting. Lock behavior with focused regression tests first, then adjust only the code paths that produce the bad output.

**Tech Stack:** Python 3.12, pytest, FastAPI, Playwright-based ImageRenderer, QQ OneBot adapter.

---

### Task 1: Add regression tests for QQ plaintext and QQ mirroring

**Files:**
- Modify: `tests/core/test_qq_text_renderer.py`
- Modify: `tests/core/test_channel_adapter_qq.py`
- Modify: `tests/gateway/test_webui_qq_sync.py`

**Step 1: Write the failing tests**

- Change inline-code expectations from preserving backticks to restoring only the inner code text.
- Add a spaces-preservation case for inline code.
- Add a long-message QQ adapter test asserting no truncation and no WebUI redirect copy.
- Update WebUI→QQ mirror test to expect full assistant content.

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_qq_text_renderer.py tests/core/test_channel_adapter_qq.py tests/gateway/test_webui_qq_sync.py -q`

**Step 3: Commit after green**

Use a normal feature/fix commit once the related production code passes.

### Task 2: Add regression coverage for pipeline compression disablement

**Files:**
- Modify: `tests/core/test_pipeline_tools.py`

**Step 1: Write the failing test**

- Add a pipeline tool-call test that injects a compressor stub which would visibly modify output if called, and assert the original tool payload is emitted unchanged.

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_pipeline_tools.py -q`

**Step 3: Commit after green**

Use a normal feature/fix commit once the related production code passes.

### Task 3: Fix production code with the smallest behavior changes

**Files:**
- Modify: `src/hypo_agent/templates/render.html`
- Modify: `src/hypo_agent/core/qq_text_renderer.py`
- Modify: `src/hypo_agent/channels/qq_adapter.py`
- Modify: `src/hypo_agent/core/pipeline.py`
- Modify: `src/hypo_agent/gateway/app.py`

**Step 1: Fix table rendering deterministically**

- Add an explicit `blockType === "table"` branch that parses Markdown with `marked.parse(...)` into `innerHTML`.

**Step 2: Fix QQ inline code restoration**

- Protect inline code content without backticks.
- Restore only the inner code text.
- Update docstring/comments to reflect the new rule.

**Step 3: Disable automatic output compression**

- Stop wiring an `OutputCompressor` into the default pipeline.
- Guard the pipeline tool-result compression branch so the original tool payload flows through unchanged when the compressor is absent.

**Step 4: Remove QQ truncation and redirect behavior**

- Remove assistant-message truncation and the `完整内容请查看 WebUI` suffix in the WebUI→QQ mirror path.
- Stop splitting QQ text segments during `format()` so full text is sent as one message-segment array.

### Task 4: Verify regressions are closed

**Files:**
- Verify: `tests/core/test_image_renderer.py`
- Verify: `tests/core/test_qq_text_renderer.py`
- Verify: `tests/core/test_channel_adapter_qq.py`
- Verify: `tests/core/test_pipeline_tools.py`
- Verify: `tests/gateway/test_webui_qq_sync.py`

**Step 1: Run focused tests**

Run: `uv run pytest tests/core/test_image_renderer.py tests/core/test_qq_text_renderer.py tests/core/test_channel_adapter_qq.py tests/core/test_pipeline_tools.py tests/gateway/test_webui_qq_sync.py -q`

**Step 2: Run broader backend verification**

Run: `uv run pytest -q`

**Step 3: Run frontend tests if touched transitively**

Run: `cd web && npm test`
