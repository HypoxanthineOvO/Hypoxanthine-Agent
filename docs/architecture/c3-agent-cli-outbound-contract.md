# C3 Agent CLI Outbound Contract

## CLI

`hypo-agent send` is the stable programmatic surface for sending messages to HYX.

Supported inputs:

- `--text`
- repeated `--image`
- repeated `--file`
- `--json payload.json`
- `--stdin`
- repeated `--channel`
- `--dry-run`
- `--output pretty|json`

Default recipient is HYX. Default channels are all enabled outbound channels. A dry run validates and returns a send plan without dispatching.

## Payload

```json
{
  "text": "message",
  "images": ["./image.png"],
  "files": ["./report.pdf"],
  "channels": ["qq", "weixin", "feishu"],
  "dry_run": true
}
```

## Results

Responses include `success`, `dry_run`, `target_channels`, `attachments`, and `channel_results`. Partial success is allowed; callers must inspect each channel entry.

Tokens must be supplied through `--token`, `HYPO_AGENT_TOKEN`, or local config and must be redacted from output and logs.

## Smoke Guardrails

Real C3 smoke messages must use `[C3-SMOKE]`. Each allowed channel may receive at most one text, one image, and one small file during final acceptance.

Notion access for `HYX的计划通` is read-only.
