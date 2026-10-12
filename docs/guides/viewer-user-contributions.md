---
name: rpnh-viewer-user-contributions
description: "Inspect exact recorded proposal contributions on the adopted Petri net."
metadata:
  document-kind: guide
  audience: trusted-host-developer
  language: en
  counterpart: viewer-user-contributions_ZH.md
  revision: "2026-10-12.1"
  status: maintained
  basis: "original Registry facts; existing HOST and read-session authority"
---

[English](viewer-user-contributions.md) | [中文](viewer-user-contributions_ZH.md)

# Recorded contributions in the real Viewer

The **User contributions** panel overlays the existing complete Petri net.
Selecting a recorded actor dims other nodes without removing graph context.
Click a node in the panel to focus its actual PN node and inspect field paths,
exact proposal/base/candidate references, and separate review/adoption records.
Inherited changes retain the proposer color with a dashed outline. Multiple
retained proposers have an overlap style; unsupported provenance stays gray.
Runtime status text and fill remain separate from contribution outlines.

A recorded actor is a trusted local HOST session label. This is not remote
account authentication or proof that the actor personally wrote the content.
The panel says **recorded proposer**; authorship and human execution identity
remain unknown unless a future separately verified contract supplies them.
Neither publication principal nor model/provider/executor establishes authorship.
One changed author element may lower to multiple PN nodes: these are not several
independent contributions or an ownership claim.

## Trusted HOST integration

Use existing, already-authorized objects in the trusted HOST:

```python
from cpn.frontend.dashboard import RegistryDashboard
from cpn.frontend.user_contributions import UserContributionView
from cpn.frontend.server import serve_projection

view = UserContributionView(
    collaboration_host, collaboration_session, registry_read_session,
    source_id=selected_source_id,
)
provider = RegistryDashboard(
    existing_run_dir, catalog=existing_catalog,
    user_contribution_view=view,
)
serve_projection(provider, open_browser=False)
```

The HOST/session must retain the original target Core binding. The original
Registry read session must already authorize the selected runtime checkpoint,
plain revision descriptors, stored public PN projections, and their exact
material closure. This adapter does not issue grants, create sessions, widen
scope, infer credentials from JSON, register facts, compile a net, or execute
work. Missing configuration returns 501; changed/expired access clears the
browser overlay. Do not expose HOST objects to model tools.

The stock standalone Viewer cannot reconstruct trusted session handles from a
run path. Without the explicit binding it reports not configured. Existing
historical runs need their retained canonical Registry and original trusted
HOST/read setup. PNGs, exported Modules, presentation labels and display-only
summary JSON cannot reconstruct missing actor receipts.

## Exact meaning and current limits

- Each request pins one net, checkpoint and complete checkpoint commit ordinal.
- Adoption requires its canonical receipt, decision and exact resulting net/revision.
- Proposal deltas compare exact base/candidate author elements. Only retained
  values followed through explicit parent edges can color the adopted graph.
  Equal values in an unselected proposal do not receive credit.
- Original stored projection verification provides actual element-to-node lowering.
  There are no example-specific batch/node mappings or regenerated mock graphs.
- Initial coverage is plain closed v1 revisions with local parent ancestry.
  Cross-source history and selected-change ancestry are unsupported.
- Operation, port and component deltas are projected. Removed elements and
  module-only/edge-only changes do not have node overlays. Missing provenance
  stays unknown; inherited baseline is never attributed to the adopter.
- Detail shows changed field paths, not raw config values, prompts or tool outputs.
  The result remains a disposable observation, not a second fact ledger.

The response is bounded by fact, material, revision and response limits. Repeated
reads recheck both original access boundaries; source navigation invalidates
cached browser data. No provider/model call is needed to inspect retained facts.
