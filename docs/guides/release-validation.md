---
name: rpnh-release-validation
description: "Separate the historical full offline baseline from current focused validation."
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: en
  counterpart: release-validation_ZH.md
  revision: "2026-09-29.3"
  status: historical-full-baseline-current-focused-delta
---

[English](release-validation.md) | [中文](release-validation_ZH.md)

# Release validation boundaries

This page separates a historical complete-suite baseline from later focused
validation. It is a sanitized summary, not a copy of local logs or private
Registry data. The historical total must not be presented as a complete-suite
result for current `main`.

## Included source boundary

The last complete offline suite in this record ran against runtime commit
`073a4516013443fadfcd05fa81d29c4aa1b5391b` on 2026-09-28. Current runtime
documentation additionally covers focused changes through
`87e98356a1ba78b6afd7bae93d26704e030467a3`: canonical cross-frontend session
ownership, subordinate execution nets, workspace version history, arbitrary
checkpoint reopen, compaction/recovery closure and separation of Registry
validation from runtime retry policy, plus pre-admission workspace destination
rejection, special-file-safe snapshot restoration, bounded concurrent Viewer
requests and strict wheel vendor-asset validation. Documentation-only commits
after that SHA do not change the runtime boundary.

The unified tree contains core Registry/PetriNet execution, Basic, Codex and
OpenCode frontends, native plugins, the shared provider/profile layer, the
optional DSH host and the read-only PetriNet viewer. Historical branch evidence,
live API campaigns, project workflows and local profiles are not included.

The packaged provider/model catalog is empty. No provider, endpoint, credential
or exact model is preselected.

## Historical complete-suite baseline

| Check | Result at `073a451` |
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

The focused sets overlap the complete suite and are change-specific evidence,
not additions to its total. Packaging checks used the actual wheel and sdist
built from that commit. No checksum was part of the acceptance decision. The
Viewer check covered every top-level static file, vendored JointJS/ELK assets
and license records. The installed smoke ran outside the source tree and guarded
against provider-capable network, subprocess and Registry effects while checking
help, empty-catalog configuration and example commands.

## Current focused delta

Runtime changes after the complete-suite baseline were validated with the
smallest directly affected sets rather than an unrelated full-suite rerun:

| Boundary | Focused evidence |
|---|---|
| AgentLoop, registered-host, frontend and checkpoint closure | 14 tests passed at `de53768` |
| Persisted v1 compatibility plus current checkpoint reopen | 2 tests passed at `de53768`; old optional `next_attempt_allowed: false` remained readable and did not block reopen, while current writers omitted it |
| Registered-operation and resource-service recovery | 16 tests passed at `de53768` |
| Workspace rejection/recovery, execution-child closure and Viewer HTTP/assets | 63 focused tests passed at `87e9835`; directory/FIFO/NUL rejection, directory correction, owner-stop FIFO restoration and incomplete-client isolation were included |
| Actual wheel Viewer gate | A wheel built from `87e9835` passed the fixed top-level and vendor-file/license set; an empty manifest is rejected |
| Current documentation and configuration reference | 20 tests passed; 70 pages, 384 internal links, 35 language pairs and the built 70-page site passed |
| Historical Registry projection | A preserved large 3-DOF run opened through `rpnh net` at verified event head 16185 without acquiring writer authority |

These sets verify exact checkpoint reentry, workspace candidate settlement,
immutable failure evidence and indexed Registry reads. They are not a new
complete-suite total or a live-provider campaign. Documentation checks validate
metadata, links, syntax, language pairing and schema examples; they do not
execute the model-backed examples.

## Calls and limits

No real model or provider API call was made in either the historical complete
offline run or the post-baseline focused runtime validation. Offline passing
results do not establish that a user-owned route is reachable. The historical
complete-suite skip was the pinned OpenCode PTY test because that executable was
not installed; OpenCode protocol tests still ran with a provider-free
application double.

The complete-suite host was Linux/WSL2 x86-64 with Python 3.13.12. Python 3.11
was not installed, and the available Python 3.12 interpreter did not have the
full test dependency set. Browser automation, the pinned real OpenCode client
and real provider routes were not exercised by this release record. Dated,
authorized live examples are reported separately in
[examples validation](examples-validation.md).

## HTTP boundary

The Viewer HTTP server is a local, read-only display surface. It accepts only a
literal loopback listener, validates the request target, `Host` and optional
same-origin `Origin`, formats IPv6 authorities correctly, serves connections
independently and bounds incomplete request reads. This does not
replace transport-specific validation in other adapters: OpenCode retains its
authenticated HTTP/SSE contract, Codex its Unix-socket contract, and provider
adapters their configured remote transport policy.

## Repository policy

The canonical project uses `main`, contains no GitHub Actions workflow and has
no automatic `push` trigger. Live-provider evidence and generated run data
remain outside the repository; only reviewed sanitized summaries are committed.
