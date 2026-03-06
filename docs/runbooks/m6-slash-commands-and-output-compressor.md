# M6 Runbook: Slash Commands + OutputCompressor + Router Metrics

## Scope

M6 introduces three runtime capabilities:

1. Slash command pre-dispatch in `ChatPipeline` (no LLM call for slash commands)
2. `OutputCompressor` for long tool outputs before ReAct tool-message injection
3. `ModelRouter` task routing lookup + per-call latency persistence

## Slash Commands

The following commands are handled in-process by `SlashCommandHandler`:

- `/help`
- `/model status`
- `/token`
- `/token total`
- `/kill` (toggle global kill switch)
- `/clear` (clear current session `.jsonl` + in-memory buffer)
- `/session list`
- `/skills`

Behavior:

- Messages not starting with `/` are passed through to normal LLM flow.
- Unknown slash commands return a hint and do not call LLM.
- Slash results are emitted as existing websocket events:
  - `assistant_chunk`
  - `assistant_done`

## OutputCompressor

Trigger policy:

- Threshold: `len(output) > 2500`
- Target: `<= 2500` chars final payload

Model routing:

- Uses `task_routing.lightweight` from `config/models.yaml`

Compression strategy:

- `<= 128K`: single-pass compression
- `> 128K`: chunked compression (~80K chunks), then iterative recompress (max 3 rounds)

Retention:

- Original output is logged via structlog
- In-memory recent-original cache keeps latest 10 entries

Marker:

`[📦 Output compressed from X → Y chars. Original saved to logs. Ask me for details.]`

## Router Metrics

`ModelRouter` now emits `latency_ms` for:

- `call_with_tools` success (`event=model_call_success`)
- `stream` success (`event=model_stream_success`)

`StructuredStore.token_usage` now stores:

- `input_tokens`
- `output_tokens`
- `total_tokens`
- `latency_ms`

Aggregations:

- `summarize_token_usage(session_id=None|<id>)`
- `summarize_latency_by_model()`

Used by slash commands:

- `/token`
- `/token total`
- `/model status`

## Verification Commands

```bash
pytest tests/core/test_slash_commands.py tests/core/test_pipeline.py tests/core/test_pipeline_tools.py -q
pytest tests/core/test_output_compressor.py tests/skills/test_tmux_skill.py tests/skills/test_code_run_skill.py -q
pytest tests/core/test_model_router.py tests/memory/test_structured_store.py -q
pytest -q
```
