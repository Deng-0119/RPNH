---
name: rpnh-petrinet-viewer
description: "Inspect current and historical Registry runs with the read-only PetriNet dashboard."
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: en
  counterpart: viewer_ZH.md
  revision: "2026-09-25.1"
  status: source-reviewed-pre-release
---

[English](viewer.md) | [中文](viewer_ZH.md)

# Read-only PetriNet dashboard

The dashboard projects an existing Registry run. It does not acquire writer
authority, execute transitions, resume work, create results or change the
PetriNet. The terminal projection and browser dashboard read the same durable
run evidence.

## Inspect a run in the terminal

```bash
rpnh net --run /absolute/path/to/run
rpnh net --run /absolute/path/to/run --show-resources
rpnh net --run /absolute/path/to/run --resources-only
```

The default view hides resource places. `--show-resources` adds only resource
nodes declared by the real net. `--resources-only` limits the projection to
those declared resources and their relevant connections.

## Open the dashboard

```bash
rpnh net --run /absolute/path/to/run --view
rpnh net --run /absolute/path/to/run --view --no-open
```

The second form prints the local address without opening a browser. Use the run
selector documented by an installed host adapter when choosing from main,
Codex or DSH run roots; the selected run remains the sole source of truth.

## Views and evidence

Overview emphasizes agent relationships. Detailed flow and full PetriNet views
retain places, transitions, arcs, resources and execution state. A terminal
label is shown only when Registry terminal evidence and final-result indexing
support it; a stopped process is not treated as a completed run.

Bundled JavaScript libraries are built locally from lockfile-pinned packages.
The dashboard loads no CDN scripts and performs no provider calls.

## Historical workflows

Pass the historical workflow's run directory to `--run`. A main-session
Registry may contain links to child workflow registries, but each child remains
independent; viewing or rolling back a main session does not delete the child.
