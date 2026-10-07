---
name: rpnh-portable-packages
description: "Preview bounded data-only packages and resolve exact local dependency locks without executing package content."
metadata:
  document-kind: reference-guide
  audience: operator-and-developer
  language: en
  counterpart: portable-packages_ZH.md
  revision: "2026-10-06.1"
  status: source-reviewed-pre-release
---

[English](portable-packages.md) | [中文](portable-packages_ZH.md)

# Portable package preview and exact local locks

This page documents the retained v1 contract. For formally bound environment requirements and the versioned v2 path, see [package environments](package-environments.md).

This first implementation inspects a data-only ZIP containing one closed Module
and resolves its exact dependencies from explicitly supplied local ZIPs. It does
not import a package into a Registry, prepare a HOST, install a plugin, lower or
compile a Module, adopt a graph, or execute operations. No network acquisition
or package code runs. All receipts explicitly set `execution_permitted=false`.

The wider portable-package design is not an implemented installation protocol.
In particular, package requirement categories are an inert inventory; they are
not the nested declaration snapshots consumed by HOST-readiness inspection.
There is no automatic package-to-readiness or package-to-candidate bridge.

## Try the complete offline example

The distributable fixture is `cpn/examples/portable_packages/minimal/`. It
contains a complete manifest, a genuine `rpnh/module_declaration/v1` document,
two application schemas, and an inventoried MIT license with correct lengths
and digests. Its symbolic `example/identity-*` HOST contracts are intentionally
not installed. Expected results: material integrity and schema closure satisfied;
self-contained Draft7 compatibility satisfied; HOST readiness, permission to use
licenses, origin authority, content review and revocation not checked. It is not
a runnable sample or proof of terminal success.

Follow the [installation guide](installation.md) to install an approved source
tree or wheel containing this feature. In that Python environment, confirm
`rpnh package --help` is available, then enter a writable directory dedicated to
the outputs. The commands below create `portable-identity.zip` and
`package-lock.json` in the current directory; rerunning overwrites these files.
Create a deterministic ZIP with Python's standard library, using the manifest's
explicit allowlist:

```sh
python - <<'PY'
import json
from importlib.resources import files
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
root = files("cpn").joinpath("examples", "portable_packages", "minimal")
manifest = json.loads(root.joinpath("manifest.json").read_bytes())
paths = ["manifest.json", *[row["path"] for row in manifest["artifacts"]]]
with ZipFile("portable-identity.zip", "w") as archive:
    for path in sorted(paths):
        info = ZipInfo(path, (1980, 1, 1, 0, 0, 0))
        info.compress_type = ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, root.joinpath(*path.split("/")).read_bytes())
PY
rpnh package preview portable-identity.zip
rpnh package resolve portable-identity.zip --entry main > package-lock.json
```

A root with exact dependencies uses repeatable local inputs:

```sh
rpnh package resolve root.zip --local-package dependency-a.zip --local-package dependency-b.zip --entry main > package-lock.json
```

`preview` emits JSON and returns 0 for structurally valid material even when a
check is missing or incompatible. Read the individual checks. `resolve` returns
0 only for a complete interpretable package/schema closure; runtime compatibility
can still be incompatible. Package reading or resolution errors are JSON on
stderr with exit status 2. CLI usage errors, such as missing arguments or unknown
options, are plain-text parser diagnostics with the same exit status. Neither
command writes package contents, extracts files, or creates a lock file itself;
the shell redirection above explicitly saves stdout.

## Implemented manifest contract

`manifest.json` must be at the ZIP root. Its schema ID is
`rpnh/share_package/v1`. `manifest_schema()` in
`cpn.rpnh.collaboration.share_packages` returns a detached complete JSON Schema
for this bounded slice. Every top-level field below is required. Unknown fields
in the manifest and its contract records are rejected, as are unknown mechanical
Module fields. Application data inside declared config objects remains data.

| Field | Implemented meaning |
|---|---|
| `schema_version`, `package_id`, `version` | Exact v1; namespaced lowercase package ID; `major.minor.patch` without prerelease/build syntax |
| `entries` | Exactly one `closed_module`, `entry_id`, `declaration_path`, fixed declaration schema, input/output schema IDs and completion contract |
| `artifacts` | Every non-manifest file exactly once: `path`, `media_type`, integer `bytes`, raw-byte `sha256`, `role`, `license_id`, `disclosure` |
| `origin` | `repository_url`, `commit`, `publisher_claim`, `source_refs`; retained claims, not authenticated facts |
| `provenance` | Exactly one row per artifact: `artifact_path`, `relation`, nullable `origin_ref`, nullable `origin_digest` |
| `dependencies` | `dependency_id`, `package_id`, nullable `manifest_digest`, nullable `version_range`, `required=true`, nullable `acquisition_hint` |
| `compatibility` | `declaration_schemas`, `runtime_contracts`, `host_contracts`; declarations never grant execution authority |
| `requirements` | Unique `requirement_id`, `kind`, `contract_id`, `required`, `effects`, `data_classes` |
| `policy_surface` | Empty array in this slice; no policy mutation |
| `licenses` | Unique `license_id`, `expression`, nonempty `text_paths`, `notice_paths`; referenced files must be nonempty license artifacts |
| `disclosure` | `classification`, `intended_audience`, `excluded_categories`; author's claim, not a sensitive-data scan |

Supported artifact roles are `declaration`, `schema`, `knowledge`, `fixture`,
`document`, `license`. Media types are `application/json`,
`application/schema+json`, `text/plain`, `text/markdown`; all are UTF-8 text.
Every JSON artifact uses strict decoding: no BOM, duplicate keys, NaN/Infinity,
overflow to infinity, trailing data, or unpaired surrogate characters.

Completion names the actual `success_exit`, all `failure_exits`, required
`acceptor_requirement_ids`, and empty `open_obligations`. Static checks compare
these with declared Module ports, operation/outcome products and terminal source.
They do not prove reachability, termination, deadlock freedom, or business
acceptance. The Module's required schemas must cover component/port schemas.
Declared component, executor, tool, analyzer, terminal and effect keys must occur
in required HOST requirements and compatibility inventory. This slice uses the
key itself as `contract_id`; it does not resolve implementations or grants.

`origin_ref` and `source_refs` retain the source-qualified version-reference
shape with `source_id` and `ref:{entity_type,logical_id,version_id}`. They remain
unverified claims and are never rewritten into local ancestry or Registry refs.
`copied` provenance requires the artifact's raw digest to equal `origin_digest`;
`derived` preserves the original digest while the artifact has its own digest.
Neither relation establishes permission. Repository and acquisition hints, when
present, are credential-free HTTPS URLs without query or fragment, never fetched.

## Exact resolution and digest domains

- `manifest_digest`: SHA-256 of the original raw `manifest.json` bytes, including whitespace
- `archive_digest`: SHA-256 of the complete input ZIP bytes, including compression/container metadata
- Artifact `sha256`: SHA-256 of the original uncompressed file bytes
- `package_lock_digest`: SHA-256 of `PackageResolutionLock.to_bytes()`, the canonical lock JSON: sorted keys, ASCII escaping, compact separators, no trailing newline

`resolve` stdout is exactly `to_bytes()`, so a redirected lock hashes directly to
`package_lock_digest`. The digest is external to the lock; there is no recursive
self-hash. The lock contains no input filesystem paths or wall-clock timestamp.
Identical exact inputs produce identical lock bytes. Same manifest in different
ZIP containers has the same manifest digest and different archive digests.
Root's given container stays pinned; equivalent dependency manifests choose the
lexicographically smallest archive digest independently of input order.

The lock (`rpnh/package_resolution_lock/v1`, resolver contract
`rpnh/package_resolver/v1`) fixes the root entry, nodes with material inventory
and original provenance, every enabled edge, schema owners and direct references,
and separate compatibility checks. Selected features are empty. A package ID
may select only one manifest digest. Missing local dependencies, identity
mismatches, cycles and conflicting selections fail. Same ID/version with
conflicting content reports `VERSION_CONTENT_REPLACED`; differing versions report
`DEPENDENCY_VERSION_CONFLICT`. Merely providing an unused alternative does not
replace a selected version.

Range-only dependencies are previewable but resolution returns
`DEPENDENCY_RANGE_UNRESOLVED`. This slice also refuses exact-plus-range requests;
it has no SemVer range solver and never assumes the range is satisfied. Optional
dependencies and feature selection are not supported. No remote hint is followed.

## Two independent schema checks

`package_schema_closure_resolved` means all required schema bytes and supported
references can be interpreted offline. `runtime_schema_authority_supported`
means the content meets this slice's conservative static compatibility profile
for the current self-contained Draft7 reader. It does not create an actual
registered schema authority or HOST-readiness attestation.

- Local `#/...` references into recognized Draft7 schema positions, including boolean schemas and recursive local structures, can satisfy both checks
- Cross-document or cross-package references can close the package graph while reporting runtime `incompatible/unsupported_schema_reference_contract`
- Missing target material/pointers report `SCHEMA_CLOSURE_MISSING`; no network resolver runs
- References into annotation data, such as `#/default` or `#/examples/0`, are unsupported and block an exact lock, even when object-shaped
- Unknown dialects/keywords and nested `$id` scopes are incompatible in this conservative slice; dynamic/recursive reference vocabularies are not supported
- Package schemas cannot override the authoritative `PROTECTED_SCHEMA_REFS` set (including both `runtime/llm_*_envelope/v1` contracts) or the reserved `rpnh/` and `registry_v1/` namespaces

Content schemas need canonical explicit IDs and Draft7 declarations. Identical
schema IDs in different packages must bind byte-identical material. No automatic
inlining, ID rewriting or reader relaxation occurs. Runtime compatibility also
conservatively scans references in annotation values, matching the existing
resource reader's recursive scan. A closed lock is still not permission to run.

## Archive and resource bounds

No archive member is extracted. Only regular-file ZIP members with stored or
deflated content are supported. Paths are portable ASCII relative names, at
most 240 characters, with no traversal, absolute/drive paths, empty components,
backslashes, reserved Windows names, trailing dots, or case-fold collisions.
Symlinks, directory entries, encryption, ZIP64, multipart archives, streaming
data descriptors, prepended executables, hidden local records and extra byte
trailers are rejected. Local metadata and the central inventory must agree.

Defaults: 32 MiB archive bytes, 4 MiB per expanded member (including manifest),
32 MiB expanded per archive, 128 members, compression ratio at most 200:1, JSON
depth 64 and 100,000 JSON nodes. The central directory is checked before `ZipFile`
allocates member records and is bounded to 1,024 bytes per permitted member;
per-member extra/comment metadata is capped at 512 bytes.

Resolution accepts at most 64 local inputs including root, 256 dependency edges
and depth 16. Aggregate input archives and aggregate expanded materials have
independent 64 MiB caps, including unused candidate inputs. The remaining expanded
budget is enforced before decompression. `PackageLimits` allows callers to use
stricter local limits; the fixed manifest contract retains its own maxima.

These are bounded parser checks, not a malware/secret scanner, license verdict,
origin authentication, or arbitrary PN verifier. Preview and lock documents
contain declared metadata that may itself be private. Review before sharing.

## Python API and verification

```python
from cpn.rpnh.collaboration.package_preview import preview_package
from cpn.rpnh.collaboration.package_resolution import resolve_package

preview = preview_package("portable-identity.zip")  # or ZIP bytes
report = preview.to_dict()                        # detached mutable copy
lock = resolve_package(preview, [], root_entry_id="main")
lock_bytes = lock.to_bytes()
lock_digest = lock.package_lock_digest
```

Returned records are frozen and contain immutable bytes/tuples. Resolution
revalidates supplied previews from their original archive bytes; a forged report
is not authority. `PackageError.code` supplies stable failure categories.

Focused deterministic tests are `tests/test_share_packages.py` and
`tests/test_share_packages_resolution.py`. They cover complete fixtures, resource
limits, malformed inputs, zero execution/network/writes, provenance preservation,
exact dependency conflicts and schema/runtime separation. They are not evidence
for package import, runtime adoption, remote acquisition, or real provider calls.
