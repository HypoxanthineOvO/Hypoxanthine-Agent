# R6: Non-Blocking Message Runtime

Date: 2026-04-26

## Summary

Implemented the R6 non-blocking runtime foundation behind a feature flag:

- `ChatPipeline` can now create tracked work items for queued user messages.
- Per-session priority queues preserve same-session ordering.
- A global semaphore limits concurrent work across sessions.
- Work status events expose `queued`, `running`, `done`, `error`, `timeout`, and `cancelled`.
- `cancel_work(work_id)` cancels queued or running work.
- `user_message_timeout_seconds` emits a terminal timeout status and releases capacity.
- Scheduler/heartbeat-style user messages use lower priority than human user messages.
- `HYPO_NONBLOCKING_RUNTIME=1` enables the new runtime; default behavior remains the legacy inline path.

## Validation

- `uv run pytest tests/core/test_pipeline_event_consumer.py -q`
  - 18 passed
- `uv run pytest tests/core/test_pipeline_event_consumer.py tests/core/test_pipeline.py tests/unit/test_pipeline_error_handling.py -q`
  - 65 passed
- `uv run pytest tests/core/test_pipeline_event_consumer.py tests/core/test_pipeline.py tests/unit/test_pipeline_error_handling.py tests/gateway/test_ws_push.py tests/gateway/test_app_scheduler_lifecycle.py tests/core/test_scheduler.py -q`
  - 84 passed
- `uv run python -m py_compile src/hypo_agent/core/pipeline.py src/hypo_agent/gateway/app.py`
  - passed
- `HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke`
  - blocked by the script guard because a listener was already present on production port `8765`; not overridden.

## Notes

- R6 does not enable the new runtime by default. This keeps deployment rollback simple: unset `HYPO_NONBLOCKING_RUNTIME` to use the prior event consumer behavior.
- Status delivery is additive; older WebUI clients can ignore `work_status`.
- Durable work-item persistence is not added in R6. Runtime status is in-memory and intended for live queue visibility.
