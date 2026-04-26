# R7: WebUI Performance And Semantic Memory UI

Date: 2026-04-26

## Summary

Implemented the R7 WebUI refactor:

- buffered assistant streaming chunks before Vue state updates;
- added markdown render caching by message key/version;
- deferred KaTeX/Mermaid enhancement for incomplete streamed blocks;
- added long chat-history pagination;
- added Codex status cards and a Codex Jobs panel;
- added typed memory API endpoints;
- made typed semantic memory the Memory page default, with SQLite preserved as a debug tab.

## Validation

- `cd web && npm run test -- --run src/views/__tests__/MemoryView.spec.ts src/views/__tests__/ChatView.spec.ts src/composables/__tests__/useChatSocket.spec.ts src/utils/__tests__/markdownRenderer.spec.ts`
  - 48 passed
- `cd web && npm run test`
  - 108 passed
- `cd web && npm run build`
  - passed
- `uv run pytest -m integration tests/gateway/test_memory_api.py tests/gateway/test_sessions_api.py -q`
  - 14 passed
- `uv run python -m py_compile src/hypo_agent/gateway/memory_api.py src/hypo_agent/gateway/sessions_api.py`
  - passed
- Playwright smoke against a local Vite server
  - desktop and mobile typed memory views rendered successfully
  - mobile horizontal overflow check passed
  - screenshots written to `/tmp/hypo-r7-memory-desktop.png` and `/tmp/hypo-r7-memory-mobile.png`
- `git diff --check`
  - passed
- Default `HYPO_TEST_MODE=1` smoke on port `8766`
  - blocked by the script guard because a listener was already present on production port `8765`; not overridden.

## Notes

- Chat history uses a low-risk windowed pagination model rather than a new virtualization dependency.
- Codex raw transcript output is not rendered inline in the main timeline. The timeline shows summarized status, and the job panel carries task-level state.
- `scripts/with_server.py` referenced by the webapp testing skill is not present in this repo, so the Playwright smoke used an inline server lifecycle helper.
