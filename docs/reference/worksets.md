---
name: rpnh-worksets
description: "Explicit local Workset acceptance and read-only evidence."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: worksets_ZH.md
  revision: "2026-10-09.2"
  status: accepted-finite-browser-blocked
  basis: "finite local ordinary PN; optional collaboration inventory"
---

[English](worksets.md) | [中文](worksets_ZH.md)

# Local Worksets and acceptance

This opt-in adapter binds a caller-selected local source Registry. It preserves source-qualified request/export/logical-delivery identities and uses the original ResourceService physical prepare, release, consumption and terminal facts. There is no network transport or executor dispatch.

WorksetOwner creates an explicit requirements/input/generation/expected-slot scope. Growth and seal compare the exact prior version, stream head and command inside EventStore BEGIN IMMEDIATE. A seal freezes expected slots and still admits their results. A completed collection cannot reopen. `grow` is supported only while the collection is open and has no acceptances or contributions; it must strictly enlarge the expected slots. It rejects growth after results have arrived.

The ordinary RunOwner.succeed optional typed action stages Acceptance and its actual local deposited occurrence in the same original Success. An opt-in strong commit check replays the original pure PN projection, durable Start and real registered product publications (plus any existing dispatcher return record). Contribution must consume that accepted occurrence. Root completion must consume every contribution in a sealed Workset and produce the actual compiled terminal. RunOwner.terminal supplies the separate real run-terminal evidence. Workset seal never supplies required-child closure.

Same source-qualified logical delivery plus target Workset/collection/input/slot returns the existing Acceptance/occurrence for matching content; changed content conflicts. Physical P1 can remain unknown after target acceptance. An explicitly requested P2 uses a new physical command and may acknowledge the existing Acceptance; a separate reconciliation retains each original terminal. No unknown terminal is overwritten. Equal bytes alone never merge business identities.

Physical commands use stable attempt and release identities. Replaying a live command returns its existing consumed handle without another release. Reopening preserves canonical records, but a lost opaque channel cannot be reconstructed or consumed again; original ResourceService reports DeliveryOutcomeUnknown. The caller must reconcile the old attempt and explicitly request a new physical command.

The Worksets Viewer button and GET /api/v2/worksets share a read-only DTO. Current business collection and immutable history are separate. Physical totals remain null unless all named source delivery histories are explicitly supplied as read-only sources; their cuts are independent, not a global snapshot. Cancellation, stop and required-child seal remain not_provided when no corresponding producer exists. No raw result payload is returned.

The local acceptance path requires actual registered operation outputs and an
effect-free selected outcome. Acceptance decisions are exactly `new` or `carry`;
carry is caller-selected and still creates a target-specific acceptance.
This adapter does not select applicability, schedule executors, supply remote
transport or grant access to a source Registry. Revalidate, recompute and
retirement are not supported Workset actions. Arbitrary child/effect completion
is not supported; the separately implemented [normal-child contract](normal-child-root-contract.md)
defines the explicit child-completion route.

## Create and seal an expected collection

Use an existing `RunOwner` whose catalog includes `workset_schema_data()` and
whose `schema_gateway.bind_source_identity(...)` has established its local
source identity. Supply exact source-qualified registered requirements and input
refs. The following function creates and seals a one-slot collection; it does
not create acceptance, contribution or run-terminal evidence. Use a fresh
`command_prefix` for a new collection.

```python
from cpn.rpnh.collaboration import WorksetOwner, WorksetExpectation


def create_sealed_workset(owner, requirements_ref, input_binding_ref,
                          command_prefix):
    worksets = WorksetOwner(owner)
    created = worksets.create(
        requirements_ref=requirements_ref,
        input_binding_ref=input_binding_ref,
        generation=1,
        expected_slots=["result"],
        command_id=f"{command_prefix}:create",
    )
    expected = WorksetExpectation.from_record(worksets.core, created)
    return worksets.change(
        expected, action="seal", command_id=f"{command_prefix}:seal",
    )
```

For local delivery, bind the exact source owner with
`bind_local_workset_source(target_owner, source_owner, source_id=...)` and rebind
on reopen. A first `accept_delivery(...)` requires actual registered `outputs`
and `output_port`; replay can retrieve the existing acceptance without starting
another firing. A sealed collection still accepts its expected results.

The panel exposes A/O/C exact links separately. Accepted occurrence count preserves history; current availability is derived from the current RunAuthority/checkpoint at the same read cut. Active firing claims make availability not_provided rather than inferring that marking membership grants use. After contribution/root completion the original accepted O remains in history but is absent from the current marking.

Historical HTTP/Node and mock-DOM checks passed; the recorded actual-browser
startup was root/sandbox BLOCKED. The static acceptance fixture used mock
product bytes and callbacks that reject execution; it does not establish
arbitrary child/effect support. See [finite validation](../guides/release-validation.md).
The example above is syntax-checked documentation, not an executed task result.
