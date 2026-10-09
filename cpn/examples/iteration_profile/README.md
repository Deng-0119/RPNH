[English](README.md) | [中文](README_ZH.md)

# Inert iteration profile authoring (R1, delivery revision 2)

`propose_evaluate_select.json` is one **declaration-only** example. It is not a
runnable RSI campaign and does not implement a scorer, proposer or selector.
The optional authoring convenience API lives beside the existing Module/compiler
APIs in `cpn.rpnh.iteration_profile`; it adds no runner, Registry type or scheduler.
This delivery replaces the unmerged first R1 package. That package emitted empty
terminal configs, so its Module could compile and settle products without
publishing a native run terminal. Do not integrate that superseded package.
The still-unpublished candidate schema/template IDs remain v1; the new required
`terminal_outcomes` field means old candidate profiles are rejected. This is not
a claim of backward wire compatibility.

It does not modify the frozen [RRSI v0.6 example](https://github.com/Deng-0119/RPNH/tree/main/examples/rrsi_v06).

## Implemented API

```python
from cpn.rpnh.iteration_profile import (
    load_iteration_profile, compile_iteration_profile,
)

profile = load_iteration_profile("propose_evaluate_select.json")
# registration is the caller's already prepared, trusted Registration.
prepared = compile_iteration_profile(profile, registration=registration)
module = prepared.module
compiled = prepared.compiled
```

`IterationProfile.from_dict(...)` and `.from_json(...)` accept the same closed
`rpnh/iteration_profile/v1` schema. The sole supported template is
`propose_evaluate_select/v1`. The schema is an explicit optional content inventory
from `iteration_profile_schema_data()`, using the existing `SchemaCatalog`;
it is not added to the mechanical Registry schema index. There is no new template
registry, import loader, generic model client, `rpnh rsi` command or run method.

Loading reads only the selected UTF-8 JSON file (at most 65,536 bytes). Duplicate
keys, unsupported fields/versions/templates, non-finite values, noninteger rounds,
and different budget units are rejected. Compilation revalidates direct Python
construction. This is a narrower implemented subset of the future RSI design:
no training, test dataset, fixture loading, child orchestration, promotion,
arbitrary graph, parallel-candidate or retry configuration is accepted.

## Trusted HOST requirements

The caller explicitly prepares a `Registration` with:

- The existing `register_basic_components(registration)` from
  `cpn.components.basic`. This v1 template requires its shipped `lower_operation`
  under the `operation` key; a different lowerer is rejected before invocation.
- The four selected application schemas: state, candidate, evaluation request,
  evaluation result. These are caller-owned versioned contracts, not new
  mechanical or generic scoring schemas.
- The exact proposer/evaluator/selector executor keys and terminal tool key.
  Each executor/tool is resolved and its registered config schema is checked by
  the original compiler. This template supplies empty executor config and explicit terminal-tool
  config `{"run_outcome": <selected mapping>}`. The tool's config schema must admit
  the chosen native `complete`/`failed` value; incompatible HOST config fails
  at compile time. The standard binding protocol is `rpnh/module_terminal/v1`.

Compilation never registers these bindings itself and never calls the executor
or terminal tool. The existing trusted pure component lowerer is called by
`compile_module`. Used schemas must be inline or use local-fragment `$ref`s;
external schema references are rejected before compilation can retrieve them.
As with the original compiler, caller-supplied trusted Python Registration code
is a trust boundary, not a sandbox for hostile Python implementations.

`model_profile_ref` is required for each role but can be `null`. A non-null value
is only a nonsecret, opaque reference to a user-owned profile. It is returned in
`required_model_bindings` for each expanded operation. No profile file, credential,
environment secret or provider is read; no model is inferred or substituted.
These obligations are not actual registered-model bindings and the Module alone
does not certify model identity. An authorized future HOST integration must
resolve and bind the selected exact profile through the existing model boundary.
Do not put credentials or private profile contents in this field.

## What is actually compiled

For each of 1–32 rounds, the trusted template creates three standard components:

1. Proposer consumes `state` and produces `incumbent` plus `candidate`.
2. Evaluator reads that candidate and consumes its round-specific validation
   `request`, then produces `evaluation`.
3. Selector consumes incumbent, candidate and evaluation. `select` and `retain`
   produce `next`; `stop` produces a distinct terminal carrier.

The shared candidate place is fused through ordinary Module links; it is not
broadcast by an external dispatcher. Every place has capacity one. Each later
proposer consumes the previous selector's `next`. There are no runtime Python
iteration counters, ready queues, retries or winner state. The Python loop only
expands a finite declaration during compilation. Early `stop` and the final
round's `select`/`retain` use existing terminal bindings/alternatives. A terminal
binding is a declaration, not terminal evidence. The original owner alone can
publish the exact terminal after native validation of its settled product.

Every profile must explicitly supply all three `terminal_outcomes` values:

- `stop`: the native result for the domain `stop` outcome in any round, including
  the final round
- `final_select`: the native result when the last declared round selects
- `final_retain`: the native result when the last declared round retains

Each value is `complete` or `failed`; there is no default. They map directly to
existing `TerminalBinding.config.run_outcome`. No new native outcome, outcome
inference or `run_terminal` field is introduced. Fixed round exhaustion is the
last round's select/retain, not an implicit success rule. The example explicitly
chooses three `complete` values for normal protocol completion. This does not
prove a candidate improved or that unknown evaluation became known. A domain
may instead declare `failed` for any of the three cases. A single domain `stop`
that needs multiple native meanings cannot be expressed by this template.

Domain evaluation UNKNOWN is distinct from physical execution uncertainty: a
HOST may legitimately retain a valid incumbent after an unknown evaluation, if
its declared domain contract permits that completed decision. Provisional
outputs, an unsettled/unknown physical execution or owner interruption do not
become terminal because the profile maps `stop` to `complete`. The original
Registry settlement, active-claim, exact-output and authority checks still
apply. Domain `stop` does not request or override an owner stop.

Round and operation progression bounds come from the finite PN structure. There
is no generic operation-attempt accounting counter in this convenience layer.
`native_model_call_budget` explicitly uses `registered_model_call` and a positive
maximum; it is **independent of rounds**. It maps only to the existing
`ModuleBudgetDeclaration.ordinary_global_cap` and `task_total_hard_cap` model-call
contract, preserving its registered returned-model-call accounting semantics
(`ModelCallCapProjection.actual_returned_calls`). This is not a count of all
dispatches or physical attempts. The sample's value 1 does not mean one operation: its two rounds expand
to six declared operations. The existing native contract requires positive or
null call caps, so this field does not offer a zero-call cap.

Each role has an explicit native scope/bucket binding with `max_attempts=null`;
we do not claim that this field counts arbitrary operation firings. Terminal and
finalization allocations are zero. These are construction materials for an
existing owner, not a new ledger or permission to make a model call. This
compiled template has no provider request operation or automatic model adapter.
Any future registered-model integration must preserve the existing call/attempt
accounting semantics and establish its actual coverage. No USD, token, training,
cross-child or hidden-executor-work ceiling is implemented here; do not equate
registered model-call counts with complete provider physical-call coverage.

The inert result contains:

- `module` and the original `compile_module` result (`compiled`)
- `budgets`, matching the Module's actual bucket declarations
- `source_map`: role JSON paths and compile-time round/component names only
- `required_entry_bindings`: initial state and one validation request per round,
  each with its exact selected schema
- `required_model_bindings`: unresolved model-profile obligations

The compiler's existing `compiled.to_dict()["registrations"]` supplies the
resolved HOST declaration inventory. The source map is diagnostic, not a
source-qualified author revision. No Registry references are minted here. Only the profile JSON source is frozen;
returned Module/compiled/budget containers retain the existing shallow-mutability
contracts and must be recompiled/revalidated after any caller edits.

## Explicit limits and next gates

The required initial state may contain only approved incumbent/feedback data,
and evaluation requests are intended for validation. The template gives the
proposer no evaluation-request input. **This is not a runtime data-isolation or
leakage proof.** No request body is loaded or semantically inspected here.
Caller schemas/executors and the existing Registry/HOST resource access boundary
must enforce exact candidate/dataset/scorer/model identities, split permissions,
comparison conditions, missing/unknown results and score/cost semantics.
`select` is an outcome name, not an implementation of a comparison rule. This
package has no task-specific scorer and changes no RRSI scores or protocol limits.

The convenience API provides no publication or execution entry. Applications
still use the existing
`ClosedModuleAuthor`, `start_run`, Registry admission/budgets and exact terminal
reader. Do not run a prepared Module while ignoring its unresolved obligations.
R2 must supply generic trusted domain operations, exact resource bindings and
single-Registry evidence. R3 must establish the full supported native/scripted lifecycle and transport,
interruption and recovery behavior. Full RRSI campaign/Digester parent-child
migration awaits the native child seam; teacher/student training and real models
need separate implementation and authorization. The revision's narrow socket-free Registry terminal probes below do not pass
those complete application/native gates.

## Deterministic checks

From the repository root, in its existing test environment:

```bash
python -m pytest -q tests/test_iteration_profile.py tests/test_compiler_json_contract.py
```

The inert checks compile/rehydrate declarations and validate schemas/products.
They inject HOST functions which fail if called and reject socket, SQLite,
subprocess, environment lookup and registration writes during the inert compile
check. The convenience functions still create no Registry or owner.

Separate terminal regressions deliberately create a temporary real SQLite
Registry with the original `start_run`, then use explicit `RunOwner.admit → start
→ products → succeed → terminal` test probes. All three mappings are tested with
both `complete` and `failed`, followed by exact read-only terminal reconstruction.
A historical empty-config control stays running; provisional output and owner
interruption produce no terminal evidence. These tests do not invoke business
executors, a scheduler, Orchestrator, event loop, socket, provider or model.
They prove the specific native terminal publication contract without claiming a
full RSI campaign or transport lifecycle. No external service is required.
