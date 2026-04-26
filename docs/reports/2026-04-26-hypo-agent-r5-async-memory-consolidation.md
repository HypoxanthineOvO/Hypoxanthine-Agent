# R5: Async Memory Consolidation Pipeline

Date: 2026-04-26

## Summary

Implemented the R5 typed memory consolidation path:

- added `MemoryConsolidationService` for candidate extraction, deduplication, conflict detection, archive handling, safe apply, reports, and rollback;
- integrated consolidation into `MemoryGC.run()`;
- added `MemoryGC.run_background()` for non-blocking scheduled triggers with timeout protection;
- made `memory_gc` cadence configurable through `config/tasks.yaml`;
- redacted credential-looking session text before LLM session-summary prompts.

## Validation

- `uv run pytest tests/memory/test_memory_consolidation.py -q`
  - 4 passed
- `uv run pytest tests/memory tests/gateway/test_app_scheduler_lifecycle.py -q`
  - 52 passed
- `uv run pytest tests/core/test_config_loader.py tests/gateway/test_app_scheduler_lifecycle.py tests/memory/test_memory_consolidation.py -q`
  - 33 passed
- `uv run python -m py_compile src/hypo_agent/memory/consolidation.py src/hypo_agent/memory/memory_gc.py src/hypo_agent/gateway/app.py src/hypo_agent/models.py`
  - passed
- `HYPO_TEST_MODE=1 uv run python scripts/agent_cli.py --port 8766 smoke`
  - blocked by the script guard because a listener was already present on production port `8765`; not overridden.

## Notes

- Candidate extraction is deterministic in R5. Session and semantic-note extraction recognizes explicit typed memory lines; legacy preferences use the R4 rule-based classifier.
- Conflicts against manually sourced typed memory are reported and skipped rather than overwritten.
- Rollback is SQLite-scoped via the R4 backup manifest. Generated JSON reports are audit artifacts and are not removed by rollback.
