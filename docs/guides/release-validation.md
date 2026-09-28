---
name: rpnh-release-validation
description: "Record the sanitized offline validation boundary for the unified public candidate."
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: en
  counterpart: release-validation_ZH.md
  revision: "2026-09-28.1"
  status: offline-candidate-validated
---

[English](release-validation.md) | [中文](release-validation_ZH.md)

# Public candidate validation

This record describes the unified candidate validated on 2026-09-28. It is a
sanitized summary, not a copy of local logs or private Registry data.

## Included source boundary

The validated runtime source is commit
`073a4516013443fadfcd05fa81d29c4aa1b5391b`. A later documentation-only commit
may contain this record without changing that runtime boundary.

The candidate contains core Registry/PetriNet execution, basic, Codex and
OpenCode frontends, native plugins, the shared provider/profile layer, the
optional DSH host and the read-only PetriNet viewer. Historical branch evidence,
live API campaigns, project workflows and local profiles are not included.

The provider/model catalog shipped by the package is empty. No provider,
endpoint, credential or exact model is preselected.

## Checks completed

| Check | Result |
|---|---|
| Complete Python 3.13 offline suite | 744 passed, 1 environment-dependent skip in 2279.34 seconds |
| Viewer HTTP, packaged assets and immutable-object focused set | 41 passed in 0.75 seconds |
| Directly affected Registry integration set | 84 passed in 241.80 seconds |
| Documentation links, syntax and language pairs | 70 pages, 366 links and 35 language pairs passed |
| Documentation command examples | Passed |
| Source wheel viewer allowlist and third-party assets | Passed, including `overview.mjs` and `wire-geometry.mjs` |
| Source distribution round trip | sdist built; a wheel rebuilt from the sdist passed the same viewer asset check |
| Installed wheel smoke outside source | Python 3.13 loaded `cpn` from the isolated environment; 7 zero-model commands passed |
| Installed native example | Export and verification passed with runtime effects guarded |

The focused sets overlap the complete suite and are reported as change-specific
evidence, not added to its test total. Packaging checks used the actual wheel and
sdist built from the validated commit. No checksum is part of the acceptance
decision.

The Viewer check verifies every top-level static file served by the Viewer,
vendored JointJS/ELK assets and their license records. The installed-wheel smoke
ran outside the source tree and rejected network, subprocess and Registry side
effects while exercising help, empty-catalog configuration and example commands.

## Calls and limits

No real model or provider API call was made. Offline passing results do not
establish that a user-owned route is reachable. The complete-suite skip was the
pinned OpenCode PTY test because the OpenCode executable was not installed in
this validation environment; OpenCode protocol tests still ran with a
provider-free application double.

The validation host was Linux/WSL2 x86-64 with Python 3.13.12. Python 3.11 was
not installed, and the available Python 3.12 interpreter did not have the full
test dependency set, so complete 3.11/3.12 compatibility was not revalidated in
this run. Browser automation, the pinned real OpenCode executable, and real
provider routes were not exercised. No Viewer JavaScript changed in this
candidate, so the separate Node/browser suites were not repeated.

## HTTP boundary

The Viewer HTTP server is a local, read-only display surface. It accepts only a
literal loopback listener, validates the request target, `Host`, and optional
same-origin `Origin`, and formats IPv6 authorities correctly. This does not
replace transport-specific validation in other adapters: OpenCode retains its
authenticated HTTP/SSE contract, Codex retains its Unix-socket contract, and
provider adapters retain their configured remote transport policy.

## Repository policy

The public candidate uses `main`, contains no GitHub Actions workflow and has no
automatic `push` trigger. Live-provider evidence and generated run data remain
outside the repository.
