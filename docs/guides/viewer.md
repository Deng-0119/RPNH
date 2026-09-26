---
name: rpnh-petrinet-viewer
description: "Inspect current and historical Registry runs with the read-only PetriNet dashboard."
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: en
  counterpart: viewer_ZH.md
  revision: "2026-09-26.3"
  status: source-reviewed-pre-release
---

[English](viewer.md) | [中文](viewer_ZH.md)

# Read-only PetriNet dashboard

The dashboard projects one existing Registry run. It never acquires writer
authority, executes transitions, resumes work, creates results or changes the
PetriNet. Terminal text and the browser use the same durable run evidence.

## Five-minute serial and parallel tour

Create two offline runs from the source checkout. They use a deterministic
local-process fixture, not a provider:

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-viewer-tour.XXXXXX")"
python -m examples.workflow_patterns.run \
  --scenario serial --run-dir "$DEMO_ROOT/serial"
python -m examples.workflow_patterns.run \
  --scenario parallel --run-dir "$DEMO_ROOT/parallel"
rpnh net --run "$DEMO_ROOT/serial" --view --no-open
```

Open the printed loopback address. After inspecting the serial run, stop only
the viewer process you started and open the parallel run:

```bash
rpnh net --run "$DEMO_ROOT/parallel" --view --no-open
```

The intended Agent-level shapes are:

```text
serial:    intake -> work -> deliver

parallel:  prepare -> facts --\
                   -> risks ---+-> join
```

The actual parallel Overview keeps only Agent successor relations:

![Actual parallel Agent overview](../../examples/workflow_patterns/assets/parallel-overview.png)

Switching the same completed run to PetriNet reveals the places and arcs that
enforce the fan-out and all-input join:

![Actual parallel PetriNet view](../../examples/workflow_patterns/assets/parallel-petrinet.png)

Switch to PetriNet before interpreting exact places and arcs. The ASCII shapes
describe Agent responsibilities only; the Petri projection remains the
authority. The [workflow pattern source page](../../examples/workflow_patterns/README.md)
also provides document and six-stage timeline examples.

## Inspect a run in the terminal

```bash
rpnh net --run /absolute/path/to/run
rpnh net --run /absolute/path/to/run --format json
rpnh net --run /absolute/path/to/run --show-resources
rpnh net --run /absolute/path/to/run --resources-only
rpnh net --run /absolute/path/to/run --node NODE_ID
rpnh net --run /absolute/path/to/run --output projection.json
```

The default projection hides resource places. `--show-resources` adds only
resource nodes declared by the real net. `--resources-only` limits the output
to those declared resources and relevant connections; an empty result means the
net declared none. `--node` selects one exact node and `--output` writes the
chosen terminal projection.

## Open the dashboard

```bash
rpnh net --run /absolute/path/to/run --view
rpnh net --run /absolute/path/to/run --view --no-open
rpnh net --run /absolute/path/to/run --view --no-open \
  --max-checkpoints 4096 --max-firings 5000
```

`--no-open` prints the local address without opening a browser. The positive
bounds default to 2048 canonical checkpoints and 2000 firing rows. Raise them
for a known large run instead of editing source. `--host` accepts only a literal
loopback IP; `--port` accepts 0–65535. View mode intentionally rejects
`--resources-only`, `--node`, `--output` and non-text `--format`; use the
terminal projection for those filters.

## Read the screen

| Region | Meaning |
|---|---|
| Header | Selected run source, interface language, read-only notice, guide and full-screen controls. |
| Run summary | Workflow title and, outside Overview, step, unsettled, settled and active-token counts. A dash means not provided, not zero. |
| View toolbar | Information level, search, resources, activity location, refresh and live polling. |
| Canvas | The selected graph projection. Selecting a node or arc opens the inspector. |
| Inspector | Human explanation, disclosed executions and exact technical evidence for the selection. |
| Timeline | Saved canonical checkpoints for the current net version. It changes observation position only. |
| Footer | Viewer health, selected observation time and checkpoint reference when available. |

## Four information levels

| View | What it displays | What it deliberately hides |
|---|---|---|
| Overview | Explicitly classified Agents and structural next-Agent relationships. | Tools, conditions, places and resources. An arrow is not proof of firing order. |
| Detailed flow | Step cards, joins, conditions and resources; only simple one-to-one ordinary handoff places are folded. | No many-to-one join, resource or condition is silently collapsed. |
| PetriNet | Every projected place, transition and arc from the adopted net. | Resource nodes remain hidden until Resources is enabled. |
| Executions | One row per disclosed firing with attempt, record state, business outcome and admission position. | Missing execution disclosure is not interpreted as zero executions. |

Selecting an Overview Agent shows predecessor/successor Agents and an **Open
full Petri net** action. In the other graph views, the inspector has **About**,
**Executions** and **Evidence** tabs. Evidence shows disclosed identities,
bindings and checkpoint data; the standard viewer does not expose prompt,
configuration or resource bodies.

## Controls and keyboard behavior

| Control | Action |
|---|---|
| Language | Switch English/Chinese labels locally. |
| Guide | Open the concise in-app reading guide. |
| Full screen | Enter or leave browser full-screen mode. |
| Overview / Detailed flow / PetriNet / Executions | Change only the read-side projection. |
| Search + Locate | Dim nonmatches and center the next matching node. Enter in Search has the same Locate action. |
| Resources | Show/hide declared resource dependencies; unavailable in Overview because Overview is Agent-only. |
| Locate activity | Center a node with an unsettled record. If none exists, Overview selects its first Agent; the other graph views locate an active-token place when available. |
| Refresh | Reload the selected live or historical observation. |
| Live | Poll the live position every 2.5 seconds while enabled. It does not start the task. |
| Line bridges | Draw crossing humps where wires cross without connecting. Connections occur only at endpoints. |
| `−` / `＋` | Zoom out/in around the canvas center. The mouse wheel zooms around the pointer. |
| Fit to view | Fit the complete current projection in the canvas. |
| Reset focus | Clear search and selection, close details and fit the graph. |
| Minimap | Show/hide the minimap; clicking it recenters the canvas. |
| Inspector `×` | Clear the current selection. |
| `● Live position` | Leave history and return to the newest observation. |
| `◀` / `▶` | Select the previous/next loaded canonical checkpoint. |
| Play / Pause, speed, slider | Replay loaded checkpoints at 0.5×, 1× or 2× without executing work. |
| Earlier records | Load another bounded history page when one is available. |
| Animate changes | Illustrate adjacent canonical settlement changes; it is not an execution event. |

Drag blank canvas space with the primary pointer to pan. Focused nodes and arcs
support **Enter** or **Space** for selection. There are no other global viewer
keyboard shortcuts.

## Visual legend

| Symbol or style | Meaning |
|---|---|
| Blue rectangular card | Transition or Agent step. |
| Gray/slate circle | Ordinary data or synchronization place. The center number is the active-token count; `?` means unavailable. |
| Green accent | Declared task-entry/boundary place. A pale-green fill means an active token is present. |
| Purple accent | Declared output place. It is a possible exit location, not completion evidence. |
| Amber dashed circle | Declared resource place. It appears only when resources are shown. |
| Pale-yellow transition fill | One or more disclosed firing records remain unsettled. It does not prove a live process. |
| Solid slate arrow | Ordinary consume or produce arc. |
| Purple dashed open arrow | Read arc: the token is observed rather than consumed. |
| Red short-dashed double arrow | Reset arc. |
| Amber long-dashed arrow | Resource or variable-resource dependency. |
| Thick outline | Current selection. Dimmed nodes/arcs are outside search or selection focus. |
| Wire hump | Two lines cross without joining. |
| Moving green dot | Illustration between adjacent saved checkpoints, not a new token fact. |

Colors and shapes aid reading; exact IDs, modes, outcomes and references are in
the inspector and terminal JSON.

## Status and completion boundaries

`settled` is a Registry record state, not automatically a successful business
answer. `started` does not prove process liveness. `outcome_unknown` and missing
data must not be read as zero. The execution inspector can show admitted,
dispatch-authorized, started, returned/failed-unsettled, outcome-unknown,
settled and invalidated records.

The browser labels declared output places but does not invent a terminal-success
badge. Confirm task completion from Registry terminal evidence and the final
registered result through the corresponding task/session status and result
commands. Process exit alone is insufficient.

## Timeline and historical workflows

The timeline replays canonical checkpoints for the current adopted net version.
It does not reconstruct provisional/unsettled intermediate states. If the task
continues while history is selected, polling does not force the page back to
live. Use the `long_process` gallery scenario to see a denser completed history.

To inspect an older workflow, pass that workflow's exact run directory to
`--run`. A main-session Registry can link child workflow Registries, but each
child is independent; viewing or rolling back the main session does not delete
the child.

Codex selectors use `--root`, `--thread-id` and either `--turn` or `--task-id`.
DSH selectors use `--root`, `--session-id` and either `--turn` or
`--request-id`. Their `--view` mode also accepts `--presentation`,
`--show-resources`, `--no-open`, `--port`, `--max-checkpoints` and
`--max-firings`; `--describe` and `--json` do not start HTTP. Presentation
metadata changes labels only and never modifies execution identity or prompts.

Bundled JavaScript libraries are built locally from lockfile-pinned packages.
The dashboard loads no CDN script and performs no provider call.
