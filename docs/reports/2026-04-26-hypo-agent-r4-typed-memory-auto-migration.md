# R4 Typed Memory Store And Automatic Migration

Date: 2026-04-26

## Summary

R4 added typed memory storage, backup/rollback migration tooling, prompt injection filtering, and typed memory tools while preserving legacy `preferences` compatibility.

Runtime/auth/cursor state can now be classified away from prompt-injected user memory.

## Changes

- Added `memory_items` schema to `StructuredStore`.
- Added typed memory CRUD:
  - `save_memory_item(...)`
  - `list_memory_items(...)`
  - `list_prompt_memory_sync(...)`
  - `clear_memory_items()`
- Added `src/hypo_agent/memory/typed_memory.py`:
  - `classify_legacy_memory_key(...)`
  - `TypedMemoryMigrator.backup(...)`
  - `TypedMemoryMigrator.migrate_legacy_preferences(...)`
  - `TypedMemoryMigrator.rollback(...)`
- Updated `ChatPipeline._preferences_context()` to prefer typed prompt memory and fall back to legacy preferences.
- Extended `MemorySkill` with:
  - `save_memory_item`
  - `list_memory_items`
- Added architecture documentation in `docs/architecture/typed-memory-migration.md`.

## Tests

RED phase:

- Missing `hypo_agent.memory.typed_memory`.
- Missing typed memory store API.
- Pipeline still injected only legacy preferences.

GREEN / regression commands:

```bash
uv run pytest tests/memory/test_typed_memory_migration.py tests/core/test_pipeline.py::test_pipeline_prefers_typed_prompt_memory_and_filters_runtime_state tests/core/test_pipeline.py::test_preference_injection -q
uv run pytest tests/skills/test_memory_skill.py tests/memory/test_typed_memory_migration.py tests/core/test_pipeline.py::test_pipeline_prefers_typed_prompt_memory_and_filters_runtime_state tests/core/test_pipeline.py::test_preference_injection -q
uv run pytest tests/skills/test_memory_skill.py tests/memory tests/core/test_pipeline.py -q
python -m py_compile src/hypo_agent/memory/typed_memory.py src/hypo_agent/memory/structured_store.py src/hypo_agent/skills/memory_skill.py src/hypo_agent/core/pipeline.py
git diff --check
```

Observed results:

- Focused typed memory / migration / pipeline tests: 8 passed.
- Prompt-suggested Memory/Pipeline regression: 93 passed.
- Existing warnings are from `lark_oapi` and `websockets` deprecations.

## Evaluation

| Metric | Score | Notes |
| --- | ---: | --- |
| diff_score | 2 | Adds typed memory schema, migrator, skill tools, and pipeline filtering |
| code_quality | 4 | Compatibility-first; migration is explicit and rollbackable |
| test_coverage | 2 | Covers classification, backup, migration, rollback, prompt filtering, and skill CRUD |
| complexity | 3 | Adds schema and migration layer but avoids broad semantic-memory mutation |
| architecture_drift | 1 | Matches roadmap and user preference for backup plus manual cleanup |

## Residual Risk

- R4 backs up SQLite only because this implementation does not mutate Markdown/semantic memory files yet.
- Classification is rule-based. A later async consolidation job can add reviewable LLM-assisted cleanup.
- Legacy `preferences` remains as a compatibility adapter; full cleanup should wait until dependent code paths are migrated.
