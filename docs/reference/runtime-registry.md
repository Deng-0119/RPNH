---
name: rpnh-runtime-registry-reference
description: "Reference the sole owner, dispatch, marking and atomic Registry boundary."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: runtime-registry_ZH.md
  revision: "2026-09-25.2"
  status: source-reviewed-not-final-candidate-acceptance
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

## Durable completion and bounded recovery on the DSH line
Host-neutral completion and recovery live in shared `cpn/components` and
`cpn/rpnh` modules rather than in a frontend adapter.

After validating the exact returned output bundle, the registered-operation path records `registered_operation_completion_recorded/v1` before returning products to the dispatcher caller. The event binds run, invocation, firing, execution lease, Start, admission, marking checkpoint, operation specification/binding, selected outcome, ordered output resource references and writer epoch. It is a durable executor-return proof, **not Petri settlement or a terminal answer**. Do not synthesize or append this event from an adapter or repair script.

On a process reopen, shared `resume_run` can settle one stale running firing with that exact completion, without invoking its HOST/provider/tool again. Registration, immutable identity and budget material are checked before writer admission. Missing, old-format, crossed or conflicting proof does not authorize replay. An empty writer-epoch gap is tolerated only when no later writer published a fact. Automatic settlement excludes outcomes with declared HOST effects or workspace bindings; these require their own durable effect/revision instructions.

The registered-host LLM boundary separately classifies exact v3 call facts when the same admitted execution re-enters. It may finish or return an already registered response without another physical call; a pre-existing submission permit without a response is `submission_unknown`. This same-execution handling is **not** cross-process recovery for a cut before the operation-completion event. Partial transport observations, headers, or locally completed writes do not establish remote completion. Provider-authoritative status queries/idempotency remain optional provider-contract capabilities, not a generic implemented guarantee for every route.

If an owner stop races with validated registered products already returned to the harness, the products settle first and the stop takes effect at the next safe boundary. A stop must not replace those products with an interrupted outcome and cause re-execution. These changes do not establish arbitrary crash recovery, general retry, or rollback of external effects.

For DSH headless exit handling, `terminal` and an `idle` whose latest turn is already committed are successful completion states. An empty or nonterminal idle session fails. Diagnostic states such as `submission_unknown` are failures, not partial success. A terminal record may contain a denied business outcome: command/protocol completion is not proof that the requested business action was approved. A durably registered provider response alone also does not prove that the enclosing task has reached terminal settlement.

## Marking, resources and transaction ownership
`TeamNetMarking` is the facade/state owner. `_marking` splits selection, claims, outputs, revisions, checkpoints and delta implementation without creating another token/epoch/claim store. Registry-backed hydration provides the current graph/marking. Do not use a mutable display projection as a replacement runtime marking.

`EventStore` is the internal transaction coordinator with writer fencing. The implementation is organized under `registry/_event_store`: backend, queries, views, lineage, provenance, accounting, proposal and commit/validation. `publish_batch` consumes typed task/transaction identity, writer epoch, idempotency key, expected heads, prepared objects/events/relations and firing-publication material. It requires a nonempty fact-event batch and permits at most one firing settlement/publication in that transaction. `RegistryConflict` rejects inconsistent exact references/publication structure. Private domain helpers must use the caller's transaction; they are not independent public services.

`_ResourceServiceKernel` retains the live core, resource authority and delivery/publication boundary. `RunOwner.access_resource(execution, resource_ref, *, access_mode, command_id)` uses admitted execution and an exact resource version. `succeed` prepares workspace settlement before committed firing success. File existence or a candidate output cannot substitute for acknowledged registered access and settlement.

## Read, recovery and compatibility
`snapshot(owner_or_client)` delegates to the existing owner or client; it does not open another writer. Owner snapshots expose exact run/task/net/checkpoint refs, declaration, marking, pending controls and enabled transitions, with `global_liveness` explicitly `UNKNOWN`. They do not grant completion authority.

Private classes, backend SQL helpers and `_event_store`, `_operation`, `_invocation`, `_provider_calls`, `_resource_service` domains are implementation details. Preserve facade/transaction compatibility when reorganizing them. Historical inert validation paths are not enabled by documentation. Recovery and data retention follow [usage](../guides/usage.md) and [troubleshooting](../guides/troubleshooting.md); DSH-specific commands are in the [DSH guide](../guides/dsh.md).

Sources: `cpn/rpnh/run.py:RunOwner,OwnerInput,resume_run`; `harness.py:Harness,OperationDispatch,OperationProducts,OperationDisposition,HarnessResult`; `marking.py`; `registry/event_store.py`; `registry/_event_store/commit.py`; `workspace_settlement.py`; DSH-line `registry/firing_recovery.py`, `cpn/components/registered_operation_dispatcher.py`, `registered_host_llm.py`, `tests/test_registered_operation_recovery.py` and `tests/test_dsh_backend.py`.
