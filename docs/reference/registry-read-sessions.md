---
name: rpnh-registry-read-sessions
description: "Use explicit owner-issued observation authority with fixed-cut Registry reads."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: registry-read-sessions_ZH.md
  revision: "2026-10-07.1"
  status: implemented-offline-functional-coverage
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
