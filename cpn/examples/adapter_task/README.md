# Installed live-adapter task

[中文](README_ZH.md)

This bundle runs the same small semantic task through Basic, Codex, DSH and
OpenCode. It contains no provider, endpoint, credential or model choice. Select
and authorize your own exact RPNH execution profile before running a host.

## Actual accepted Registry views

| Basic | Codex 0.155.0 |
|---|---|
| ![Basic accepted run](assets/basic-petrinet.png) | ![Codex accepted run](assets/codex-petrinet.png) |

| Pinned DSH | OpenCode 1.18.32 |
|---|---|
| ![DSH accepted run](assets/dsh-petrinet.png) | ![OpenCode accepted run](assets/opencode-petrinet.png) |

These are the actual settled PetriNet views behind each presentation, with
run-specific checkpoint identities omitted. They are not TUI screenshots and
do not replace the host-specific Registry and provider audit checks.

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

## Modify the task and expected answer

The default verifier always checks the installed stock task. If you edit
`task.txt` and `expected.json`, explicitly select your local expected fixture:

```bash
rpnh examples verify --result answer.json --expected expected.json
```

The fixture must contain exactly `count`, `total`, `mean`, `minimum`, `maximum`
in that order, with finite JSON numbers, a positive integer count and coherent
summary arithmetic. Verification does not execute a task or prove Registry
settlement. Export to a new directory when upgrading; existing output directories
are refused, preserving your edits. Discover other reusable code examples with
`rpnh examples list`, then use `export --example NAME --output DIR`.
