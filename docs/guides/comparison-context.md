---
name: rpnh-comparison-context
description: "Compare authorized exact PN definitions and checkpoints without execution."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: comparison-context_ZH.md
  revision: "2026-10-11.3"
  status: implementation-candidate
  basis: "public exact read session; descriptive comparison only"
---

[English](comparison-context.md) | [中文](comparison-context_ZH.md)

# Independent exact-net comparison

The independent viewer reads an existing, HOST-selected Registry read session. It
can compare exact registered net definitions, exact saved checkpoints, and stored
author PN projections. It does not require a current run. It does not compile,
execute, adopt, merge, create a grant, or change a Registry.

Start it with an existing trusted local read-HOST configuration:

```sh
rpnh net --read-host-config /absolute/path/read-host.json --view --no-open
```

This is a same-OS-user, loopback-only viewer. The configuration chooses existing
source bindings and owner-issued observation contexts; HTTP input cannot supply
paths, principal declarations, grants, callbacks, or a compiler. Index, record,
material-body and export permissions remain independent. Opening this viewer
does not grant any of them. See the independent Registry read-session guide for
configuration and issuer details. Existing `rpnh net --run ...` behavior remains.

## Plain merge results

Fresh `PlainModuleMergeAuthor` results persist a deterministic
`rpnh/plain_merge_public_pn/v1` projection before the actual two-parent revision
M is committed. The viewer selects M itself and displays its existing PN nodes,
arcs and configuration references. Comparing M with a parent uses `full_pair`:
merge correspondence and runtime continuity are `not_provided`.

The merge-only `rpnh/stored_plain_merge_public_pn/v1` envelope freezes a bounded
ancestor inventory proof, tied to the original source binding, exact materials,
commands, explicit conflict choices, HOST declarations and schema authorities.
The reader reconstructs the nearest base, normalized analysis and resolved
Module from authorized fixed-cut bytes. It never compiles, invokes a HOST,
queries current facts or writes a Registry. Every merge proof material is
required; denial or corruption cannot become a topology-only fallback. This
version retains the session's existing row and total material-byte limits; a
large valid merge can exceed a selected observer's view budget. These view limits
do not reject the authoritative merge. Graph authors, opaque designer constraints
and unsupported author histories remain excluded.

The original result command and its five prepared materials are unchanged.
Publication adds one immutable projection cut before the final revision cut;
interrupted replay reuses its exact identity and bytes. Historical completed M
without a stored projection remains `PROJECTION_UNAVAILABLE`; reading or replay
never backfills it. Ordinary author and Assembly projections retain their
existing contracts.

## Select and navigate

1. Choose a source and an explicit exact object on each side. There is no implicit
   latest checkpoint and neither selector depends on the current dashboard.
2. Choose definition, configuration, materials and/or runtime axes. Unrequested
   axes say `not_requested`.
3. Read the complete pair. Use the independent canvases to pan, zoom and select
   nodes. Both canvases use the existing NetRenderer and ELK layout.
4. Choose a declared module/member scope, or scope to a selected node. Hierarchy
   comes from verified declarations and occurrence paths, never names or layout.
   Parent navigation follows the declared parent. Internal arcs and boundary
   connections are separate; boundary placeholders are not new PN owners.
5. “Full pair” restores both exact nets at the same returned SourceCuts. If the
   full scope exceeds limits or is unavailable, the old scoped pair is cleared.
   Large scopes are never silently truncated and called complete.

When no declared hierarchy is provided, the graph stays flat and says so. A
source-cut handle belongs to its read session; changing its head, borrowing a
handle from another session, or silently selecting a newer cut is rejected.
Ordinary later appends can coexist with a reconstructable fixed cut. Binding,
fence, catalog or authority changes invalidate the observation.

## What each mode means

- `reliable_diff`: verified correspondence closes the selected scope, including
  its internal nodes and arcs. Individual fields can still be unknown
- `partial_mapping`: only the verified portion is compared; remaining subjects
  stay visible without invented add/delete claims
- `full_pair`: independent reading without verified correspondence. Matching
  names or bytes do not establish identity

A retained author identity and a copy are different claims. A retained origin
can also have several distinct copies. Copy direction is explicit after swapping
panes. Split/fusion remains a grouped relation, including its grouped edges;
there is no Cartesian expansion or inferred runtime identity. General many-to-
many mapping is unsupported. Manual visual pairing changes only this displayed
comparison and never increases reliability or writes a Registry fact.

For a direct copy or retained operation using the registered native-plugin v1
component, the reader can also verify its generated capability place and read
arc. It checks the complete stored lowering recipe and exact configuration,
operation, plugin/executor, tool and schema registrations against the authorized
parent projection. This proof uses `rpnh/native_capability_derivation/v1` and
means author correspondence only. It never composes a chain of parents, compiles
a net, or establishes token continuity. Changed configuration, missing parent
material authority, other lowerers and shared capability carriers remain unknown.

## Four independent axes

- Definition compares the explicitly provided public node/arc fields through
  verified subject correspondence
- Configuration compares approved exact declaration/requirements references.
  Different refs do not prove different effective model, tool or workspace
  values. Host-registration requirements and package environment requirements
  retain their distinct types
- Materials uses exact token resource refs and separately authorized headers.
  Body digests are computed only after an authorized material-body read; an
  initially denied body stays unknown. A Boolean arc `resource` flag is never a
  material reference
- Runtime currently covers selected-checkpoint marking and exact token
  occurrence refs only. Firings, activity and completion evidence remain
  explicitly unknown. Definition-only targets have no borrowed current marking

`provided: null`, an unknown field, and proven absence are distinct. Known-same
means one provided field is equal under its comparator, not that two nets are
business-equivalent or safely interchangeable. Counts are field-row counts for
this exact scope. Missing counts are `null`, not zero.

## Publication and lifecycle

The server validates both sides and all evidence, validates the complete DTO,
and rechecks every involved source before publishing anything from the pair.
No half-pair is streamed. Errors are fixed no-store codes without private paths
or exception details. The comparison-only provider has no legacy raw-net route.

Changing a selector, scope, axis or visual pair cancels pending work and clears
both canvases, mapping, counts and details. Late responses cannot repopulate a
new generation. Close, dashboard navigation, Back/Forward, pagehide and bfcache
restoration discard the pair. The previous live-refresh preference is preserved.

The new read-only routes are `GET/HEAD /api/v2/comparison-selection` and
`GET/HEAD /api/v2/comparison-context?request=<JSON>`, using
`rpnh/comparison_request/v1` and `rpnh/comparison_context/v1`. Both request and
response are closed DTOs. The existing `/api/v2/comparison-view` and
`rpnh/checkpoint_comparison/v1` still require the same exact net and preserve
their earlier current-capture semantics.

Large requests use read-only `POST /api/v2/comparison-context` with
`Content-Type: application/json` and the same closed v1 request DTO as the body.
The browser switches to POST when the encoded GET path reaches 60000 bytes,
before the HTTP request-line limit of 65536 bytes; smaller requests retain GET,
and HEAD remains supported. POST requires one valid `Content-Length` and does
not accept chunked transfer. Before reading or allocating the body, the server
checks its declared length against HOST `max_request_bytes`, returning 413 when
exceeded. The parser also checks actual UTF-8 body bytes, including whitespace;
duplicate JSON keys remain invalid. The Python parser is
`parse_body(raw: bytes, *, host_limits=None)`; the local request helper accepts
`handle_request(provider, "POST", "/api/v2/comparison-context", body=raw)`.
This POST performs only the existing read-only comparison. Methods on other
routes and owner operations are unchanged. Comparison responses send exactly
the compact UTF-8 JSON bytes measured by the budget validator; `Content-Length`
is that byte count. HEAD returns the same length header without the body.

## HOST resource budgets

`LIMITS` contains defaults, not fixed product ceilings. The trusted Python APIs
are `ComparisonProvider(session, *, host_limits=None)` and
`comparison_context(session, request, *, host_limits=None)`. The mapping accepts
partial overrides; omitted fields retain these defaults:

| Budget | Default |
|---|---:|
| `max_nodes_per_side` | 10000 |
| `max_edges_per_side` | 30000 |
| `max_mapping_relations` | 20000 |
| `max_field_rows` (all axes together) | 200000 |
| `max_evidence_reads` | 20000 |
| `max_response_bytes` | 16000000 |
| `max_material_bytes` (one optional body) | 1048576 |
| `max_selection_cursors` (cached continuation handles) | 128 |
| `max_request_bytes` (UTF-8 JSON body and canonical representation) | 1000000 |

Every override must be a positive integer, excluding Boolean values, within the
JSON safe-integer range (1 through 9007199254740991). Unknown keys fail. Integer
wire representation, reference formats, string shapes and the occurrence-path
shape limit remain protocol validation, not resource settings.

The request/selection DTO still contains exactly the original six `limits`
fields. A client may lower these values; exceeding the configured HOST value
fails with `scope_limit` (HTTP 413). Underlying session `ReadLimits` still apply:
response bytes are clamped to the session response budget; optional material
reads use the minimum of HOST per-body bytes, session `max_material_bytes`, and
the remaining comparison byte budget; selection cursors use the minimum of HOST
cursor capacity and session `max_cursors`. Other session read gates remain in
force. The selection route advertises the effective six budgets, which the
browser sends back. HTTP parsing and both response validators use those budgets,
without an additional default-sized graph/row array ceiling. No output is silently
truncated. An oversized optional material body remains undisclosed as before.

For example, keep this policy in a separate trusted local
`comparison-policy.json`, never in an HTTP request or the closed read-HOST DTO:

```json
{
  "max_nodes_per_side": 20000,
  "max_edges_per_side": 60000,
  "max_mapping_relations": 40000,
  "max_field_rows": 400000,
  "max_evidence_reads": 40000,
  "max_response_bytes": 32000000,
  "max_material_bytes": 8388608,
  "max_selection_cursors": 256,
  "max_request_bytes": 2000000
}
```

```python
import json
from pathlib import Path
from cpn.frontend.comparison_context import ComparisonProvider
from cpn.frontend.server import serve_projection
from cpn.rpnh.collaboration.read_host_config import open_read_host_session

policy = json.loads(Path("/absolute/path/comparison-policy.json").read_text())
with open_read_host_session("/absolute/path/read-host.json") as session:
    provider = ComparisonProvider(session, host_limits=policy)
    serve_projection(provider, host="127.0.0.1", port=8765, open_browser=False)
```

The existing `read-host.json` `limits` object must also allow the intended session
budgets, for example `{"max_response_bytes": 32000000,
"max_material_bytes": 8388608, "max_cursors": 256}`. Its existing loader builds
typed `ReadLimits` for the HOST binding and an omitted-limits session request
that inherits those ceilings. For a direct Python session, configure
`RegistryReadHostBinding(..., limits=ReadLimits(...))`; omit request limits to
inherit them, or supply `ReadLimitOverrides(...)` to tighten selected fields.
An explicitly supplied `ReadLimits(...)` restricts every field it contains,
including its default-filled fields. Requests cannot raise HOST ceilings. The ordinary CLI launcher retains Comparison defaults;
use the explicit launcher above for Comparison overrides.

For custom HTTP integrations, pass the same trusted policy to
`parse_query(raw, *, host_limits=None)`,
`validate_request(value, *, host_limits=None)`, and
`validate_comparison_context(value, request, *, host_limits=None)`. The existing
server obtains it from `ComparisonProvider.host_limits`. These are local HOST
choices, not new request fields, grants, model settings or Registry writes.

## Verification boundaries

Deterministic Python tests exercise canonical owner issuers, real stored author
producers, real checkpoint publication, public sessions, permissions and cuts.
Node tests exercise strict DTO validation and cancellation; a controlled-DOM test
runs the actual dual NetRenderer lifecycle with real ELK geometry. Those are not
browser paint, accessibility, mobile behavior, OS isolation, or a malicious-HOST
security audit. Browser/loopback checks blocked by the execution environment must
be completed in a supported local environment without bypassing its restrictions.
