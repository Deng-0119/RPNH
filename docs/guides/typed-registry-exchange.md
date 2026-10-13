---
name: rpnh-typed-registry-exchange
description: "Read exact typed public PN projections and exchange ordinary closed subnets through independently authorized boundaries."
metadata:
  document-kind: reference-guide
  audience: operator-and-developer
  language: en
  counterpart: typed-registry-exchange_ZH.md
  revision: "2026-10-13.1"
  status: source-reviewed-pre-release
---

[English](typed-registry-exchange.md) | [中文](typed-registry-exchange_ZH.md)

# Typed Registry projections and subnet exchange

The installed read HOST uses `TypedReaderCatalog` with the independent Registry
read session. A catalog is a parser/reader inventory, not a grant. The session
checks the selected source, exact reference, current authority and fixed cut
before invoking a reader and rechecks before returning any result.

## Supported boundaries

| Entry | Exact public record | PN projection | Subnet exchange |
| --- | --- | --- | --- |
| Resource v1 | Header only; never payload | Not applicable | Required declared materials only |
| Author revision v1 | Typed descriptor | Stored ordinary author, explicit plain transform and plain merge producers | Plain closed roots/single-parent history; verified ordered dual-parent plain merges |
| Graph author revision v2/v3 | Typed descriptor | Explicitly unsupported | Explicitly unsupported |
| Branch v1/v2/v3; SourceSet v1 | Typed descriptor | Not applicable | Not imported as a new branch/source selection |
| Assembly v1–v8 | Typed descriptor | Explicitly unsupported | Explicitly unsupported |
| Assembly v9 | Typed descriptor | New v9 producer's stored final inventory and reconstructed actual-final mapping | Explicitly unsupported |
| Candidate plan v1/v2 | Bounded descriptor projection | Not a candidate-validity/readiness claim | Explicitly unsupported |
| Native run identity; terminal evidence | Bounded public descriptor | No invented execution history | Not included by default |
| Net instance v1; marking checkpoint v1 | Bounded public descriptor | Exact stored compiler/dependency closure; checkpoint token occurrences | Runtime import unsupported |
| Execution checkpoint v1 | Bounded public descriptor | Explicitly unsupported | Explicitly unsupported |

Unknown versions fail closed. Historical author records without the new stored
projection return `PROJECTION_UNAVAILABLE`; a read never repairs them. Descriptor
support does not imply material validation, candidate validity, adoptability,
execution permission, or environment readiness. Exact resource identity remains
source-qualified; an artifact digest is not a Registry reference.

## Stored projections and mappings

`ClosedModuleAuthor`, `PlainModuleTransformAuthor`, `PlainModuleMergeAuthor` and `AssemblyAuthorV9` publish
a separate immutable projection resource before their revision success point.
It records the compiler inventory actually used by that producer and checksums
for the exact material dependencies. Public reads revalidate those bytes,
source/element/boundary/HOST material, projection, hierarchy and mapping. They
rehydrate the stored compiler wire with the offline verifier; they do not invoke
a lowerer, discover a plugin, compile a declaration or launch a HOST.

If optional parent/copy/transform proof is initially unavailable, the reader
independently revalidates the selected graph's own material and returns topology
with `mapping_coverage = not_provided`, empty relations and no inferred member
scopes. Missing own graph material still fails. Integrity failures or an access
change never silently downgrade to a cached graph.

The public graph uses the existing viewer node/edge whitelist. Configuration
contains exact `host_requirements_ref` and `declared_configuration_ref` pins,
not arbitrary operation configuration or credentials. HOST registration
requirements are distinct from package environment requirements. Assembly
origin output omits raw fragment contexts and command metadata. Runtime graph
configuration exposes only the selected stored declaration reference.

Retained/copy correspondence and split/fusion retain their exact source and
result revision, whole endpoint groups and original evidence. General N→M and
multi-generation inferred mapping are unsupported. A copied element is a new
author identity. No author correspondence establishes runtime token identity.
The edge-role projection names explicit operation/port ownership; repeated
indistinguishable roles are not guessed from names or array order.

For Assembly v9, the public reader reconstructs the actual-final mapping from
stored member and final inventories, verifies the source material lock and
pure composition, and retains declared member occurrences. It does not assume
that a member compiled alone is identical to its final-context fragment.

Checkpoint projections validate the exact checkpoint commit witness, full
selected net/declaration closure and token occurrence references. A net alone
has `runtime.coverage = not_provided`. A checkpoint provides only the selected
marking; it does not imply complete firing or business-result history.

## Export

Use `plan_subnet_export(session, root_revision_ref=..., cuts=...)`, followed by
`export_subnet(session, plan=..., destination=...)`. The default and currently
supported selection is `definition_closure`. The result contains a verified
portable package and any exact dependency archives. The caller handles the
already-authorized destination; this API never interprets a destination as an
arbitrary filesystem path or URL.

The plan closes ordinary definition, element/boundary maps, actual HOST/schema
declarations, required parent evidence and stored projection. Missing sources,
missing dependencies, cycles, wrong versions or budgets fail explicitly. Export
rebuilds the plan, rereads every body, checks independent export permission for
every included reference and the exact destination, and performs a final
source/authority recheck. A caller-constructed plan cannot omit export checks.

The package contains historical source cut evidence without session/cursor/cut
handles. Its inner exchange inventory is checked for every byte count, digest,
exact material reference, parent/material closure and stored dependency pin,
in addition to the outer portable manifest inventory. Deterministic ZIP member
ordering/timestamps and a collision-free allocator cover both generated and
preserved paths, including case collisions.

## Plain merge exchange v2

A closure containing an ordered two-parent plain merge uses the inner
`rpnh/subnet_exchange/v2` inventory. The outer manifest is `rpnh/share_package/v3`, with a mandatory
`subnet_exchange` artifact path and exact inner schema version. The v3 preview
and resolution-lock contracts retain this marker; the ordinary v1/v2 schemas
and their default API behavior are unchanged. Root/single-parent ordinary
histories without preserved v3 origin keep their previous outer/inner versions.

V3 is also retained when re-exporting a locally imported revision whose inert
original package was v3, even though the new local history uses inner exchange
v1. This prevents an older reader from silently treating the source proof as
an unrelated generic document. The 4544cdc v1/v2 parser rejects outer v3 with
`UNSUPPORTED_PACKAGE_SCHEMA` before import. Renaming the manifest version is
not a supported compatibility conversion.

An optional v3 `environment_requirements_path` keeps the existing exact v1
environment contract. It is never synthesized from HOST declarations. A mixed
v1/v2/v3 closure uses lock v3; a lower-version root cannot select v3 dependencies.
When any selected entry lacks formal environment requirements, environment
readiness remains undeclared. This version gate grants no execution authority.

The existing `rpnh/plain_merge_public_pn/v1` fixed-cut producer verifier is
required at export. Every exact proof dependency is included: source identity,
owner/bootstrap/principal descriptors, frozen schema authority, ancestor
revisions, original materials, analysis, caller choices, result command,
compiled inventories and stored graph. Required descriptors are inert evidence;
export permission is checked for them as well as every material and revision.
No observer grant, participant/session authority or active runtime is copied.

Import reuses the original pure merge verifier to reconstruct the exact base,
ordered parents, normalized analysis, choices, result materials, HOST inventory
and public graph. Outer ancestor records must match their proof descriptor
bytes; duplicate/missing/unused proof pins, changed identities or unsupported
producers fail closed. Source proof establishes internal historical consistency,
not external-publisher authentication, current access, a new local merge,
runtime continuity or permission for the receiving owner. New local revision
and element identities retain only explicit copy-origin correspondence.

Shared-ancestor depth counts the longest path, and all parent/proof edges count
toward the same package budget on both export and import. Merge archives and re-exports with preserved original archives use
deterministic member ordering/timestamps and DEFLATE to fit the existing inert
provenance envelope; expanded-byte, artifact and compression-ratio limits still
apply. Existing stored archive bytes remain pinned in provenance; generating a new compressed export produces a new archive digest and is not an in-place rewrite. Same-command import replay still requires its original exact plan. Oversized histories remain unsupported. Designer constraints, graph,
Assembly, selected-change and other specialized contracts remain rejected.

## Retained package ancestry

The original package is found across the verified revision ancestry, not only
on the selected merge root. An imported seed can therefore be edited, forked
and merged without dropping its task fixtures, documents, licenses, environment
artifacts or exact dependency archives. One unique original archive/lock is
required; distinct incompatible origins return `AMBIGUOUS_PACKAGE_ORIGIN`.
Original requirement IDs are preserved for environment mappings, and newly
required HOST contracts are appended without claiming the prior environment
satisfies them.

Retained copy-origin evidence is recursively checked against its exact original
archives and lock, source exchange, selected definition, import plan digest,
copy command and complete newly allocated element map. Every archive is
admitted before its expanded materials are parsed, including all supplied
dependencies; extra/unselected archives are rejected. Nested package and revision
ancestry depths compose, with one shared archive/expanded-byte/member/edge
budget. These checks bind historical evidence only. They do not create source
principals, grants, leases, branches, runtime facts or authority in the receiver.

This also tightens older inner-v1 evidence acceptance: previously accepted
malformed, inconsistent or oversized retained histories can now fail. Ordinary
v1/v2 schema bytes are unchanged; this is not a promise that every older package
will pass the strengthened semantic checks.

## Import

`plan_subnet_import(package, target=TargetRegistrySelection(...),
local_packages=...)` is inert. All required dependencies must resolve from exact
local archives. No network acquisition, installation, preparation, adoption or
run is performed.

`import_subnet(owner_author, plan=..., command_id=...,
expected_target_head=...)` requires the existing target `ClosedModuleAuthor`.
It verifies the package/closure again. The first command reservation compares
the target ordinal and writer fence inside the native `BEGIN IMMEDIATE` write
transaction. The existing canonical resource publisher and owner checks are
preserved. A changed command conflicts; a retry reuses its original local
revision and resource identities even if the Registry subsequently advanced.
A failed attempt may leave immutable preparation/reservation facts; retrying the
same command completes the original operation rather than overwriting objects.

Imported revisions and elements have new local identities. The result includes
an exact copy-origin map. Registry exports retain their source-qualified
historical provenance. Ordinary packages use tagged `package_artifact` origins
with manifest digest, artifact path, entry and element locator; no source ID or
Registry history is fabricated. Claims in portable provenance are historical
package evidence, not transferable authority or proof of current source access.

Inert schemas can be registered through the target's existing owner gateway;
conflicting schema identities are rejected. Before the command reservation, every source HOST schema must be present with identical bytes in the exact selected package closure; changing a schema artifact under the same `$id` is rejected. Executable implementations must
already exist in the trusted target Registration. A Registry export's recorded
HOST declaration identities must match the target; this check is not proof of
runtime behavior. No Branch is advanced or configuration overwritten.

The original selected root archive and every lock-selected dependency archive
are retained as inert, checksummed import evidence. Re-export preserves all
original environment artifact bytes, including non-entry documents. Environment
identity is `(manifest_digest, path)`, so different packages using the same path
remain distinct. Output manifest references are updated when generated names
collide. The exact dependency archives remain available for deterministic
re-export/re-import. Oversized evidence is rejected before initial publication.

## Integrity and validation evidence

Legacy `resource_version/v1` does not record a universal payload checksum.
A current computed SHA-256 is labeled separately from recorded integrity;
registered length alone does not prove an unchanged same-size legacy body.
Owned projection/import publications record `content_sha256`, and projection
closure verifies its recorded dependency digests. Consumers must not promote a
legacy computed digest into a claim of historical checksum verification.

Focused offline coverage lives in `tests/test_registry_typed_exchange.py` and
`tests/test_registry_read_session.py`. It includes real producer publications,
actual independent observer issuance/session export and target import,
revocation, split/fusion, context-sensitive Assembly v9 reconstruction, exact
runtime/checkpoint reads, stale-head/racing writes, replay/interruption recovery,
inner inventory tampering, missing dependencies and v2 path/byte preservation.
These functional tests do not replace the separate independent authorization
review, real browser acceptance or same-package business terminal acceptance.

The merge-specific deterministic regression is
`python -m unittest discover -s tests -p 'test_subnet_merge_exchange_unittest.py' -v`.
It needs the declared runtime dependencies but not pytest. It checks real
Registry/observer/session exchange and adversarial closure cases, not a complete
multi-user project journey or post-import business execution.

All lock-selected packages, including v3 dependencies, have their mandatory
exchange evidence verified in the same traversal budget. Unselected candidates
are bounded during preview but are not traversed for exchange semantics.
`PackageLimits.members` remains 128 per archive; `closure_members` is 256 across
selected and recursively retained archives (repeated archives are charged again).
The complete 18-artifact seed measured 96 members at merged export and 195 after
re-export, with no individual archive exceeding 99 members. Tight aggregate limits
fail closed rather than widening the per-archive allowance.
