---
name: rpnh-package-environments
description: "Read formally versioned package environment requirements without installing or executing package content."
metadata:
  document-kind: reference-guide
  audience: operator-and-developer
  language: en
  counterpart: package-environments_ZH.md
  revision: "2026-10-07.1"
  status: source-reviewed-pre-release
---

[English](package-environments.md) | [中文](package-environments_ZH.md)

# Package v2 environment declarations and exact targets

A `rpnh/share_package/v2` ZIP declares the environment its closed Module needs.
It keeps the [v1 ZIP safety and exact dependency rules](portable-packages.md).
Preview and package resolution remain inert: no extraction, interpreter probe,
plugin load, network acquisition, installation, Registry write or business run.
The returned report and lock always say `execution_permitted=false`.

Environment declarations answer what the author requires. Local selection,
concrete dependency resolution, preparation, HOST assembly and business execution
are separate steps. See [local environment preparation](../environment-preparation.md)
for those steps and their explicit authorization boundaries. A passed declaration
check is not a prepared or running environment.

## Version and compatibility boundaries

A v2 entry adds the required `environment_requirements_path`. Its target must be
an inventoried `document` with `media_type=application/json`, byte count, raw-byte
SHA-256, license, disclosure and provenance. The environment document's `entry_id`
must equal the manifest entry. It cannot be an unbound sidecar, executable locator
or a script supplied by the package.

The v2 pipeline uses `rpnh/package_preview/v2`,
`rpnh/package_preview_parser/v2`, `rpnh/package_resolution_lock/v2` and
`rpnh/package_resolver/v2`. A v2 lock records each node's `manifest_schema` and
`environment_requirements`: entry, artifact path, raw digest and schema version.
A selected v1 node has the explicit value `not_declared`.

V1 inputs retain their original report/lock versions and strict unknown-field
rejection. A v1 root cannot select a v2 dependency. A v2 root may lock v1 material,
but automatic environment preparation rejects that mixed closure with
`ENVIRONMENT_REQUIREMENTS_UNDECLARED`. Undeclared requirements are not an empty
list and are not inferred from a machine that happens to run the package. Supply
an updated formal package before preparing it. `preview_package_v1` is the
explicit legacy-only reader and rejects v2.

## Closed requirements document

The document schema is `rpnh/environment_requirements/v1`. All eight fields are
required: `schema_version`, `entry_id`, `python`, `distributions`,
`system_requirements`, `tools`, `plugins`, `services`. Unknown nested fields,
duplicate JSON keys, non-finite values and oversized structures are rejected.
Every row is required; an empty category means no extra requirements declared in
that category, not a statement about remote service availability.

A small example:

```json
{
  "schema_version": "rpnh/environment_requirements/v1",
  "entry_id": "main",
  "python": {
    "requirement_id": "python",
    "implementation": "cpython",
    "version_specifier": ">=3.11,<4"
  },
  "distributions": [{
    "requirement_id": "harness",
    "name": "rpnh-harness",
    "version_specifier": ">=0.1.0rc1,<1",
    "satisfies_host_requirement_ids": []
  }],
  "system_requirements": [],
  "tools": [],
  "plugins": [],
  "services": []
}
```

- Python supports `cpython` and explicit PEP 440 specifiers. Distribution names
  are normalized Python distribution names, not import names. `rpnh-harness` is
  mandatory. URL/VCS/path/editable specifications and installation arguments are
  not allowed. A range is a requirement, not a concrete install selection
- System rows use a closed `platform`, `capability` or `os_package` union.
  Platform values are declared operating-system/architecture names, not proof
  RPNH supports that platform. Current runtime support remains Linux/WSL2.
  Capability constraints are inert scalar data or `{operator,value}` with
  `present`, `eq`, `gte`, `lte`. Only installed trusted adapters interpret them;
  an unknown contract or version protocol is unsupported
- Tools declare `tool_contract_id`, `version_constraint`,
  `required_capabilities` and HOST requirement mappings. Shared requirements
  contain no local executable path
- Plugins declare `plugin_id`, `api_contract`, `version_specifier` and a
  `distribution_requirement_id` in this document. No entry-point/import locator
  is accepted; installation, metadata presence, trusted assembly and a successful
  operation are different facts
- Services declare `service_contract_id`, capabilities, authentication need and
  a nullable model constraint. Exact model conditions are bounded strings;
  floating `latest`, `default`, `auto` and `:latest` aliases are rejected as exact
  selections. A capability constraint lists `contract_ids`. Local profile,
  account and credential references do not belong in the shared document

Requirement IDs are unique across every category within one entry. Their full
identity is `(manifest_digest, entry_id, requirement_id)`. Equal short IDs in two
packages remain independent. `satisfies_host_requirement_ids` refers only to the
same manifest's flat HOST requirements and never grants loading or execution.
Collections are traversed by requirement ID; set-valued contract/ID lists are
sorted. Raw artifact bytes remain the artifact digest domain regardless of this
semantic ordering.

## Read the exact selected closure

The installed entry remains `rpnh package preview` / `rpnh package resolve`:

```sh
rpnh package preview root.zip
rpnh package resolve root.zip --local-package dependency.zip --entry main > package-lock.json
```

Resolve writes canonical lock bytes to stdout without an extra newline. Do not
pretty-print a saved lock and treat its digest as unchanged. The following Python
API rechecks the exact archives, manifests, artifacts and lock before returning
requirements; it does not trust a saved “preview passed” report:

```python
from pathlib import Path
from cpn.rpnh.collaboration.share_packages import PackageResolutionLock
from cpn.rpnh.collaboration.environment_requirements import read_environment_requirements

lock = PackageResolutionLock(Path("package-lock.json").read_bytes())
requirements = read_environment_requirements(
    lock, [Path("root.zip"), Path("dependency.zip")], entry_id="main"
)
target = requirements.target.to_dict()
```

Only lock-selected archives contribute requirements and schema closure. Unused
catalog packages do not add Python constraints, schemas or implicit dependencies.
Selected archive substitution is rejected even when recompression leaves the
manifest bytes unchanged. Package dependency resolution remains exact manifest
pinning; environmental version ranges do not add a shared-package SemVer solver.

`PackageTarget` contains `package_lock_digest`, `root_manifest_digest`,
`root_archive_digest`, `entry_id`, ordered `requirement_artifacts` and
`requirements_digest`. Each artifact row is package-qualified and contains
`manifest_digest`, `entry_id`, `artifact_path`, `artifact_digest`.
`requirements_digest` hashes the canonical ordered inventory, not a guessed merged
requirements file. Each original artifact digest is still independently checked.

Manifest, archive and artifact hashes name their respective raw bytes. Lock and
local DTO hashes name their canonical bytes, with hashes stored outside their own
documents. None is a Registry exact reference. A requirements document or redacted
preparation report gains a Registry reference only through an authorized owner
publication. Paths and local binding digests are not public Viewer identities.

## HOST diagnosis and internal ports

`diagnose_package_host_requirements(requirements, snapshot)` accepts the verified
requirements and an already-assembled inert `HostDeclarationSnapshot`. It checks
required kind/key presence and the canonical required schema bodies. `terminal`
maps to Registration's `tool`; `effect` remains a separate runtime permission
check. It never builds a Registration, loads plugins, compiles or grants anything.

Without a complete author-provided expected declaration inventory, the report
retains `author_implementation_identity=not_supplied`. A matching local snapshot
cannot prove its own author identity. `declarations_status=matched` still leaves
`contract_compatibility=requires_compilation`, permission/capacity unchecked and
`execution_ready=false`. Actual Module compilation against the selected trusted
HOST must validate operational contracts before running.

V2 permits operation inputs supplied as trusted component internal ports, such
as native plugin capabilities. Preview cannot prove they exist and explicitly
leaves internal-port binding unchecked. They cannot be used as undeclared public
boundary endpoints; wrong-direction public ports, missing operation outputs and
invalid terminal endpoints still fail. Actual trusted lowering/compiler checks
remain mandatory. V1's original stricter parser behavior is unchanged.

## What this verifies

The deterministic package tests cover v1 compatibility, v2 raw-byte identity,
selected closure, namespace separation, undeclared v1 nodes, malformed fields,
unsafe references, immutable DTOs, native internal-input boundaries and zero
probe/load/network/Registry behavior. They do not establish installed-artifact
acceptance, selected-interpreter runtime success, native worker completion or
remote-provider availability. Those require their own preparation and actual-run
evidence for the same exact package/lock/entry.
