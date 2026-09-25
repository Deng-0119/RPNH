---
name: rpnh-display-observation
description: "Define the read-only display observation boundary and evidence limits."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: DISPLAY_OBSERVATION_ZH.md
  revision: "2026-09-25.1"
  status: source-reviewed-pre-release
---

# Read-only display observation (current state only)

`cpn.rpnh.inspection.project_registry_observation(run_dir, *, catalog)` is an
opt-in Python API for a trusted dashboard host. It does not change the existing
`project_registry_net()` or `rpnh/net_view/v1` contract. It does not add a CLI
command or a browser route.

```python
from pathlib import Path
from cpn.rpnh.inspection import project_registry_observation
from cpn.rpnh.registry.schema_catalog import SchemaCatalog

snapshot = project_registry_observation(Path("/path/to/existing/run"), catalog=SchemaCatalog())
```

The caller supplies the run's actual catalog; the default shown above is only
appropriate for a run that uses that catalog. The response is
`rpnh/net_observation/v1` with:

- `source`: one existing run, task ID, exact net reference, observed head ordinal
  and writer epoch;
- `net`: an unchanged v1 current-state Petri-net projection, including formal
  marking counts from the verified checkpoint;
- `boundaries`: declared entry/exit ports, places and terminal rules; declaration
  is **not** proof that a terminal event occurred;
- `transition_bindings`: exact existing node, operation binding and executable
  binding references for each transition, together with its compiled operation
  and scoped input/output port IDs and places; not a new step identity;
- `coverage`: explicitly marks firing history and token trajectories as
  `not_provided`, and as-of history as `unsupported`.

The reader opens the Registry with `create=False, read_only=True`, uses the
existing validated current-runtime hydration, and checks the head ordinal and
writer epoch before/after assembling the result. If either changes, it rejects
the mixed snapshot; callers may retry later. An absent run is not created.
It does not take a writer lease, invoke an executor, recover a run or publish
anything. No additional facts are stored in the Registry.

The pre-existing v1 projection includes operation configuration. Hosts must
apply their own disclosure policy **before** exposing it to a browser or caching
it; switching the view does not authorize more data. The new boundary response
does not copy terminal tool configuration or reuse `node_synopsis` as UI text
(that field also participates in model prompts). The edge IDs in v1 remain
scoped to the projected declaration, not global cross-version IDs.

This is current observation, not historical replay. In particular,
`hydrate_module_runtime()` selects the **latest** adopted net and checkpoint.
Relabelling it with an older ordinal, treating a full `ordered_firing_record()`
as an as-of record, or granting provisional observations canonical authority
would misstate Registry facts. A bounded canonical checkpoint index may be a
later, separately verified read-only addition; provisional detail, transaction
boundaries, publication-time visibility, concurrent settlement predecessors and
structure migration require their own focused validation before a timeline is
claimed. UI grouping, card descriptions and animation remain outside main.

A visible provisional firing, live workspace file, or process exit is not a
terminal result. Dashboards must label such files as unsettled and must report
completion only when the Registry contains canonical `run_terminal_evidence/v1`
and `final_result_index/v1` objects. The viewer is read-only; it neither settles
the firing nor turns a provisional workspace view into a registered revision.

The display-observation API itself remains isolated to `cpn/rpnh/inspection.py`;
it changes no identity, Registry, Petri admission, settlement, provider, resume
or schema code. Focused tests cover v1 compatibility, declared multi-boundary
projection, missing-run noncreation, mixed-head rejection, and a real SQLite
writer commit during observation. Product, readability and browser acceptance
remain separate from this observation contract; see the
[viewer guide](guides/viewer.md).
