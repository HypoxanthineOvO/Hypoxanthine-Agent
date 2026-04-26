# Codex Isolated Execution Lane

Date: 2026-04-26

## Purpose

Hypo-Agent now has the first backend slice of a Codex execution lane for code, command, repair, and diagnostic work. The goal is to move high-friction engineering work into tracked jobs while keeping conversational history clean.

## Isolation Model

`CodexBridge` can now pass isolation settings to the Codex app-server SDK through `AppServerConfig`:

- `codex_home` sets `CODEX_HOME` in the app-server environment.
- `app_server_cwd` sets the app-server process cwd.
- `config_overrides` is forwarded to Codex CLI `--config` entries through the SDK.

When `codex_home` is set, `CodexBridge.isolation_mode` is `dedicated_codex_home`; otherwise it is `sdk_default`.

This is the strongest currently implemented process-level isolation. If the underlying Codex CLI or SDK still writes to a location outside `CODEX_HOME`, Hypo-Agent cannot fully prevent external client history pollution. The fallback is that Hypo-Agent does not persist raw Codex transcript into L1 session history; it stores structured `codex_jobs` / `codex_job_events` and transient user-visible progress instead.

## Job Model

`StructuredStore` now persists:

- `codex_jobs`
- `codex_job_events`

`codex_jobs` stores `job_id`, `session_id`, `operation`, `working_directory`, `trace_id`, `status`, `isolation_mode`, `thread_id`, summaries, and timestamps.

`codex_job_events` stores structured progress/debug events by `job_id`.

## Service Layer

`CodexJobService` is an opt-in wrapper around `CodexBridge`. It supports high-level operations:

- `inspect_repo`
- `apply_patch_task`
- `run_verification`
- `diagnose_failure`
- `summarize_diff`

It does not expose arbitrary raw shell as a user-facing operation.

Progress events are pushed as `Message(message_tag="tool_status")` with:

- `metadata.transient = true`
- `metadata.persist_to_l1 = false`
- `metadata.codex_job_id`
- `metadata.trace_id`

The service intentionally does not append these progress messages to `SessionMemory`; raw transcript goes into job events.

## Compatibility

Existing `CodexBridge.submit`, `continue_thread`, `abort`, repair, and Hypo-Coder tests still pass. The new job lane is not yet the sole backend for old `/codex`, repair, or coder flows; broader unification is a follow-up.

## Limitations

- History isolation depends on Codex honoring `CODEX_HOME` and config overrides.
- The new job service is opt-in and not yet wired into every slash command.
- Existing repair runs still use `repair_runs`; R3 adds the generic `codex_jobs` model without deleting repair compatibility.
