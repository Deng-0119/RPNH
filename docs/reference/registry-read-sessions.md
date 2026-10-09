---
name: rpnh-registry-read-sessions
description: "Use explicit owner-issued observation authority with fixed-cut Registry reads."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: registry-read-sessions_ZH.md
  revision: "2026-10-09.1"
  status: implemented
---

[English](registry-read-sessions.md) | [中文](registry-read-sessions_ZH.md)

# Independent Registry read sessions

## Authority and setup

The installed read-only HOST can open an existing Registry in a second process
without creating a task, admitting an Invocation, starting a run, acquiring a
writer fence, or recording an Observation. The original owner must first issue
an observer profile and grant explicitly through its existing
`RegistryRegistrationGateway`. `issue_observer_access` and
`revoke_observer_access` in `cpn.rpnh.registry.observer_access` are trusted-owner
commands. Issuance is not part of browsing. A renewal uses a new explicit
command and session; it does not silently extend an old session.

The opt-in `observer_access_schema_data()` inventory adds a v2 profile and
grant plus a revocation record. Canonical owner publication validates exact
source, task, bootstrap, principal, command, writer fence, and governance head.
An arbitrary context, an accessible directory, an opaque source name, a
SourceSet member, a package digest, or a `HOST` string is not a grant.

`ObserverReadScope` separates index fields, record fields, exact material refs,
exact export refs, and export destinations. Field scopes name registered entry
types and fields explicitly. No permission is inferred from another permission.
The existing v1 metadata observer and managed Invocation paths retain their
original authority checks. A metadata-only observer cannot read material;
managed material delivery still requires the existing governed delivery gateway.

This is an owner-controlled local HOST boundary within one OS user. It does not
provide authentication between mutually hostile processes of that same OS user,
and it is not a remote multi-tenant authorization service.

## Installed entry

The independent journey uses a trusted local configuration file:

```bash
rpnh net --read-host-config /absolute/path/to/read-host.json --view
```

The file uses `rpnh/registry_read_host_config/v1`, an explicit `purpose`, and
selected `sources`. Each source row contains `source_ref`, `access_path`,
`registry_root`, `binding_generation`, and the already-issued
`observer_context`. The configuration reader derives the caller from the OS
user; requests cannot supply a principal or an importable adapter. The file
must be a regular, non-symlink file owned by the current user with mode `0600`
or `0400`, under trusted parent directories. Do not place it in a share package.

The optional `source_set` configuration selects one exact owner-published
SourceSet and its configured manifest Registry. It restricts the selected
sources to explicit members and access paths. It does not discover paths or
issue permission. The source file, identity, and content are rechecked at
access boundaries. Opening a missing Registry fails without creating it.

The installed adapter calls `open_readonly_source` and
`open_registry_session`; callers do not need private Registry constructors.
For a custom trusted HOST, `RegistryReadHostBinding` separates the already
verified caller, resolver, existing-authority provider, and typed-reader
catalog from `ReadSessionRequest`. The resolver returns a `ResolvedReadSource`
with an explicit binding generation. Closing a session releases its own reader
handles and memory buffers; it does not revoke grants or stop runs.

## Queries and cuts

`query_index` accepts a typed `IndexQuery`, a tuple of `TypedIndexClause`
values, and bounded `eq`, `in`, or catalog-declared numeric/time comparisons.
Unknown types, fields, projections, and predicate forms fail before scanning.
An undisclosed field cannot be used as a hidden filter or count side channel.
The stable order is source, entry type, logical identity, exact version.

Index entries contain exact source-qualified references and allowed fields.
They never inline resource bodies. `read_exact` applies separate record
permission and returns a fixed public projection. The supported finite typed
inventory and field directory are available from `describe()`; support for a
schema-validated descriptor does not imply material-semantic validity, ability
to lower/compile that type, adoption authority, or execution readiness.

A public `SourceCut` has exactly `source_id`, `cut_id`,
`head {ordinal, writer_fencing_epoch}`, and `reader_contract_version`. The
session retains the full internal Registry head and a bounded canonical
snapshot. Raw task-control and stream-head details are not returned. A
historical request provides a separate `HistoricalCutRequest`; a caller cannot
edit an existing handle's ordinal. An object canonically promoted after the
cut stays absent from that cut even if its provisional publication was older.

Continuation tokens are opaque session-local memory handles. They bind the
complete query, page size, source selection, cuts, current authority, resolver
binding generation, and exact reader/schema catalog. Ordinary canonical append
does not shift an existing cut. A changed filter, field, page size, source,
principal, binding, or catalog cannot continue from an old cursor. Replay of a
cursor is read-only and returns the same page at the same cut.

Limits default to a 100-entry page, at most 1,000 entries per whole page,
64 sources/clauses, 32 predicates/projection fields, 64 values per `in`,
64 KiB query input, 4 MiB response/material, 100,000 scanned rows, and
64 MiB of scan/retained canonical data. The HOST may tighten limits.
Scalar row/byte budgets run before unbounded history hydration. Each cut
hydrates history once, not once per indexed object. Over-budget reads return
`LIMIT_EXCEEDED` or a valid bounded continuation, never a false complete page.

## Fixed product-origin query

`session.query_product_origin_v1(root, at_cut, include=None, page_size=None,
cursor=None)` is a public Python session capability. The module-level convenience
function `query_product_origin_v1(session, root, at_cut, include=None,
page_size=None, cursor=None)` has the same behavior. Both are available through
`cpn.rpnh.collaboration`. The constants `PRODUCT_ORIGIN_PROFILE`,
`PRODUCT_ORIGIN_PAGE_SCHEMA`, and `PRODUCT_ORIGIN_CONTRACT_REVISION` identify
`product_origin_v1`, `rpnh/product_origin_page/v1`, and revision `2`.
There is no `fields` parameter or per-request limit override. The existing
comparison viewer and its HTTP endpoints do not expose this query.

### Exact root and request

Choose one already authorized source and capture a cut before querying. `at_cut`
must be an unchanged `SourceCut` issued by this session, with the same source as
`root`. The accepted root forms are:

- `SourceQualifiedResourceRef` wrapping the original two-field
  `ResourceVersionRef`: canonical `petri_output` or `workspace_write` whose
  producer is an Invocation
- `SourceQualifiedVersionRef` wrapping an exact `operation_result/v1`
  `VersionRef`, with `operation_result` / `operation_result_version` ID kinds

A generic `VersionRef` wrapping a resource, a name, path, bare ID, `latest`,
implicit source, or cross-source search is not accepted. The resource-origin
restriction applies only to the root, not to Start inputs or claim resources.
A supported request never fetches a newer head internally.

`include=None` requests all three relations, in the fixed order
`producer_execution`, `start_inputs`, `claims`. An explicit nonempty list or
tuple must contain unique strings and include `producer_execution`; any input
order normalizes to the same fixed order. For producer proof alone, pass
`include=("producer_execution",)`. This neither looks for Start nor enumerates
claim tokens or consumed claims. Adding only `claims` does not look for Start;
adding only `start_inputs` does not require the public Claims fields or return
claim classifications. Unsupported relations fail before object reads.

`page_size` is a positive integer; booleans are rejected. Its maximum is
`min(100, session.limits.max_page_size)`, and its default is
`min(20, maximum)`. Wrong types and nonpositive values return `INVALID_QUERY`;
an explicit value above the maximum returns `LIMIT_EXCEEDED`, without clamping.
These profile limits do not change the defaults of `IndexQuery` or `ReadLimits`.

### Required field permissions

Every requested record/index field is preauthorized before checking object
existence, completion counts, missing Start, or dependency integrity. Root access
alone is insufficient. Use the existing owner-issued `ObserverReadScope`; this
query issues no grant. Record and index permissions are separate.

| Entry type | Required record fields for every include | Conditional record fields |
| --- | --- | --- |
| `invocation/v1` | `invocation_ref`, `task_ref`, `net_instance_ref`, `own_transition_firing_ref`, `operation_binding_ref`, `operation_execution_lease_ref` | None |
| `transition_firing/v1` | `transition_firing_ref`, `task_ref`, `net_instance_ref`, `operation_binding_ref`, `firing_admission_ref`, `claim_marking_delta_ref`, `admission_marking_checkpoint_ref` | Start: `start_event_id`, `start_transaction_id`, `start_ordinal`, `start_input_binding_refs`, `start_input_resource_refs`; Claims: `claimed_input_refs` |
| `firing_admission/v1` | `firing_admission_ref`, `transition_firing_ref`, `invocation_ref`, `operation_execution_lease_ref`, `claim_marking_delta_ref`, `admission_marking_checkpoint_ref` | None |
| `firing_completion/v2` | `firing_completion_ref`, `transition_firing_ref`, `invocation_ref`, `operation_result_ref`, `successor_checkpoint_ref` | None |
| `operation_result/v1` | `operation_result_ref`, `invocation_ref`, `transition_firing_ref`, `business_outcome` | Resource root: `output_resource_refs` |
| `marking_checkpoint/v1` | `marking_checkpoint_ref`, `net_ref`, `settled`, `transition_firing_refs` | None |
| `resource_version/v1` | None | Resource root: `resource_id`, `resource_version_id`, `origin_kind`, `producer_ref`, `provenance_producer_invocation_ref`, `provenance_operation_binding_ref` |
| `marking_delta/v1` | None | Claims: `marking_delta_ref`, `net_instance_ref`, `phase`, `transition_firing_refs`, `operation_binding_refs`, `consumed_refs` |
| `petri_token/v1` | None | Claims: `petri_token_ref`, `net_instance_ref`, `resource_ref` |

Every include also needs the `firing_completion/v2` index fields
`transition_firing_ref`, `firing_completion_ref`, `invocation_ref`, and
`operation_result_ref`. A result root needs no `output_resource_refs` permission
and does not expand other outputs. Internal validation dependencies confer no
additional record or material disclosure rights. Existing default projections
remain unchanged; the six added reader types default only to their self reference,
and Start fields are opt-in.

### Page, proof, and rows

The successful response has exactly these fields: `schema_version`, `profile`,
`contract_revision`, `root`, `source_cut`, `access_revision`, `root_proof`, `rows`,
`coverage`, and `continuation`. The packaged
[page content schema](../../cpn/schemas/rpnh/product_origin_page.v1.schema.json)
is inert validation data, not a new Registry object or stored truth.

`root_proof` repeats on every page and has exactly six fields:
`producer_invocation_ref`, `transition_firing_ref`, `firing_completion_ref`,
`operation_result_ref`, `operation_binding_ref`, and `root_role`. All references
are complete and source-qualified. `root_role` is:

- `registered_output` when the exact resource is a member of the canonical
  result's `output_resource_refs`
- `invocation_produced_resource` for a nonmember resource whose entire required
  producing closure is valid
- `operation_result` for a result root

The nonmember role still requires the full producing closure. Sharing an
Invocation or having a `produced_by` link alone proves neither formal output
membership nor a successful profile query. The response exposes no other
outputs, private closure IDs, task/round/net/lease details, titles, or bodies.

There are exactly two row shapes:

- `start_input`: `role`, original zero-based `position`, `input_binding_ref`,
  `resource_ref`, `evidence`, and `verification`. The binding is a source-qualified
  generic resource or token `VersionRef`; the resource uses the two-field
  `ResourceVersionRef` wrapper. Evidence is exactly `start_event_id`,
  `start_transaction_id`, and `start_ordinal`. Event/transaction IDs remain typed
  strings. Verification is exactly `binding_identity="exact_at_cut"`,
  `resource_identity="exact_at_cut"`, `target_record="not_requested"`, and
  `material="not_read"`.
- `claim`: `role`, `token_ref`, nullable `resource_ref`, `classification`,
  `evidence`, and `verification`. Classification is `consumed_claim` or
  `non_consuming_claim`. Evidence is exactly `transition_firing_ref` and
  `claim_marking_delta_ref`. Verification is exactly
  `token_record="verified_at_cut"`, `resource_target="not_requested"`, and
  `material="not_read"`.

Start rows precede claims and retain original positions, including repeated
resources. Claims sort by full source/entity/logical/version identity; different
roles never merge. Start records the actual input resource version, which may
legitimately differ from the token's resource after substitution. A claim's
resource reference does not imply target metadata or body access. Producer-only
success has `rows=[]` and `continuation=null`.

### Coverage, paging, and finite scope

All mandatory producing closure and every requested relation candidate/endpoint
check finish before the first successful page. An invalid later item, missing
required witness, denied field, or insufficient validation budget prevents any
successful prefix or cursor.

`coverage` has only `scope="authorized_root_at_cut"`, `state`, and `relations`.
Producer execution is always `{state: complete, witness: verified_at_cut}`.
Requested Start/Claims use `witness="verified_at_cut"`; their state is `complete`
when cumulatively delivered through this page and `partial` otherwise. An empty
relation is complete even before another relation finishes. An unreached nonempty
relation is partial. Unrequested relations are exactly `{state: not_in_profile}`.
The following fixed unsupported labels always use that same shape without
checking existence: `direct_derivations`, `calls_in_execution`, `observed_reads`,
`formal_access`, `declarations`, `parent_child`, `recursive_ancestors`, and
`content_influence`. Asking for any of them returns `UNSUPPORTED_RELATION`.
There are no hidden, candidate, scanned, or total counts.

Global `partial` means only that fully validated authorized rows remain to be
delivered, with a nonnull continuation. Global `complete` has a null continuation.
Every page's entire envelope, repeated proof, coverage, and continuation count
against the response-byte limit. A page may contain fewer than `page_size` rows
for byte fit. If the necessary proof/envelope or one necessary row cannot fit,
the query returns `LIMIT_EXCEEDED`; it never returns an empty continuation page.
History capture, logical validation work, retained state, and response bytes
remain independently bounded by the existing session limits.

Continue with the unchanged full request, including root, cut, include, and page
size, plus the returned cursor. Reordered equivalent includes normalize equally.
Origin and index cursors share the session's slot limit and lifecycle but cannot
be interchanged (`CURSOR_MISMATCH`). Replay reuses the verified rows and next token
without allocating another slot. Current authority, source binding, schema/catalog,
and expiry are checked after serialization on success, replay, and protected-data
errors. The safe error response contains only `code`, a fixed `message`, and
`reopen_session`; authority/expiry failures override a pending data-dependent error.

Ordinary same-net sibling Success is supported. A legal changed-net settlement
returns `UNSUPPORTED_SETTLEMENT_SHAPE`; this query performs no bridge or replay.
Later canonical promotion cannot make an object visible in an older cut. A new
session must capture a fresh or explicitly historical cut and pass current
permission checks; old handles and cursors cannot be transferred.

`complete` describes this finite authorized root, cut, and selected relations.
It does not establish all workflow history, recursive ancestry, actual reading,
delivery acknowledgments, tool-call causality, or model/content influence.
See the [independent reader guide](../guides/independent-registry-reader.md) for a
full-request continuation example.

## Delivery and invalidation

Every page, exact record, body, projection, and export authorization receives a
final current-authority recheck, including session expiration and exact catalog
fingerprints after callbacks finish. The default maximum session lifetime is
ten minutes, shortened by HOST limits and any authority expiry.

Revocation, a changed writer fence or governance head, a different source/path
binding, or changed reader/schema contract invalidates affected cached data and
cursors. Query gaps retain null counts rather than pretending a source has zero
objects. A comparison consumer clears the whole required pair if either side
becomes invalid. `global_atomic_snapshot` is always false: per-source cuts and
final checks do not create a distributed atomic snapshot.

`read_material` is a separate bounded body operation. `representation="bytes"`
is the default; `representation="utf8"` strictly decodes verified bytes and
returns text in `body`. Invalid UTF-8 returns `INVALID_UTF8`, and unsupported
representations return `INVALID_REPRESENTATION`. The response retains `bytes`
and `sha256` for existing consumers, and includes `material_digest` and the
registered `content_schema_ref`. Limits and `byte_count` describe the original
bytes in either representation. Stored byte size and any recorded digest are
verified; a newly calculated digest is not described as a historical producer
checksum when the legacy resource has none. No encoding is guessed and no bytes
are silently truncated. Export authorization additionally
checks each exact ref and the explicit destination. Observation reads do not
create managed delivery receipts.

`capture_observation` explicitly returns
`UNSUPPORTED_OBSERVATION_CAPTURE`. The legacy SourceSet/Observation capture
contract remains available through its original API; ordinary new-session
pagination does not require it. Closed or expired handles cannot be reopened by
editing their wire values. Source-cut evidence retained in an exported package
is provenance, not transferable current permission.

## Validation boundary

The deterministic suite `tests/test_registry_read_session.py` exercises real
synthetic owner issuance, independent second-process configuration loading,
zero fact/fence writes, paging, separate scopes, fixed cuts, missing sources,
mid-read invalidation, and bounded scans. It performs no provider calls or real
external permission grants. These are ordinary functional regressions; they do
not replace the separately gated independent adversarial authority audit.
