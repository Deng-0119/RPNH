# Installed live-adapter task

[中文](README_ZH.md)

This bundle runs the same small semantic task through Basic, Codex, DSH and
OpenCode. It contains no provider, endpoint, credential or model choice. Select
and authorize your own exact RPNH execution profile before running a host.

Export a fresh copy from any installed `rpnh-harness` distribution:

```bash
EXAMPLE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-example-parent.XXXXXX")/adapter-task"
rpnh examples export --output "$EXAMPLE_ROOT"
cd "$EXAMPLE_ROOT"
```

Follow one host directory at a time. Every run uses a fresh operational root.
Copy only the assistant's JSON object to `answer.json`, then verify it locally:

```bash
rpnh examples verify --result answer.json
```

The verifier checks task semantics only. Registry terminal evidence and
provider-private physical-call records remain authoritative execution evidence;
a TUI message or exit code alone is not enough. Generated runs and raw model
transcripts are private operational data and must not be committed.

Each host directory also contains a sanitized `evidence.json` from the
2026-09-26 acceptance run. These summaries record Registry authority, semantic
verification and physical response counts separately. They contain no run ID,
local path, endpoint, credential or raw transcript and do not replace a user's
own validation of their selected route.
