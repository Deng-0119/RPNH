---
name: rpnh-runtime-registry-reference
description: "Reference the sole owner, dispatch, marking and atomic Registry boundary."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: runtime-registry_ZH.md
  revision: "2026-10-04.1"
  status: source-reviewed-v0.1.0rc1
  basis: "core; adapter differences explicitly labelled"
---

[English](runtime-registry.md) | [中文](runtime-registry_ZH.md)

# Runtime, marking, RunOwner and Registry

## Owner and harness collaboration
`RunOwner` is an advanced trusted-host interface, not an observer constructor. The public `start_run(module, registration, **owner_arguments)` delegates to `cpn.rpnh.run.start_run`; it creates/publishes a fresh lineage and does not automatically execute a model or tool. Required host arguments must follow that function's actual signature and selected configuration, not an invented “run(prompt)” SDK.

`OwnerInput(schema_id, payload: bytes, summary)` carries original input. Owner methods include `admit(transition_id, *, logical_tau, command_id, prepare_admission=None)`, `start(admitted, *, command_id)`, `products(execution, *, outcome_id, products, command_id)`, `succeed(outputs, *, command_id)`, `terminal()` and `snapshot()`. They exchange registered authority objects, not arbitrary IDs copied from a display. `admit` raises `RuntimeError` while admission is paused for graph editing.

The harness constructor is:

```python
# Signature reference, not a call or an executable example:
# Harness(*, owner, event_loop, prepare_dispatcher, submit_operation,
#         select_ready=None, prepare_admission=None, max_in_flight=1)
```

It requires an actual `RunOwner`, that owner's `OwnerEventLoop`, callable dispatch/submission boundaries, and a positive integer `max_in_flight` (not bool). Wrong ownership/callback types raise `TypeError`; invalid concurrency raises `ValueError`. Concurrent physical work does not grant workers independent writer authority.

## Dispatch and result contracts

| Type | Responsibility | Important boundary |
|---|---|---|
| `OperationDispatch(execution, invoke)` | Bind one callback to exact admitted execution | `execution` must be `OperationExecutionAuthority`; `invoke` callable |
| `OperationProducts(authority, timing_observation=None)` | Supply already registered outputs | Arbitrary executor return data is not `RegisteredOperationOutputsAuthority` |
| `OperationDisposition(execution, kind, payload=None, resume=None)` | Represent resource wait, execution block or terminal handoff | `kind` is `resource_wait`, `execution_block`, or `terminal_handoff`; not Success |
| `HarnessResult` | Report goal/stop state, marking, trace and terminal evidence | Check `terminal_evidence_ref` and `completion_error`; text alone is insufficient |

Malformed boundary objects raise `HarnessBoundaryError`. Resource wait requires payload and a callable resume; other dispositions cannot supply a resume callback. The trace correlates execution lease, result, start event, output resources and budgets to exact committed identities.

`request_owner_stop()` requests safe owner work; it does not assert that physical execution has stopped. `RunOwner.record_owner_stop(*, idempotency_key)` records the authorized safe-boundary event, without signal handling, automatic settlement or opening a new writer. The launcher still owns stop authorization.

## Durable completion and bounded recovery
Host-neutral completion and recovery live in shared `cpn/components` and
`cpn/rpnh` modules rather than in a frontend adapter.

After validating the exact returned output bundle, the registered-operation path records `registered_operation_completion_recorded/v1` before returning products to the dispatcher caller. The event binds run, invocation, firing, execution lease, Start, admission, marking checkpoint, operation specification/binding, selected outcome, ordered output resource references and writer epoch. It is a durable executor-return proof, **not Petri settlement or a terminal answer**. Do not synthesize or append this event from an adapter or repair script.

On a process reopen, shared `resume_run` can settle one stale running firing with that exact completion, without invoking its HOST/provider/tool again. Registration, immutable identity and budget material are checked before writer admission. Missing, old-format, crossed or conflicting proof does not authorize replay. An empty writer-epoch gap is tolerated only when no later writer published a fact. Outcomes with declared HOST effects remain excluded. A workspace-bound completion is recoverable only when it names the exact immutable `workspace_revision_candidate/v1` held by a `map_ready` subordinate execution checkpoint; recovery validates that archive and uses it instead of rescanning the live workspace.

Owner-selected historical execution is a separate `reopen` protocol, not stale-firing recovery. It accepts any exact committed checkpoint in the current net lineage after the owner is stopped or terminal. One transaction records `run_reopen_authorization/v1`, supersedes the current token occurrences, clones the selected cut into fresh occurrences, advances each workspace lineage through an `owner_reopen` revision, commits a reentry checkpoint and installs a stopped authority with an incremented execution generation. The workspace head uses compare-and-swap against the prior head; mutable trees are restored before dispatch. Attempts retain the current high-water marks even when the selected marking is older. Retrying the same durable command is idempotent, while a new user command creates a new generation. Historical objects and terminal evidence are never rewritten.

The same owner-selected command may start from a recoverable running generation. Before recording the reopen authorization, shared Core deterministically enumerates every active firing and settles its exact registered completion or closes its unresolved call as owner-interrupted. Those per-firing closures are independently idempotent: if the replacement owner exits after closing one parallel branch, retrying the durable reopen command drains only the remaining active firings and then stages the selected cut. The narrow one-use stale-lease proof is bound to the exact task, run, invocation, firing, lease, result, transaction and event material; it does not authorize ordinary stale settlement.

The registered-host LLM boundary separately classifies exact v3 call facts when the same admitted execution re-enters. It may finish or return an already registered response without another physical call; a pre-existing submission permit without a response is `submission_unknown`. This same-execution handling is **not** cross-process recovery for a cut before the operation-completion event. Partial transport observations, headers, or locally completed writes do not establish remote completion. Provider-authoritative status queries/idempotency remain optional provider-contract capabilities, not a generic implemented guarantee for every route.

If an owner stop races with validated registered products already returned to the harness, the products settle first and the stop takes effect at the next safe boundary. A stop must not replace those products with an interrupted outcome and cause re-execution. Checkpoint reopen restores registered Petri/workspace state; it does not erase or undo external effects produced after the selected cut. Provider submission without registered completion first blocks as `submission_unknown`. A later owner-selected reopen may durably close that exact unresolved physical attempt as owner-interrupted and re-fire from the selected safe cut with a fresh identity; the original unknown fact remains immutable and is never an automatic retry.

For DSH headless exit handling, `terminal` and an `idle` whose latest turn is already committed are successful completion states. An empty or nonterminal idle session fails. Diagnostic states such as `submission_unknown` are failures, not partial success. A terminal record may contain a denied business outcome: command/protocol completion is not proof that the requested business action was approved. A durably registered provider response alone also does not prove that the enclosing task has reached terminal settlement.

Registry validation deliberately stops at structural integrity. Current events
must close exact identities, references, order and atomic transaction material;
they do not encode a universal retry/remediation policy. Current workspace
writers therefore omit `next_attempt_allowed`. The v1 schemas still accept that
field when reading already-persisted history, but its historical value cannot
override current runtime policy or block checkpoint reopen. Failed actions stay
visible and are interpreted by the declared operation and Agent runtime.

## Marking, resources and transaction ownership
`TeamNetMarking` is the facade/state owner. `_marking` splits selection, claims, outputs, revisions, checkpoints and delta implementation without creating another token/epoch/claim store. Registry-backed hydration provides the current graph/marking. Do not use a mutable display projection as a replacement runtime marking.

`EventStore` is the internal transaction coordinator with writer fencing. The implementation is organized under `registry/_event_store`: backend, queries, views, lineage, provenance, accounting, proposal and commit/validation. `publish_batch` consumes typed task/transaction identity, writer epoch, idempotency key, expected heads, prepared objects/events/relations and firing-publication material. It requires a nonempty fact-event batch and permits at most one firing settlement/publication in that transaction. `RegistryConflict` rejects inconsistent exact references/publication structure. Private domain helpers must use the caller's transaction; they are not independent public services.

`_ResourceServiceKernel` retains the live core, resource authority and delivery/publication boundary. `RunOwner.access_resource(execution, resource_ref, *, access_mode, command_id)` uses admitted execution and an exact resource version. `succeed` prepares workspace settlement before committed firing success. File existence or a candidate output cannot substitute for acknowledged registered access and settlement.

Subordinate execution Petri nets are same-Registry authority beneath one exact business invocation/firing. Their definitions, instances, weighted tokens, active/settled transition firings and checkpoint stream are distinct from `TeamNetMarking`; independent transitions may be active concurrently. Built-in file materialization and workspace-finalization nets reach `map_ready` only with exact Registry evidence. The business Success transaction requires every subordinate instance to be map-ready and stages `execution_terminal_mapping/v1` beside the operation result, successor marking checkpoint and optional workspace revision. It is therefore impossible for an execution checkpoint alone to consume or produce business tokens.

Deterministic `write_file` path, parent and destination-type faults are checked
before attaching a file-materialization instance. If the private filesystem
changes after that admission boundary, the mismatch is an integrity/recovery
failure on the same materialization identity, not a second model-correctable
action beside an active child. Snapshot restoration recreates ordinary files by
staged atomic replacement and never writes snapshot bytes into a FIFO or other
special file.

Instance attachment and Success sealing share one per-firing execution-child stream. Success compare-and-appends the sealed instance/checkpoint/mapping set, so an attachment racing with settlement makes one transaction stale instead of leaving an unmapped running child after business publication. Execution settle artifacts include their predecessor checkpoint and command identity; a losing stale attempt cannot reserve immutable version locators needed by the valid retry.

Every settled workspace revision carries sorted `path_deltas`. Each delta records create/update/delete, exact nullable before/after resource references and bounded summaries. Full archives preserve the bytes and modes; the delta chain preserves per-path version lineage. Finalization first freezes `workspace_revision_candidate/v1`; concurrent-head merge and final path deltas are still calculated by the Success owner against the current lineage head.

## Explicit preserved candidate plans (opt-in)

`cpn.rpnh.collaboration.preserved_candidate_publisher.PreservedCandidatePlanPublisher`
is an advanced trusted-host entry for one inert `collaboration_candidate_plan/v2`.
The Registry must explicitly include `candidate_v2_schema_data()`. The entry
requires the existing owner gateway, producer principal, explicit `Registration`
and a same-source plain closed Module revision. It accepts registered components
using the supported deterministic operation protocol without tools or optional
runtime HOST selections. The existing v1/default publisher retains its scope.

For nonempty `selected_slot_refs`, supply a typed `PreservedBasis` and explicit
`selected_schema_refs`. Each symbol must name the exact compatible slot in that
basis. Selected schemas must be the unique projection of the author's frozen
`schema_refs` onto those symbols' schema IDs, and must equal the basis outputs'
exact schema resource refs. Equal bytes under another ref do not substitute for
that identity. Empty selections require no basis or selected schema refs.

First publication rechecks static owner, author, wire, resource, slot and byte
evidence in the committing SQLite cut. The selected adoption event must still
be current. A stale basis is rejected. After successful publication, an exact
complete-request replay uses the frozen historical basis even after a later
adoption. v1 and v2 share the stable command/ID namespace; an exact existing v1
request returns its original format, while changed input conflicts.

An immutable prewrite file alone is not a registered successful plan. An exact
interrupted prewrite may retry first admission; it receives no historical
replay exemption. Changing its request cannot overwrite the same version file.
Use a new command for a newly selected basis.

The producer performs real compilation with the supplied Registration outside
the committing write transaction. The fixed Core gate validates static material
and mechanical wire compatibility; it does not attest which HOST callable
produced arbitrary supplied fragments. This entry publishes no graph, manifest,
readiness, adoption or execution authority. Graph-authoritative v1–v4 sources,
Assembly inputs and runtime provider/workspace bindings remain unsupported here.

## Read, recovery and compatibility
`snapshot(owner_or_client)` delegates to the existing owner or client; it does not open another writer. Owner snapshots expose exact run/task/net/checkpoint refs, declaration, marking, pending controls and enabled transitions, with `global_liveness` explicitly `UNKNOWN`. They do not grant completion authority.

Private classes, backend SQL helpers and `_event_store`, `_operation`, `_invocation`, `_provider_calls`, `_resource_service` domains are implementation details. Preserve facade/transaction compatibility when reorganizing them. Historical inert validation paths are not enabled by documentation. Recovery and data retention follow [usage](../guides/usage.md) and [troubleshooting](../guides/troubleshooting.md); DSH-specific commands are in the [DSH guide](../guides/dsh.md).

Sources: `cpn/rpnh/run.py:RunOwner,OwnerInput,resume_run`; `harness.py:Harness,OperationDispatch,OperationProducts,OperationDisposition,HarnessResult`; `marking.py`; `registry/event_store.py`; `registry/_event_store/commit.py`; `registry/_event_store/validation/`; `registry/execution_net.py`; `registry/execution_runtime.py`; `registry/checkpoint_reentry.py`; `file_execution_net.py`; `workspace_settlement.py`; `registry/firing_recovery.py`; `cpn/components/registered_operation_dispatcher.py`; `registered_host_llm.py`; `tests/test_execution_net_registry.py`; `tests/test_workspace_revision_history.py`; `tests/test_multi_output_same_turn.py`; `tests/test_registered_operation_recovery.py`; and `tests/test_dsh_backend.py`.
