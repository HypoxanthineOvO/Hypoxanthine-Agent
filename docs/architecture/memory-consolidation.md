# Async Memory Consolidation

Date: 2026-04-26

## Purpose

R5 extends `MemoryGC` from session summary generation into a typed memory consolidation pipeline. The pipeline extracts durable memory candidates, classifies them into the R4 typed memory classes, deduplicates repeated candidates, detects conflicts against existing memory, applies safe changes after backup, and writes a JSON report for audit and rollback.

## Lifecycle

`MemoryGC.run()` now performs two phases:

1. `MemoryConsolidationService.run(apply=True)` scans eligible inactive session files, legacy `preferences`, and semantic Markdown notes for typed memory candidates.
2. The existing session summary flow writes L3 Markdown summaries and marks processed sessions.

Scheduled execution uses `MemoryGC.run_background()`, which schedules the full run as an asyncio background task and wraps it in a timeout. This keeps the scheduler trigger from occupying the main event path while consolidation proceeds.

## Candidate Sources

- L1 sessions: inactive, unprocessed `.jsonl` sessions that satisfy the GC message-count threshold.
- Legacy compatibility view: existing `preferences` rows classified with the same deterministic R4 rules.
- Semantic notes: Markdown files under the knowledge directory, excluding generated summaries, consolidation reports, and backups.

Explicit typed candidates use:

```text
记忆: user_profile.favorite_drink = 绿茶
memory: interaction_policy.reply_boundary = 答完直接结束
记忆归档: user_profile.favorite_drink = 用户撤回该偏好
```

The class must be one of `user_profile`, `interaction_policy`, `operational_state`, `credentials_state`, `knowledge_note`, or `sop`.

## Safety

Before any add, update, or archive is applied, the service calls `TypedMemoryMigrator.backup(...)` and records the manifest path in both the report and each item rollback metadata.

Conflict handling is conservative:

- repeated candidates with the same class/key/value are skipped as duplicates;
- candidates that disagree with manually sourced active memory are reported as conflicts and do not overwrite the existing value;
- existing memory previously sourced from consolidation may be updated by a newer candidate.

`MemoryGC` also redacts credential-looking session lines before sending transcripts to the lightweight model for Markdown summaries. `credentials_state` remains non-injectable because `StructuredStore.list_prompt_memory_sync(...)` only returns prompt-safe classes.

## Reports And Rollback

Each run writes:

```text
memory/knowledge/consolidation_reports/memory-consolidation-<timestamp>-<id>.json
```

The report includes counts for `added`, `updated`, `archived`, `skipped`, and `conflicts`, plus per-item action and reason fields.

Rollback uses:

```python
await MemoryConsolidationService(...).rollback(report_file)
```

Rollback restores the SQLite database from the backup manifest referenced by the report.

## Scheduling

`config/tasks.yaml` now supports `memory_gc` cadence:

```yaml
memory_gc:
  enabled: true
  mode: cron
  cron: "0 4 * * *"
```

`mode: interval` with `interval_minutes` is also supported. When no task config is present, the app preserves the legacy default of `0 4 * * *`.
