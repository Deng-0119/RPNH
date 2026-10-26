---
name: rpnh-registry-read-sessions
description: "Use explicit owner-issued observation authority with fixed-cut Registry reads."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: registry-read-sessions_ZH.md
  revision: "2026-10-25.1"
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
64 MiB of scan/retained canonical data. The HOST may tighten limits; material
policy is described separately below.
Scalar row/byte budgets run before unbounded history hydration. Each cut
hydrates history once, not once per indexed object. Over-budget reads return
`LIMIT_EXCEEDED` or a valid bounded continuation, never a false complete page.

## HOST material policy and operation bounds

`ReadLimits` retains a conservative 4 MiB material default. The trusted HOST may
configure `max_material_bytes` (one immutable body) and
`max_material_closure_bytes` (unique immutable bodies in one public projection
or mapping operation). All fifteen budgets, including response, scan, source,
page, cursor and lifetime ceilings, are trusted HOST policies. Their existing
defaults apply when the HOST omits settings; defaults are not a second hidden
maximum. Sparse requests inherit HOST values and can only tighten them.
An increased resource budget conveys no record, material, export,
Registry-write or PN authority.

A normal `ReadSessionRequest` uses sparse `ReadLimitOverrides`. Omitted fields
inherit the HOST ceiling; explicitly requesting `max_material_bytes=4194304`
keeps that 4 MiB restriction even when the HOST permits more. Each supplied
request value is clipped to the HOST ceiling. A sparse body restriction does
not also restrict the closure. `limits: {}` and an explicit default-valued
field therefore have different meanings. Wire values must be positive
integers; null, booleans, non-integers and unknown fields are rejected. Python
`ReadLimitOverrides` uses `None` internally for omission and omits it on wire.

Legacy full `ReadLimits` request objects remain explicit restrictions for all
fields. In a HOST `ReadLimits`, omitted `max_material_closure_bytes` follows
`max_material_bytes`, preserving the old single-knob policy. This fallback is
resolved when converting the policy to effective values; `dataclasses.replace`
on an unresolved policy preserves it. Effective session limits contain concrete
values, so replacing one effective field changes only that field. The owned
read-HOST file's `limits` is trusted policy, not a second default-filled request.
The file's ownership and unchanged-content checks still apply. Viewer/HTTP
requests cannot set this trusted policy. New serialized requests containing the
closure field require a reader with the updated request schema.

One public projection operation shares a temporary ledger with all nested
proof, topology, assembly, runtime-declaration and generated-correspondence
reads. `read_public_mapping` shares one ledger across both roots. Every fetched
immutable body, including descriptors, is charged once by its exact
source-qualified reference and verified registered size before a physical
read or the reader's cached return. Repeated references do not add unique
bytes, but material access still checks current authorization. Original pin,
byte, digest, metadata, fixed-cut and semantic proof checks remain required.
A new operation gets a fresh ledger; it does not mutate the session's snapshot
or publish Registry facts. Separate Viewer left/right projection calls are
separate operations, not a single Viewer-wide or concurrent-session quota.

Per-body overflow returns `MATERIAL_TOO_LARGE`; aggregate overflow returns
`LIMIT_EXCEEDED`. The aggregate now covers previously uncounted nested and
descriptor reads: unchanged numeric defaults can reject a large nested or
mapping read accepted by older accounting. This is intentional stricter
coverage. A lower `read_material(max_bytes=...)` still tightens its body limit.
Generic internal payload reads outside a public-projection operation keep
their previous default behavior.

Choose operator ceilings from the actual immutable proof and workload. Stored
envelope plus pinned dependency bytes can be only a lower bound because other
required descriptors also count. This change supplies no PN-size benchmark
establishing a larger default. This ledger bounds unique raw input bytes, not
peak Python heap, repeated I/O, parsing work, serialization scratch, total RSS,
or concurrent operations. Scan and response limits remain independent; they
do not certify all projection allocations. Changing ceilings never makes an
invalid or unauthorized proof acceptable and never repairs stored projections.

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

### Installed origin CLI transport

`rpnh origin --read-host-config HOST.json --root ROOT.json
[--include RELATION ...] [--page-size N]` is a thin adapter over this same public
query and the fixed built-in read-HOST catalog. The strict root file uses the
existing source-qualified reference schemas, rejects duplicate/unknown fields,
and never infers identity. It is limited to 64 KiB before decoding and to the
effective query-byte budget before capture. Default includes remain all three
relations; explicit includes repeat `--include` and require unique values with
`producer_execution` present.

One process owns one session and one source cut. Each stdout NDJSON line is the
unchanged original canonical page, with its original proof, coverage and
continuation. Only the current page/transport buffer is held; the transport
buffer is bounded by the effective response-byte limit plus one newline. The
adapter serializes before public `final_recheck((root.source_id,))`, writes and
flushes, then discards the buffer before requesting the next internal
continuation. It never aggregates pages or exposes cursor/resume inputs.

Exit `0` requires the terminal page to be flushed and the session to close.
A later error can leave earlier authorized lines, and sink failure can leave
an unfinished last line: any nonzero exit means the stream is incomplete.
The session closes on failure, including a sink failure. Stderr uses only fixed
safe codes, with no exception message, path, or grant contents. There is no
persistent cursor service, automatic reopen, arbitrary catalog/module selector,
writer access, or material expansion. See the
[CLI guide](../guides/independent-registry-reader.md#stream-one-products-origin-from-the-cli)
for commands and root-file details.

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

