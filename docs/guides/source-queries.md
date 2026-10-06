---
name: rpnh-source-queries
description: "Record and inspect finite, authorized SourceSet observations."
metadata:
  document-kind: how-to
  audience: trusted-host-developer
  language: en
  counterpart: source-queries_ZH.md
  revision: "2026-10-05.1"
  status: opt-in-source-observation-v1
---

[English](source-queries.md) | [中文](source-queries_ZH.md)

# SourceSet queries and recorded observations

A trusted HOST can publish a versioned expected source list, query each source
through its existing ResourceService authority, explicitly record the result,
and show the same result to an agent and the [Viewer](viewer.md). This is an
opt-in metadata interface. It does not start or resume work, deliver payloads,
issue grants, discover remote Registries, or establish remote trust.

## Register the scope

Compose `source_observation_schema_data()` into the HOST's explicit catalog.
The existing `RegistryRegistrationGateway.bind_source_identity` establishes
one local source identity. `SourceMember` contains an exact source-qualified
task reference, explicit access-path identifiers, aliases and a `link_kind`.
The link classifications are `immutable_snapshot`, `editable_fork`,
`exclusive_capability`, and `control_message`; a classification does not copy,
transfer or authorize any capability.

Use `gateway.publish_source_set(members=..., command_id=...)` for the initial
manifest. A successor supplies both `expected=old.source_set_ref` and
`expected_sequence=old.sequence`. The original Registry transaction compares
the exact predecessor and sequence before committing; an intervening update
is a conflict. Command retries return the same immutable version, while a
changed command body conflicts. Old versions remain readable after reopening.

An expected source stays in the manifest when it is offline, denied or not yet
attached. Exact source identity deduplicates aliases. Conflicting identities
or path lists for one source are rejected. Aliases and path identifiers are
never filesystem locations or permission grants.

## Query and record separately

Construct `SourceSetQuery` with a read-only manifest Registry, an explicit
HOST resolver, and `capture_context=...` for the local operation that records
query evidence. A canonical Viewer reader omits that capture context. The resolver takes `(source_id, access_path)` and returns a
`RegistrySourceReader(read_only_core, existing_context)`, or raises
`SourceUnavailable`. It must use a previously authorized exact
`InvocationContext` or an already issued compatible observer context.
This feature supplies no observer grant issuer or automatic fallback context.

`query(source_set_ref, paths=..., limit=..., media_types=...)` selects exactly
one declared path per expected source and calls the existing ResourceService
query. Each source independently fixes its own canonical head, cursor and
current authority. A path never gains permissions from another path to the
same source. Missing sources have null counts; complete empty authorized
queries may have zero. Global totals remain null while any source is missing
or has further pages. Counts describe authorized headers in this query only.

The query returns an immutable `SourceObservationDraft` and writes nothing.
Call `gateway.record_source_observation(query=query, draft=..., command_id=...)`
explicitly. The shared source validator replays the exact finite pages at their
fixed cuts, compares complete canonical headers, cursors, offsets and completion,
and rechecks current capture qualification. Forged draft fields are rejected.
The record belongs to the local capture Invocation's provisional firing area;
its refs are not promoted merely because its transaction committed. This separate
write is not a resource read or evidence of payload delivery.

For the next page, call `query` with `previous=recorded_observation_ref` and the
same manifest, paths, limit and filters, then explicitly record the new draft.
Earlier provisional records are read only through the exact same-Invocation
FiringView. Canonical GET and another firing cannot read them. Original Success
later promotes the complete observation group through the unchanged Registry
publication gate. Only unfinished sources fetch a new page. Completed sources are reauthorized
without rescanning. The returned draft contains retained authorized headers
and the new page, so consumers replace the old result rather than appending
unverified client data. Sources that were missing remain gaps in that original
observation; a new query or explicit supplement investigates later availability.
Publishing S@2 does not add S@2 members to S@1 continuation pages.

`through={source_id: canonical_ordinal}` optionally selects independent source
cuts for the initial query. These positions form an observation vector, never
a global atomic snapshot. Current authority still governs historical reads.

Full original source-qualified qualification references remain in every saved
capture; no fingerprint replaces an invocation dependency. After Success the
original Invocation is closed to I/O, so it cannot continue an old cursor.
A different reader starts a new Observation or supplement. Exact identical
record-command retries after Success return the original reference without
reopening capture I/O; different content or producers conflict.

## Independent supplements

`supplement(observation_ref, dependency=SourceQualifiedResourceRef(...))` reads
one exact resource header through the original selected path. It keeps its own
cut and exact dependency, and can be recorded with a separate command. Missing
or denied dependencies remain explicit. The original Observation is unchanged;
a later supplement never establishes that the old source vector was causally
complete. A changed source/path authority cannot be silently substituted.

## Viewer integration and current access

Configure `SourceObservationView(query, explicit_observation_refs)` and pass it
as `source_observations` to `RegistryDashboard`. The existing Viewer serves
`GET /api/v2/source-observation`, with an optional exact JSON `observation_ref`.
GET and HEAD only read selected saved results; unselected references are
rejected. The "Recorded source queries" panel displays the selected result,
source cuts, paths, loaded/total/unknown counts and newer-manifest notices.
Its explicit observation buttons only navigate the configured saved results.
The graph/checkpoint timeline and this observation remain separate.

Only canonically promoted captures can be selected for GET. The same consumer
checks the original registered capture context as historical evidence, and
separately checks a currently authorized reader on each original path. It
compares every header against the canonical source at the original cut. An
independent validation query checks the saved finite pages and completion at
that same scope; it never substitutes new authority into an old cursor or
adds newly accessible rows. If access changed or a source is unavailable,
old headers and counts are cleared from the response and UI. The immutable
stored capture remains intact; the response distinguishes capture classification,
query filters and disclosure labels from current access. A historical source gap
without a new probe has current access `not_checked`. No supplied reader is
`not_established`; an explicitly offline reader is `unavailable`. Unprovided manifests are not inferred from online sources.
The HOST's explicit Viewer selection is a disclosure choice, not a source grant.

## Validation boundary

The focused tests initialize synthetic SQLite Registries, admit ordinary pure
operations, and use the original Start/products/Success API with explicit static
schema-valid output bytes to promote observations. Start acknowledges only the
synthetic registered inputs. The registered executor is never invoked; no model,
provider, worker, socket, external effect, workspace capture, replacement or
remote transport is used. Tests separate read-only queries/GET from explicit
recording and fixture settlement writes. They cover same-firing pagination,
canonical invisibility before Success, historical GET with a new current reader,
closed-cursor rejection, CAS/replay, source/path identity, missing counts,
authorization changes, exact supplements and actual UI model/panel consumption.
Browser rendering and navigation on the target OS require separate validation;
Node tests do not establish browser or remote transport behavior.
