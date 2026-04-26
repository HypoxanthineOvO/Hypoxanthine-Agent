# Non-Blocking Message Runtime

Date: 2026-04-26

## Purpose

R6 adds an opt-in non-blocking runtime for queued user messages. The legacy inline event consumer remains the default. When enabled, the event consumer accepts user-message events quickly, turns them into tracked work items, and lets per-session workers process them behind a global concurrency semaphore.

Enable it with:

```bash
HYPO_NONBLOCKING_RUNTIME=1
```

## Work Item Model

Each queued user message receives:

- `work_id`
- `trace_id`
- `session_id`
- `kind`
- `priority`
- `status`
- `terminal`
- timestamps

Status updates are emitted to the same per-message `emit` callback as normal stream payloads:

```json
{
  "type": "work_status",
  "work_id": "work-...",
  "trace_id": "trace-...",
  "session_id": "main",
  "kind": "user_message",
  "status": "queued",
  "terminal": false
}
```

Terminal statuses are `done`, `error`, `timeout`, and `cancelled`.

## Execution Model

The runtime uses two layers:

1. A global semaphore limits concurrent work across sessions.
2. A per-session priority queue preserves same-session order while allowing different sessions to run independently.

Human user messages have normal priority. Scheduler/heartbeat-style user-message events use lower priority, so queued human messages in the same session run first when neither item has started.

## Cancellation And Timeout

`ChatPipeline.cancel_work(work_id)` cancels queued or running work and emits a terminal `cancelled` status.

`user_message_timeout_seconds` can be set when constructing `ChatPipeline`. When a work item exceeds that runtime budget, the pipeline emits:

- an `error` payload with code `USER_MESSAGE_TIMEOUT`;
- a terminal `work_status` payload with status `timeout`.

Provider or stream errors still emit the existing user-facing error payload, and the work item is marked `error`.

## Compatibility

The feature is behind `HYPO_NONBLOCKING_RUNTIME=1`. Without that flag, `ChatPipeline` keeps the previous behavior: the event consumer awaits `_consume_user_message_event(...)` inline.

The existing stream payloads are unchanged. `work_status` is additive and can be ignored by older clients.
