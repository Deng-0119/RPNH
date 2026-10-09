---
name: rpnh-release-validation
description: "Separate the historical full offline baseline from version-specific focused validation."
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: en
  counterpart: release-validation_ZH.md
  revision: "2026-10-09.1"
  status: historical-version-specific-validation
---

[English](release-validation.md) | [中文](release-validation_ZH.md)

# Release validation boundaries

This page records specified historical source windows. It is not complete certification of the combined V6 product, current main or a new rc2 binary. Curated results retain source, overlays, inputs, commands and complete statuses; raw runtime materials are not distributed here. Offline/native passing checks cannot establish a real model route, stock frontend or unrun business journey.

## Recent historical native windows

| Source and scope | Result |
|---|---|
| `00f2d29c7deffed44e2ec635a24f390c6e0d9ace` + 16 example files | [Atomic tool](../results/tool-pipeline-20261008/README.md): 32 unique / 33 executions PASS, real AF_UNIX, zero models |
| `dbad00458e9b356fcaf0bb97ceb90258ed9b1de0` + 5 ERP files and C fixture | [ERP runtime](../results/erp-runtime-20261008/README.md): 65 PASS + 4 subtests; B three lifecycle/C six scenarios PASS, no Odoo/grader |
| `674252feb836f631c162979f177d1fe91f22559f` + H1 seven files | [H1](../results/entry-reader-20261009/README.md): 39 unique / 39 executions, PASS_NATIVE_FINITE |
| `715468dab0b1bea07d7e94a7aa0606eaf194365c` + H2a five files | [H1+H2a](../results/entry-reader-20261009/README.md): 166 unique / 166 executions, PASS_NATIVE_FINITE; includes units and 12 independent package cases |

Old AF_UNIX EPERM/native-resume blocks and earlier shared-harness A's PARTIAL_ENV keep their original statuses; H1/H2a did not relabel them. Final OS signal-handler identity was not separately probed. Do not add passes from different windows as coverage of one product.

## Historical finite integration on 2026-10-06

The older freeze04 actual Chromium startup was BLOCKED by root/sandbox restrictions, without successful page/real-DOM/screenshot acceptance. HTTP, Node and mock DOM passing checks do not change that result. This was finite acceptance of selected contracts, not full HOST/all I00–I10/advanced25 acceptance. Business results are [AutomationBench first18 5 PASS / 9 FAIL / 4 BLOCKED and separate repair4 1 PASS / 3 FAIL](../../examples/automationbench/PUBLIC_RESULTS_20261006.md); the older 14 scored tasks were not rerun. Historical publication source `f58a0f061d1daf4c09fc96c43237c613cc43f439` checks are distinct from freeze04/repair experimental source:

| Publication boundary | Historical result |
|---|---|
| Python / documentation unittest | 198 distinct Python nodes passed; separate 26 documentation tests OK |
| Node | 224 distinct titles passed; absent-Node HTTP 1 PASS / JavaScript 1 SKIP; repeated HTTP does not add coverage |
| Schema/resources/install | 268 schemas, 1 catalog; 674 files in each wheel/sdist; build/install/dependencies/assets exit 0; 7 zero-model commands passed outside source |
| Viewer | 6 rebuilt assets byte-equal; not actual-browser certification |
| Documentation | 122 maintained pages/61 pairs + 30 supporting pages; 152 HTML; 10,983 local links without failures; 64 external links not fetched |
| Example contracts | 2 bilingual schema examples/empty catalog valid; 3 invalid mutations rejected; syntax/schema only, no live example execution |

These historical publication checks made no real provider/model/judge/browser calls and cannot be transferred to V6. Initial failures and later corrected windows remain separate historical records.

## Earlier complete offline baseline

The complete suite on 2026-09-28 used `073a4516013443fadfcd05fa81d29c4aa1b5391b`, with later focused records through `87e98356a1ba78b6afd7bae93d26704e030467a3`. Its host was Linux/WSL2 x86-64, Python 3.13.12; Python 3.11 was absent and Python 3.12 lacked full test dependencies. The original result scopes below do not certify later trees.

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

## Historical focused delta

Runtime changes after the complete-suite baseline were validated with the
smallest directly affected sets rather than an unrelated full-suite rerun:

| Boundary | Focused evidence |
|---|---|
| AgentLoop, registered-host, frontend and checkpoint closure | 14 tests passed at `de53768` |
| Persisted v1 compatibility plus current checkpoint reopen | 2 tests passed at `de53768`; old optional `next_attempt_allowed: false` remained readable and did not block reopen, while current writers omitted it |
| Registered-operation and resource-service recovery | 16 tests passed at `de53768` |
| Workspace rejection/recovery, execution-child closure and Viewer HTTP/assets | 63 focused tests passed at `87e9835`; directory/FIFO/NUL rejection, directory correction, owner-stop FIFO restoration and incomplete-client isolation were included |
| Actual wheel Viewer gate | A wheel built from `87e9835` passed the fixed top-level and vendor-file/license set; an empty manifest is rejected |
| Historical documentation and configuration reference | 20 tests passed; 76 pages, 420 internal links, 38 language pairs and the built 76-page site passed |
| Historical Registry projection | A preserved large 3-DOF run opened through `rpnh net` at verified event head 16185 without acquiring writer authority |
| `v0.1.0rc1` complete collection | At `1e85b4f`, 790 tests passed and the pinned OpenCode PTY test skipped because its executable was absent; 23 Unix-socket tests failed before protocol handling because the operator-supplied temporary root made their socket paths exceed the platform limit. Both affected files then passed all 30 tests with a short temporary root. No source change was needed, so the combined candidate evidence covers 813 unique passing tests and one documented environment skip without presenting the first run as a single clean pass. |

These sets verify exact checkpoint reentry, workspace candidate settlement,
immutable failure evidence and indexed Registry reads. They are not a new
complete-suite total or a live-provider campaign. Documentation checks validate
metadata, links, syntax, language pairing and schema examples; they do not
execute the model-backed examples.


## Using these records

Historical offline/native sets made no real provider API calls; route reachability requires a separately authorized condition. The packaged provider/model catalog is empty, with no preselected provider, endpoint, credential or exact model. A current version's build, installation and documentation results require its own release record; this rewrite executed none of those gates. See [curated results](../results/README.md) and [examples validation](examples-validation.md).
