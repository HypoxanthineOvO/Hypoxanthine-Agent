# C3 Agent CLI / Delivery / Plan Acceptance

## Scope

C3 verifies:

- `hypo-agent send` programmatic CLI
- outbound text, image, and file dispatch
- Claude/Codex/OpenCode Skill wrappers
- QQ / Weixin / Feishu per-channel results
- Notion `HYX的计划通` read-only summary
- heartbeat plan summary

## Local Verification

```bash
uv run pytest \
  tests/core/test_outbound_send_service.py \
  tests/scripts/test_agent_cli_send.py \
  tests/gateway/test_outbound_api.py \
  tests/core/test_notion_plan_reader.py \
  tests/skills/test_hypo_agent_send_skill_wrapper.py
```

## Real Smoke

Use `[C3-SMOKE]` and keep the run bounded:

- one text per channel
- one image per channel
- one small file per channel
- one generated image delivery smoke
- one Notion read-only today summary smoke

## Final Action

After implementation, restart the Hypo-Agent service and send HYX a completion report through:

```bash
hypo-agent send --text "C3 completion report..."
```
