# send-to-hyx

Send HYX a message through the local Hypo-Agent CLI.

## Usage

Dry run:

```bash
hypo-agent send --dry-run --text "[C3-SMOKE] hello" --output json
```

Send:

```bash
hypo-agent send --text "hello HYX"
```

With attachments:

```bash
hypo-agent send --text "attachments" --image ./image.png --file ./report.md
```

Use `HYPO_AGENT_TOKEN` or local config for authentication. Do not embed secrets in this command file.
