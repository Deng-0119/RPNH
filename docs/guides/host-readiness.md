---
name: rpnh-host-readiness
description: Compare inert HOST declarations without execution or permission claims.
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: en
  counterpart: host-readiness_ZH.md
  revision: "2026-10-06.1"
  status: offline-validated-initial-slice
---

[English](host-readiness.md) | [中文](host-readiness_ZH.md)

# Declaration-only HOST readiness diagnostics

This initial slice answers a narrow question: **do the required declaration bytes
match a supplied inert HOST declaration snapshot?** It does not prepare, authorize,
or execute a candidate. It adds no Registry schema, graph publication, adoption,
reservation, or execution path. Existing persisted v1/v2 readiness records retain
their original meanings.

The API is in `cpn.rpnh.collaboration.host_readiness`. There is no new CLI command;
a diagnostic must not enter a CLI path that first discovers or loads plugins.

## Snapshot an already-prepared HOST

HOST code preparation is a separate, explicit action. Given a concrete
`Registration` already prepared by your trusted application,
`snapshot_host_declarations(registration)` copies only `registration.declarations()`.
It never calls `resolve`, a component lowerer, an executor, a tool, an analyzer,
a plugin factory, or `load_catalog`.

This complete offline example uses a sentinel callable that must never run:

```python
from cpn.rpnh.registration import Registration
from cpn.rpnh.collaboration.host_readiness import (
    diagnose_host_requirements,
    snapshot_host_declarations,
)

# Explicit HOST preparation, outside the diagnostic API.
def sentinel_executor(**kwargs):
    raise AssertionError("diagnostics must not execute this callable")

registration = Registration()
registration.register_executor(
    "example/echo/v1",
    sentinel_executor,
    identity={"implementation_id": "example.echo", "revision": "v1"},
    contracts={"transport": "deterministic"},
)

snapshot = snapshot_host_declarations(registration)
required = {
    "executor": {
        "example/echo/v1": registration.declaration("executor", "example/echo/v1")
    }
}
report = diagnose_host_requirements(required, snapshot).to_dict()
assert report["declarations_status"] == "matched"
assert report["execution_ready"] is False
assert report["permission"] == report["capacity"] == "not_checked"
assert report["reservation"] == "not_reserved"
```

`required` is the existing nested `registrations` object from author HOST
requirements or compiled wire, not the whole host-requirements envelope.
Supported registration kinds are `schema`, `component`, `executor`, `tool`, and
`analyzer`. Requirements may select a subset; extra snapshot declarations are not
additional requirements. Input containers must be standard JSON dicts/lists,
with finite numbers, string keys, exact declaration fields and matching kind/key.
Executable declarations require nonempty identity and object contracts and reject
executable locator fields. Schema declarations require matching `$id` and the
existing Draft7 dialect. Python adapters, cyclic data, and malformed fields fail
with `TypeError` or `ValueError`.

## Interpret the report

Each `declaration_checks` entry has a kind/key, status, reason, and exact required
and available declaration digests:

- `matched` / `exact_declaration_match`: complete declaration content matches
- `missing` / `missing_host_declaration`: the supplied snapshot lacks this entry
- `mismatch` / `declaration_digest_mismatch`: the same kind/key has different content
- `not_checked` / `no_host_snapshot`: no HOST snapshot was supplied

Overall `declarations_status` is `not_checked` without a snapshot; otherwise it is
`mismatch` if any entry mismatches, `missing` if any entry is absent, or `matched`.
Mixed missing and mismatching entries remain visible individually. Empty
requirements match an explicit snapshot, but remain `not_checked` without one.
Neither outcome establishes execution readiness.

`required_declarations_digest` and `host_declarations_digest` are SHA-256 over the
complete respective nested registrations objects. Per-entry digests cover the
entire declaration, including identity, contracts or schema. The exact encoding
is UTF-8 of `json.dumps(value, ensure_ascii=True, sort_keys=True,
separators=(",", ":"), allow_nan=False)`, after concrete JSON validation. Object
key order is canonical; array order and numeric representations are preserved,
so `true`, `1`, and `1.0` differ. No whitespace, source-code, callable-identity,
plugin-archive, or implementation-revision provenance claim is implied. An
inventory with extra declarations can match every requirement while having a
different full-inventory digest.

`HostDeclarationSnapshot` and `HostReadinessDiagnostic` are frozen DTOs.
`to_dict()` and `snapshot.registrations` return detached data: changing the input
or returned nested objects cannot alter earlier observations. Snapshotting is not
an atomic cut against concurrent Registration mutation. Subsequent registrations
are absent from an older snapshot. Implicit mechanical schemas not returned by
`Registration.declarations()` are not silently loaded; a requirement for one is
reported missing unless it is in the supplied inert snapshot.

## Inert JSON and historical plans

A previously saved declarations object can be copied without preparing code:

```python
from cpn.rpnh.collaboration.host_readiness import HostDeclarationSnapshot

saved = snapshot.to_dict()
restored = HostDeclarationSnapshot(saved["registrations"])
assert restored.declarations_digest == saved["declarations_digest"]
assert diagnose_host_requirements(required, restored).execution_ready is False
```

This checks content consistency only. A copied or caller-invented JSON snapshot
is not authenticated HOST evidence and never becomes an execution capability.
The API accepts the snapshot DTO, not arbitrary report JSON, as its second
argument. If no prepared snapshot exists, omit it and preserve `not_checked`.
Do not load plugins merely to replace that outcome with a positive-looking report.

For an already committed candidate, pass its **exact** `VersionRef` and the
existing Core configured with the appropriate schema catalog:

```python
from cpn.rpnh.collaboration.host_readiness import diagnose_candidate_plan

# core and exact_plan_ref come from your existing Registry read path.
report = diagnose_candidate_plan(core, exact_plan_ref, snapshot).to_dict()
assert report["plan_ref"]["entity_type"] in {
    "collaboration_candidate_plan/v1", "collaboration_candidate_plan/v2"
}
assert report["execution_ready"] is False
```

The wrapper dispatches v1 to the existing `read_candidate_plan` and v2 to
`read_preserved_candidate_plan`. These canonical historical readers retain their
existing exact-byte, dependency, offline-validation and fixed-cut checks. The
wrapper adds no raw database reader and performs no version upgrade. The report
includes `plan_ref` and `plan_sha256`, covering the complete canonical plan bytes
verified by the reader. Missing, malformed or unsupported evidence raises the
reader's error; unknown plan versions and aliases such as `latest` are rejected.
Historical readability is not proof of current permission, first admission,
actual HOST lowering, or adoptability.

## What remains unverified

Every report has `execution_ready=false`, `permission=not_checked`,
`capacity=not_checked`, and `reservation=not_reserved`. `callable_identity`,
`lowering`, and `runtime_schema_authority` also remain `not_checked`.

The direct declaration comparison does not validate schema runtime compatibility
or fetch `$ref` targets. Matching declarations containing an external schema
reference still do not mean the Registry runtime supports it. It does not inspect
credentials, configure providers, test remote services, or reserve resources.
The separate existing `cpn.rpnh.diagnostics.diagnose` executes registered analyzers
and is intentionally not used here.

Continue locally with real observed requirements and already-prepared HOST data.
The next executable slice would require actual trusted recompile/full-wire
comparison, exact input and permission checks, and the designed atomic
registration/dispatch guard with lifetime pins. This snapshot contains neither
callables nor a frozen execution selection; it cannot close rebind/check-use gaps
or enable shared-inventory execution across owners. Graph bridging, adoption,
recovery and real-provider acceptance are not implemented or demonstrated here.

## Offline verification

From a checkout with the test dependencies installed:

```sh
python -m pytest -q tests/test_host_readiness.py
python -m pytest -q tests/test_candidate_plan_read_context.py tests/test_candidate_plan_offline.py tests/test_preserved_candidate_plan_reads.py
```

The focused tests include exact/missing/mismatch/not-checked outcomes, malformed
inputs, immutable detached data, sentinel factory/callable zero calls, blocked
plugin/network/subprocess paths, and real canonical v1/v2 reads without added
Registry objects or events. They are offline mechanism evidence, not benchmark,
provider or execution-readiness acceptance.
