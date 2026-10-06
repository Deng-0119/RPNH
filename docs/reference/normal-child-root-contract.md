---
name: rpnh-normal-child-root-contract
description: "Explicit local normal execution-child closure in the original Workset Success."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: en
  counterpart: normal-child-root-contract_ZH.md
  revision: "2026-10-06.1"
  status: accepted-finite-browser-blocked
---

[English](normal-child-root-contract.md) | [中文](normal-child-root-contract_ZH.md)

# Normal execution children and Workset root completion

This optional local adapter extends [Worksets](worksets.md) with the explicit
`execution-v1-normal-only` closure profile. It connects already registered
normal `ExecutionRuntime` children to the parent's original ordinary Success
and a fully readable `collaboration_root_terminal/v2`. The caller chooses the
child definition, content schema, expected business slots, output port, exact
Workset expectation, registered products, command and budgets. The adapter does
not infer an application's completion policy or dispatch child computation.

## Select the contract explicitly

Use `normal_child_root_schema_data()` to obtain the existing Workset inventory
plus `execution_child_seal/v1` and `collaboration_root_terminal/v2`. Construct the
Registry's catalog explicitly from the returned documents, definitions and
paths. Merely importing the public symbols does not register the new types.
`workset_schema_data()` and prior catalog entry points retain their original
inventories, including the existing transplant and Assembly opt-ins.

After the parent has actual registered products and all of its children have
settled, call
`WorksetOwner.complete_normal_children(expected=..., outputs=..., output_port=..., command_id=...)`.
`expected` is an exact `WorksetExpectation`; `outputs` is the real
`RegisteredOperationOutputsAuthority` from the original parent products. The
explicit typed action is `CompleteWorksetNormalChildren(expected, output_port)`.
The facade uses that action with the original `RunOwner.succeed` and adds strict
completion replay. Neither entry accepts a caller-created seal or a selected
subset of children.

Workset/v1 wire format is unchanged. Its `required_child_seal_ref` is created as
`None` and remains unchanged in every successor. The new seal is referenced only
by RootTerminal/v2; the Workset's generic `terminal_ref` points to that root.
The original `CompleteWorkset`/RootTerminal/v1 route keeps both refusals: its
root child-seal field must be null, and the parent must have no execution
children. Business `expected_slots` remains nonempty. An empty-child path does
not mean an empty business collection.

## One Success transaction, the complete child set

The seal is a real immutable object with an exact VersionRef, not a seal event
ID presented as an object reference. The original Success transaction publishes
the child seal, terminal mappings, RootTerminal/v2, Workset/v1 successor,
operation result, firing completion and successor business checkpoint together.
Existing parent products and child state were previously committed under that
parent's provisional publication and become canonical through this Success.
`RunOwner.terminal()` remains a separate later transaction for run-terminal
evidence; root completion does not call it or imply that evidence exists.

The required set is every execution instance belonging to the exact local
source/task/run and parent invocation/business firing. Definition, attach fact,
admission checkpoint, complete checkpoint history and latest child checkpoint
must agree. Each child must be normally `map_ready`, have no active firing, have
valid terminal tokens and provide nonempty, readable evidence. The seal binds
the exact parent refs, child stream and pre-seal head, child/evidence/mapping
refs, result, successor checkpoint, Success command and transaction.

Commit validation re-enumerates the whole set in the original writer snapshot
and checks the child-stream head by CAS. Missing, duplicate, extra or wrongly
owned children fail. A newly committed attach makes an older closure proposal
stale; a closed parent rejects later producer-attributed child attachment or
progress. The closing Success batch cannot introduce execution instance,
definition, checkpoint, token or transition-firing objects, or an attach event.
The existing lease, writer, adoption, product, terminal, all-contribution
consumption and other-active-firing checks still apply.

## Normal-root token allocation and persisted capability

New explicit normal-root completions use the settlement delta scalar
`ordinary_token_ref_scheme: "normal_root_firing_scoped/v1"`. Only the exact
`CompleteWorksetNormalChildren` action selects this scheme. Existing token refs
are retained. New token identities use the exact original net and business
firing refs plus the token ordinal in separate versioned logical/version UUID
namespaces. Repeated proposals at the same firing and ordinal are deterministic;
changed immutable material still fails the original no-clobber check.

A refused root proposal may leave immutable prewrites. Its new token namespace
is separate from the legacy namespace used by a later ordinary acceptance or
contribution at the same ordinal. Prewrites are preserved. This bounded change
does not repair the general legacy ordinary-Success allocation risk.

The writer closes the source, native genesis, native run, bootstrap command and
initial catalog from one database snapshot before token publication. That
persisted catalog must explicitly support this exact optional scalar and its
settlement/single-firing rule. The real delta is validated against its stored
schema source. A newer HOST schema cannot grant capability to an old catalog;
this profile includes no catalog upgrade or metadata replacement.

Commit independently binds the selector to the actual same-Success root/v2,
seal, completion, result, delta and checkpoint before ordinary/revision dispatch.
An unknown, null, misplaced or live-normal missing selector is rejected. Effect
and revision outcomes are outside this normal allocation profile. Plain legacy
Success and empty-output behavior keep their existing defaults.

Historical normal roots without the scalar use only the original legacy
formula. Tagged roots use only the new formula and the catalog at their original
pre-Success cut. Readers never try both formulas. Exact public replay reads the
original closure before the new-write capability gate, preserving old root refs
and their original catalog authority.

## Supported evidence bytes and historical schema authority

The new profile has a finite evidence codec contract:

- JSON descriptors of exactly `execution_instance/v1`,
  `execution_net_definition/v1`, `execution_checkpoint/v1`,
  `execution_token/v1` and `execution_transition_firing/v1`, with
  `application/json` bytes equal to their validated metadata
- `resource_version/v1` with schema-backed JSON or UTF-8 `text/*` content,
  validated against its exact supported self-contained Draft7 schema authority

Validation checks real bytes, self identity, producer, task, round, net, size,
media type and exact refs. The schema authority must already have been canonical
before the evidence was registered, including its publication, transaction and
every required promotion terminal. A schema becoming canonical later cannot
retroactively authorize earlier evidence. Evidence must precede child settlement.

Unsupported opaque evidence, an unknown descriptor type, or a resource without
the required codec/schema validator is rejected by this profile. A claimed
JSON media type alone is insufficient. The existing `ExecutionRuntime.settle`
contract for same-parent refs is unchanged; an accepted settlement is not itself
proof that the new root-closure profile can validate its evidence.

## Full reads, strict replay and versioned views

`read_root_terminal(read_only_core, exact_root_ref)` requires a read-only Registry
Core and a source-qualified RootTerminal/v2 ref. It returns the verified root,
Workset, seal, successor checkpoint ref and `verified_at_cut`. It uses one local
database snapshot to recheck immutable bytes, publication and promotion,
Workset history, actual contribution consumption, compiled Module terminal
binding and the same-Success closure. Child history is selected at the original
Success cut; the current snapshot also checks that the closed parent acquired
no later child state. Generic `read_record` is not this complete reader.

Calling `complete_normal_children` again is a read-only replay only when the
original command, exact parent refs, Workset expectation, profile, selected
outcome, output port and entire registered output bundle match. Changed material
is rejected. Matching replay returns the verified original closure without
another Success, I/O revalidation, workspace planning or edit advance. Default
`RunOwner.succeed` retry behavior remains unchanged.

The Workset projection preserves `rpnh/workset_view/v1` for v1-only data. A
supported root/v2 produces explicit `rpnh/workset_view/v2`; its separate
`root_child_closure` contains the profile, root ref, seal ref and verified local
cut. The old projected Workset child-seal field remains null. The server and
JavaScript consumer recognize both view versions and reject unsupported or
inconsistent closure data. This local proof does not establish a global cut or
complete physical delivery coverage across source Registries.

## Validation boundary

freeze04 has independent finite acceptance for normal-root retained refs/bytes
across transactions, separate T01 run-terminal evidence, ordinary revision,
legacy-catalog rejection, twochild/exact replay/cold v2 reads, closed-parent
refusal, seven damaged-copy read-only refusals, thread attach-wins and separate
child-stream CAS. A separate R02 window establishes one explicit Success-wins
order. These bounded windows supersede the earlier allocation-repair NOT_RUN status.

The writer's dynamic 64 cases and separate R02 case remain distinct. Independent
integration has 8 windows, 31 executions and 30 distinct nodes; earlier pins are
not relabelled freeze04. HTTP/Node and mock DOM passed; actual browser startup
was root/sandbox BLOCKED, with no page/real-DOM/screenshot acceptance. See
[finite validation](../guides/release-validation.md).

This does not guarantee arbitrary scheduling/child counts, general typed-call
dispatch, recursive child execution, TaskControl spawn, remote effects,
cross-run capacity or full I02/HOST/advanced25 acceptance.
