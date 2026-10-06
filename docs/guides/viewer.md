---
name: rpnh-petrinet-viewer
description: "Inspect current and historical Registry runs with the read-only PetriNet dashboard."
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: en
  counterpart: viewer_ZH.md
  revision: "2026-10-04.5"
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

The Viewer is a separate, read-only local display service. It accepts IPv4 and
IPv6 loopback literals (`127.0.0.1` and `::1`), prints bracketed IPv6 URLs, and
serves only requests whose `Host` and optional `Origin` identify that exact
listener. Connections are handled independently, and each accepted socket has a
finite I/O timeout, so one partial loopback client does not indefinitely block
other Viewer requests. The timeout is not a total request deadline, connection
cap or thread cap. This boundary does not add an HTTP transport to Registry
execution or provider calls; Codex, OpenCode and DSH retain their own adapter
transports.

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

Selecting an Overview Agent shows every real transition in its declared
`source_ids`, each member’s disclosed firing rows, predecessor/successor Agents,
and an **Open full Petri net** action. Equal display names do not merge Agents.
The canvas card combines all member records, including rework and concurrent
instances; a settled representative never hides another member’s unsettled or
unknown status.

Counts are limited to the selected frame’s disclosed member scope. Missing
runtime or incomplete coverage leaves the total unknown, rather than zero. An
explicit empty list is shown as empty; zero totals require complete disclosure
within this frame. Identical rows with the same complete firing reference on the
same member are deduplicated. Different facts under one reference, conflicting
member associations or one reference on several members, including members on
other Agent cards in the same frame, are marked inconsistent before navigation.
Each card still displays and counts only its own declared members.

Choose a member to open its original Petri transition’s **Executions** tab
without preselecting a firing, or choose a firing to open that tab at exactly
that full reference. The
**Executions** table uses the same navigation. Short version labels are display
text only; the complete reference is available in each row. Legacy rows without
a complete reference or enough comparable task/run/net identity remain visible,
but their exact-navigation control is disabled.

Switching Overview → PetriNet → Overview preserves the original member and
selected firing. An accepted same-net refresh may advance the observation head
without losing a still-disclosed full target. If the selected member/reference
is no longer disclosed, is inconsistent or belongs to a changed source scope,
the selection is cleared with an explicit notice. The viewer never substitutes
a same-named or latest instance, and this display selection grants no execution
or cross-source authority.

In the other graph views, the inspector has **About**, **Executions** and
**Evidence** tabs. Evidence shows disclosed identities, bindings and checkpoint
data; the standard viewer does not expose prompt, configuration or resource
bodies.

For a place, **Executions** keeps a separate card for every token in the selected
checkpoint. Each card shows that token record's exact `resource_ref` as full,
plain-text JSON, preserving both `resource_id` and `resource_version_id` without
merging tokens or resource versions. An explicit `resource_ref: null` means that
token record has no resource reference; a missing field in an older response is
marked **not provided**, not interpreted as null. This applies to live and
selected historical checkpoints in the same Registry. It does not fetch resource
names or bodies, substitute `work_resource_ref`, or infer grants or delivery.

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

## Single-frame observation model (developer boundary)

`dashboard-model.mjs` exports two pure read helpers, `observationContext(frame)`
and `observationCoverage(frame)`. Both require the output of `normalizeFrame`,
including its legacy `rpnh/net_view/v1` fallback. They leave the input, net nodes
and edges unchanged. The read-only **This frame's observation context** card
consumes this model in Detailed flow, PetriNet and Executions; Overview keeps
it hidden alongside other technical information. No HTTP response schema,
Registry record or reader service is introduced.
The helpers reject conflicting source modes, capture writer epochs or task IDs
when both the top-level source and `net.source` provide the field. Missing
optional hints remain compatible and are not backfilled; the existing
normalizer and frame acceptance are unchanged. If context generation fails, only
the card clears its old contents and shows **Context unavailable**; the main
frame, timeline and error banner keep their existing behavior. This does not
synthesize a `read_failed` coverage declaration. A normal request failure keeps
the last complete frame and its card. The backend need not repeat the top-level
task ID in `net.source`.

The context maps live to `current` and history to `canonical-as-of`. Its one
`source_cuts` entry preserves exact `net_ref` and `checkpoint_ref` without
flattening their identities. `cut.head_ordinal` is the selected observation
boundary. `checkpoint_selector` retains the existing `position.cursor` hint;
in the legacy fallback that hint is the head, not proof of a saved checkpoint.
`observed_capture.latest_head_ordinal` is the separately reported latest head.
The source-cut `cursor` means pagination and stays null because this frame
contains no history page. Checkpoint selection and paging are not interchangeable.

The card shows those three positions separately, using only the current frame's
reported latest head, never an accumulated history-polling value. Missing values
show **Unknown** while a proven zero stays `0`. Native **Exact references (full
JSON)** disclosure preserves complete net/checkpoint reference values as plain
text, without shortened identities, translations or generated links. Its open
state survives frame refreshes and language changes. This first display slice
does not show epochs and adds no requests or runtime writes.

In history, `cut.writer_fencing_epoch` is null: the existing source epoch belongs
to the current capture and is retained only in `observed_capture`. It does not
prove the historical writer epoch or identify the checkpoint's marking epoch.
An `initial_configured` fallback has no Registry cut, selector or captured head;
its firing coverage is `unsupported`. Missing Registry positions remain null.

`local_source` carries mode, task ID and run directory as local display hints.
It does not establish cross-source identity or authority. SourcePointer fields,
`source_set`, `manifest_version`, access path, disclosure evidence, query scope
and paging cursor remain null. These helpers cannot prove multi-source coverage
or combine separate reads into a consistent observation.

Coverage is limited to firing rows on the frame's explicitly listed transition
IDs, with the original firing-disclosure declaration. `complete` requires a
known mode-matching declaration (`current_observations` or `canonical_only`),
an observed head, a net reference and firing arrays for every listed transition.
It means that disclosed frame scope is complete, not that all real executions
or lifecycle evidence are available. Only this known complete scope may report
zero. Partial scope can report a positive loaded count; an unproven empty count
and an unknown total remain null. Explicit unavailable or unsupported disclosure
keeps null counts. No totals are estimated from head distances or summary hints.
No runtime manifest, readiness, adoption or execution evidence is synthesized.

## Opt-in saved checkpoints across net versions

The default v1 dashboard/history API and its current-net timeline remain unchanged.
At a reported net-version boundary, the Viewer probes the separate saved-checkpoint
capability with the current frame's exact saved selector. **Previous net segment**
loads the advertised predecessor only after a click. It shows one saved checkpoint,
with that net's persisted compiler declaration, boundaries, exact bindings and
marking. It does not reuse today's graph or merge an old segment into the v1 timeline.

The read-only endpoint is `GET /api/v2/checkpoint-view`. All three query parameters
are required exactly once: `net_ref` and `checkpoint_ref` are URL-encoded JSON
objects containing only `entity_type`, `logical_id`, `version_id`; their types must
be `net_instance/v1` and `marking_checkpoint/v1`. `cut` is the positive decimal
ordinal of that checkpoint's complete publication transaction. Unknown, duplicate,
missing or mismatched selectors fail; there is no implicit latest or v1 fallback.
The `rpnh/checkpoint_view/v1` envelope includes the normalized selector, a
`rpnh/dashboard/v1` history frame, separate capture head/writer epoch, adoption
evidence and previous-net navigation coverage. The new envelope is checked before
the legacy frame normalizer receives its frame.

Selection is limited to the predecessor chain reachable from the captured current
canonical checkpoint. `--max-checkpoints` bounds that traversal. A reader limit
reports partial coverage; an unavailable target is not found by searching the
whole database. The selected frame uses canonical objects/events at its exact
cut, complete transaction/publication evidence, matching descriptor bytes and
exact resource provenance. Persisted compiler wire uses the fixed offline loader;
this endpoint never loads HOST code, runs a new compilation callback or exposes
arbitrary resource bodies. It also validates the finite material inventory:
canonical owner/principal refs, effective task authority, plan/node membership,
node role and operation spec, input resources and schema refs against their
selected root collections. Registered port/place/cardinality/outcome tuples must
match the persisted wire. Only schema resources actually referenced by these
wire ports, present in the selected root and canonical at the same cut, receive
an additional internal body read. Their registered content type/size and exact
JSON document are checked against the wire schema using type-sensitive JSON
comparison. Business token/input resource bodies are never read or returned.
A changing capture head or writer epoch rejects the mixed read.

The checkpoint cut, structural net identity and capture H/E are separate clocks.
For example, a saved checkpoint may be complete at 75 while its net's adoption
commits at 77. Selecting 75 shows the saved net and marking at 75. It does not jump
to 77 or copy adoption status from a later capture. Adoption labels distinguish
current at the cut, previously adopted, no evidence in a complete query, and
unknown/incomplete evidence. A later adoption/activity cut is not supported by
this saved-checkpoint selector.

Switching segments stops playback, delayed seeking and polling, clears node/firing
selection and viewport memory, and never animates cross-net deltas. **Return to
retained view #H** restores the previous complete frame, including a live frame
whose capture H was later than its checkpoint, without fetching latest. That
restored capture is explicitly paused: refresh, polling and timeline controls stay
disabled. Only **Return to live** requests current state again. Failed or stale
responses preserve the last complete frame; v2 404/501 never fall back to `/api/v1/net`.

This slice does not provide historical firing/activity rows, provisional replay,
cross-net deltas or an older segment's checkpoint pagination. Firing coverage is
`not_provided`, with no invented empty runtime arrays or zero-execution claim.
Validation includes a retained two-net fixture through the real read-only reader
and GET handler into production app/panels under a Node VM/DOM stub. The fixture has
identical topology, distinct declarations/bindings/token occurrences and a shared
resource pair; it has no firings. Synthetic race/negative contracts are separate.
Browser/ELK layout, paint, dense historical paging and a moving real writer have
not been verified by this validation.

### Opt-in recorded firing activity

In a real transition's **Executions** detail, choose **View recorded activity**.
This separate read-only observation lists only `firing_admitted/v1`,
`transition_firing_started/v1`, and `operation_execution_started/v1`. The graph
stays at its selected checkpoint C; activity displays its own captured evidence
head H and writer epoch E. Every row shows its recorded ordinal and complete
transaction commit separately. PROVISIONAL publication has no canonical visible
position. A recorded operation start means only that start evidence was loaded;
it does not establish process liveness, completion, outcome, or settlement.

**Load more activity** continues the same exact task/run/net/checkpoint/member
scope and H/E. If H/E changes, the old observation remains visible and pagination
stops. **Refresh activity explicitly** validates a new first page and replaces the
old observation atomically; a failed refresh retains the old H/E. A source or
access change clears it. Closing, changing selection, navigating checkpoints, or
returning to live discards activity. Language changes keep expanded firing cards
without fetching. These cards never change the graph's runtime, Agent counts,
tokens, timeline, or animation; Agent-member navigation can reach the same real
transition but the Agent overview does not aggregate this activity.

`GET /api/v2/firing-activity` (HEAD validates the same query without a response
body) is optional and uses `rpnh/firing_activity/v1`. Required parameters are exact
`net_ref`, `checkpoint_ref`, positive complete checkpoint `cut`, and a JSON
`transition_ids` array of 1–64 unique selected members. `limit` is 1–100 (default
50); `cursor` is only the returned opaque continuation token. There is no arbitrary
past evidence-head or resource-body parameter. Query/cursor budgets are 16 KiB/
8 KiB. Each page uses one explicit read-only SQLite snapshot, then fresh H/E and
source-binding guards. Verification allows at most 2048 envelopes per transaction,
256 KiB per descriptor and 8 MiB of page verification material, with a two-second
activity-reader deadline. Exceeding a reader budget fails closed; it does not
change business validity. Checkpoint closure retains its existing reader bounds.

Errors are `400 invalid_query`, `409 stale_observation`, `403 access_changed`,
`501 unsupported`, or `503 read_failed`. A terminal page only completes these
three event types for the displayed scope after a continuous first-to-last page
chain. Loaded event and distinct firing counts differ; lifecycle/all-activity
totals remain unknown. Completion, results, successor checkpoints, settlement and
delta remain `not_provided`. This metadata-only feature uses one bound Registry,
not a cross-source authority or arbitrary historical provisional-state reader.

Explicit activity loading pauses playback and automatic frame refresh, so subsequent automatic frame polls do not discard the selected observation. The UI labels the main graph as retained. Closing activity restores the prior automatic-refresh preference; returning to live clears activity and uses the existing live-read path.

Activity byte budgeting preflights every selected SQL column with
`LENGTH(CAST(... AS BLOB))` inside the same read snapshot before materializing
candidate events, full transaction envelopes, outbox JSON, descriptors and
identity/member rows. The page budget includes transported UTF-8/BLOB values,
a fixed 32 bytes per row and 8 bytes per cell, bounded H/E control material, and
each descriptor read buffer (registered size plus one detection byte). This is a
verification-material limit, not an exact Python heap limit. Every SQL
materialization is charged, including repeated events reached by different
queries; transaction and exact-descriptor cache hits skip both refetch and
recharging. Descriptor content keeps the existing exact locator/envelope checks,
checks file size first, and reads at most registered size + 1. Every event,
including a cached firing's later events, must match the complete verified firing
reference.

### Exact token resource registration card

In a place's token list, **View resource registration** requests only that displayed
exact token's resource pair at its selected saved checkpoint. The opt-in GET/HEAD
`/api/v2/checkpoint-view` accepts the additional JSON `token_resource` parameter:
`token_ref`, `resource_ref`, `expected_task_id`, and `expected_capture` (head ordinal
H and writer fencing epoch E). The original net/checkpoint/cut C parameters remain
required. Missing/null/incomplete resource refs cannot open a card; no work ref,
name, path, or latest-value resolver is used. Original v1 and target-free v2
responses remain unchanged. Older providers can leave details unavailable.

The versioned `token_resource_metadata` extension is limited to one exact token
occurrence: task/run/net/checkpoint refs, C and H/E, the token/pair, place and
active-in-checkpoint flag, registered byte size, media type, nullable schema
identifier, and registration publication event/recorded ordinal/containing
complete transaction commit ordinal. Schema identifiers are plain text labels;
null means not provided. The same resource in another checkpoint remains a
separate occurrence. The registration positions are not earliest canonical
visibility, access, consumption, permission, delivery, or publication-time epoch.

The card adds no body reads beyond the existing checkpoint reader. That reader
may separately read selected compiler-wire and port-schema bodies; an alias to
one of those resources does not make the entire request body-free. The card does
not validate content integrity: registered size is not measured size,
`actual_verified_byte_size` is null, and no registered/content hash is invented.
Summary, descriptors, provenance, producer dossiers, locators, and payloads are
not disclosed. Scope/capture mismatches fail closed; access changes clear old
details. Stale results can retain only already displayed details in the same
scope, with a warning. Closing or selecting a newer token/place/checkpoint
invalidates late results without changing the main graph or firing activity.

The client learns exact task/run identity from the first valid card response and
anchors subsequent responses for that complete request scope, even after access
changes clear displayed details. Closing or changing scope clears the anchor.
Identifier fields must be strings; arrays and other coercible values are rejected.
The opt-in HTTP response boundary cross-checks both frame source layers, the
selected net/checkpoint/C, capture H/E and token placement before disclosing it.
