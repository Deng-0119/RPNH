---
name: rpnh-result-erp-runtime-20261008
description: "ERP synthetic native runtime validation"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: en
  counterpart: README_ZH.md
  revision: "2026-10-09.1"
  status: historical-curated-result
---

English | [中文](README_ZH.md)

# ERP synthetic native runtime validation

This historical validation used synthetic backends, real workers and AF_UNIX. It did not run a real provider, Odoo world or original ERP grader. The [complete finite status projection](results.json) identifies the five-file ERP overlay and independent C fixture over base `dbad00458e9b356fcaf0bb97ceb90258ed9b1de0`; publication at `e92b05c9afe324ebb675f2d67b73c02efe7b9536` was not a new benchmark run. Core was unchanged.

| Scope | Original local result |
|---|---|
| Offline | 65 unique tests passed; 0 fail/skip; 4 additional subtests |
| Historical AF_UNIX-denied subset | 15 blocked cases later passed locally; included in 65, not added |
| B installed lifecycle: complete | PASS, terminal present, 4 scripted submissions, process exit 0 |
| B installed lifecycle: stop | PASS, quiescent without terminal, 3 scripted submissions, process exit 2 |
| B installed lifecycle: timeout | PASS, quiescent without terminal, 3 scripted submissions, process exit 2 |
| C explicit unknown / backend exception / lost completed reply | Three PASS scenarios; each one dispatch, request and synthetic backend effect |
| C known nonzero failure / domain infeasibility / completion | Three PASS scenarios; each two dispatches, requests and synthetic backend effects |

The C fixture's total was 9 worker dispatches, 9 bridge requests and 9 synthetic backend invocations. Three unknown scenarios produced `started -> outcome_unknown`; 18 same-ID/changed-body/new-call probes across original and reconstructed services added no receipts or dispatches. Known returned results supported cached readback, rejected identity/body conflicts and allowed valid new IDs. Service reconstruction reused the same owner/Registry, so this is not OS-owner crash recovery or exactly-once ERP transactions. Unrun unknown variants remain listed in the projection.

The input cohort is synthetic lifecycle/receipt cases, not ERP business tasks. There is no business scorer; acceptance compares receipt/admission state with explicit expected scenario outcomes. B's installed versions were RPNH 0.1.0rc2, plugin 0.1.0 and Harbor 0.24.0. The C source command is parameterized below; PYTHON is the selected installed interpreter, SOURCE the historical overlay, PATCH_PAYLOAD the frozen five-file reference, OUTPUT a new directory, and TMP a short native temporary directory:

```sh
env -u PYTHONPATH TMPDIR="$TMP" "$PYTHON" -B   "$SOURCE/examples/erp_bench/scripts/unknown_native_acceptance.py"   --output "$OUTPUT" --patch-payload "$PATCH_PAYLOAD"
```

The frozen external reference must be supplied separately; it is identified in `results.json` and not redistributed here. For CLI lifecycle and offline setup see the [ERP guide](../../../examples/erp_bench/README.md). This recipe is not a newly executed check. Historical AF_UNIX permission failures keep their old status; local success does not relabel those windows. These finite checks do not validate the combined V6 product.


The [distribution manifest](MANIFEST.json) identifies these rewritten summary/projection bytes, not original materials.
