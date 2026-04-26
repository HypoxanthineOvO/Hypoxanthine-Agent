# Typed Memory And Automatic Migration

Date: 2026-04-26

## Purpose

Hypo-Agent no longer has to treat every durable memory item as a generic `preferences` row. R4 adds a typed memory table and a compatibility migration path so user-facing profile/policy memory can be separated from operational cursors and auth state.

## Memory Classes

| Class | Prompt-Injected | Purpose |
| --- | --- | --- |
| `user_profile` | yes | Stable user facts, durable preferences, personal profile details |
| `interaction_policy` | yes | Language, tone, reply boundary, formatting, behavioral rules |
| `knowledge_note` | yes | Durable knowledge notes that are useful in future reasoning |
| `sop` | yes | Approved reusable procedures |
| `operational_state` | no | Service cursors, channel ids, database ids, scanner state |
| `credentials_state` | no | Auth/login workflow state, tokens, cookies, pending auth metadata |

Only prompt-injectable classes are returned by `StructuredStore.list_prompt_memory_sync(...)`.

## Storage

`StructuredStore` now creates `memory_items`:

- `memory_id`
- `memory_class`
- `key`
- `value`
- `language`
- `source`
- `confidence`
- `status`
- `metadata_json`
- `rollback_metadata_json`
- timestamps

Legacy `preferences` remains available for old call sites. `ChatPipeline._preferences_context()` first reads typed prompt memory, then falls back to legacy preferences if no typed prompt memory exists.

## Migration

`TypedMemoryMigrator` provides:

- `backup(reason=...)`
- `migrate_legacy_preferences()`
- `rollback(manifest_path)`

The migration is rule-based in R4 and records classifier metadata on each typed item. Representative mappings:

- `reply_boundary` -> `interaction_policy`
- `favorite_drink` -> `user_profile`
- `auth.pending.*` -> `credentials_state`
- `*.cursor`, `email_scan.*`, `notion.todo_*` -> `operational_state`

## Backup And Rollback

Before migration, the migrator copies the SQLite database and writes a JSON manifest containing backup id, source database path, backup database path, reason, and timestamp.

Rollback copies the backup database back to the original database path and resets the store initialization flag so future operations re-check schema state.

## Skill Surface

`MemorySkill` still supports:

- `save_preference`
- `get_preference`

It also now supports typed memory:

- `save_memory_item`
- `list_memory_items`

Chinese is the default for user-visible memory summaries; English is preserved when the stored value has no CJK content or when identifiers/titles require it.

## Limitations

- R4 backs up SQLite. Markdown/semantic memory file backup can be added when migration starts mutating those files.
- Classification is deterministic and explainable, not LLM-based yet.
- Legacy preferences are preserved; cleanup of old backup files remains manual as requested.
