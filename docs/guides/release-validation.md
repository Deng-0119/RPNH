---
name: rpnh-release-validation
description: "Separate the historical full offline baseline from current focused validation."
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: en
  counterpart: release-validation_ZH.md
  revision: "2026-10-06.1"
  status: historical-full-baseline-current-focused-delta
---

[English](release-validation.md) | [中文](release-validation_ZH.md)

# Release validation boundaries

This page separates a historical complete-suite baseline from later focused
validation. It is a sanitized summary, not a copy of local logs or private
Registry data. The historical total must not be presented as a complete-suite
result for current `main`.

## 2026-10-06 finite integration and bridge repair

The benchmark execution source conditions are named **freeze04** and **repair**.
Their historical manifests do not describe the final publication file set, which
additionally includes documentation, packaging-check and test changes. This
integration includes typed-v9, P4 full-history Assembly merge, P6 graph source
merge and the bounded I02/shared-graph gate, plus Workset/normal-root readers,
token allocation, ordinary revision and native HTTP/Node consumers. Prior
cloud ownership handoff has completed; earlier waiting statements are historical.
These are finite acceptances, not full HOST, all I00–I10 or advanced25 acceptance.

| Prior evidence | Accepted boundary |
|---|---|
| Local early typed / P4 / I02 | Writer nodes 23 / 38 / 10; independent nodes 11 / 10 / 7 |
| Local early P6 | Writer 32 on freeze03 + 6 on freeze04; independent 5 on 03 + 1 on 04; never relabelled all on 04 |
| Dynamic normal-root writer | 64 cases / 192 phases; separate R02 Success-wins window: 1 case / 3 phases |
| Integration independent execution | 8 exit-zero windows, 31 executions / 93 phases / 30 distinct nodes: 8 executions on freeze02, 22 on freeze03, 1 on freeze04 |
| Finite reader/root cases | Retained refs/bytes across Success, separate T01 run terminal, ordinary revision, legacy-catalog rejection, exact replay/twochild, 7 damaged-copy read-only refusals, attach-wins and separate CAS; R02 covers one explicit Success-wins order |
| Native observation | HTTP and Node PASS; mock DOM PASS; not actual browser DOM |
| Actual browser | Chromium startup exit 1: root/sandbox restriction, BLOCKED; pytest skip is not PASS; no successful page/DOM/screenshot validation |
| Bridge repair | Writer 52 tests; independent 50 distinct tests across six exit-zero windows; overlap, not 102 unique |
| Installed native-host preparation | Two separate offline windows, 7 PASS each |

Finite concurrency checks do not guarantee arbitrary scheduling or child counts.
The real browser was not retried or run without its sandbox. Source restoration
and collection counts are not test passes. Repair business results are separately
reported in [AutomationBench first18 + repair4](../../examples/automationbench/PUBLIC_RESULTS_20261006.md).
Human review was not run, contamination remains unknown and strong worker OS
isolation is unproven. The prior evidence above remains separate from the new
publication checks below; its counts are not added to the new results.

## 2026-10-06 publication validation

Independent scoped checks against the integrated publication source passed on
Linux/WSL with Python 3.13 and Node 22.22.1. These are finite boundary checks,
not a complete test suite or full HOST/I00–I10/advanced25 acceptance.

| Publication check | Actual result |
|---|---|
| Scoped Python tests | 198 distinct passed nodes, including all 30 bridge cases; repeated executions are counted once |
| Documentation unittest suite | 26 tests OK, recorded separately from the 198 scoped Python nodes |
| Node tests | 224 distinct passed test titles; mock DOM/Node evidence is not real-browser acceptance |
| Optional-Node boundary | With Node absent: HTTP 1 PASS, JavaScript 1 SKIP; the repeated HTTP node does not increase the 198-node count |
| Schema and package resources | 268 schemas validated and 1 catalog JSON checked; all 674 expected packaged files verified in both wheel and sdist |
| Build and installation | Wheel/sdist build, wheel installation, dependency check and wheel asset audit exited 0; 7 installed zero-model commands passed outside the source tree |
| Viewer rebuild | 6 assets rebuilt byte-equal to the checked-in files |
| Documentation check/build | 122 maintained pages / 61 language pairs, plus 30 supporting documents; 152 HTML pages built |
| Built documentation links | 10,983 local links checked, failures empty; 64 external links were not fetched |
| Documentation examples | 2 bilingual schema examples and the empty catalog valid; 3 invalid mutations rejected; syntax/schema checks do not execute examples or validate live transport |

The documentation results combine the final check/build/link validation with
the recorded unittest and example checks. The builder also syntax-checked 5 linked JSON, 4 CSV
and 3 Python documents. Supporting result pages are built without becoming
maintained topics or relaxing their metadata and language-pair requirements.

Initial failures are retained separately: the bridge runner's relative-path
`dir_fd` guard interpretation and Node dependency resolution were corrected
within the task-local runner/dependency setup, with affected checks rerun.
The earlier documentation checker failures were resolved by supporting linked
result documents and downloads while preserving link and metadata validation.
Those failures are not erased or relabelled as passes. The optional-Node test
split and wheel audit repair are publication test/packaging changes, not new
runtime behavior or new benchmark execution source identities.

No real provider, model, judge or browser calls were made for these publication
checks. The prior actual browser root/sandbox launch remains BLOCKED; no retry,
sandbox bypass or real page/DOM/screenshot success is claimed.


## Included source boundary

The last complete offline suite in this record ran against runtime commit
`073a4516013443fadfcd05fa81d29c4aa1b5391b` on 2026-09-28. That historical runtime
record additionally covers focused changes through
`87e98356a1ba78b6afd7bae93d26704e030467a3`: canonical cross-frontend session
ownership, subordinate execution nets, workspace version history, arbitrary
checkpoint reopen, compaction/recovery closure and separation of Registry
validation from runtime retry policy, plus pre-admission workspace destination
rejection, special-file-safe snapshot restoration, independent Viewer connection
handling with a finite socket I/O timeout, and strict wheel vendor-asset
validation. Later runtime integration has its own finite evidence above; this historical
baseline does not certify the integrated publication tree.

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
| Current documentation and configuration reference | 20 tests passed; 76 pages, 420 internal links, 38 language pairs and the built 76-page site passed |
| Historical Registry projection | A preserved large 3-DOF run opened through `rpnh net` at verified event head 16185 without acquiring writer authority |
| `v0.1.0rc1` complete collection | At `1e85b4f`, 790 tests passed and the pinned OpenCode PTY test skipped because its executable was absent; 23 Unix-socket tests failed before protocol handling because the operator-supplied temporary root made their socket paths exceed the platform limit. Both affected files then passed all 30 tests with a short temporary root. No source change was needed, so the combined candidate evidence covers 813 unique passing tests and one documented environment skip without presenting the first run as a single clean pass. |

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
independently and gives accepted sockets a finite I/O timeout. This is neither
a total request deadline nor a connection or thread cap. It does not replace
transport-specific validation in other adapters: OpenCode retains its
authenticated HTTP/SSE contract, Codex its Unix-socket contract, and provider
adapters their configured remote transport policy.

## Repository policy

The canonical project uses `main`, contains no GitHub Actions workflow and has
no automatic `push` trigger. Live-provider evidence and generated run data
remain outside the repository; only reviewed sanitized summaries are committed.
