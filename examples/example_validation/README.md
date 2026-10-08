# Shared example evidence contract

[中文](README_ZH.md)

A small, read-only publication envelope for the first parallel examples. It
borrows the exact-byte hashes, retained-private/distributed-copy distinction,
append-only history and independent scoring boundaries from
[AutomationBench evidence](../automationbench/docs/EVIDENCE.md). It does not
replace that example's schemas, launch work, call providers, write Registries,
apply patches, or score tasks. Existing RPNH Registry and original graders stay
authoritative. The helper uses only the Python standard library.

## Files and return format

- `result-manifest.schema.json`: strict draft-07 structure, version
  `rpnh/example-evidence/v1`.
- `template.json`: incomplete ERP2299 example, **not a run or successful result**.
- `validate.py`: read-only shape, consistency, byte-hash and safe-path checks;
  emits `rpnh/example-merge-summary/v1` JSON on stdout.
- `tests/test_validation.py`: synthetic offline fixtures only.

Each lane returns a **new** `result-manifest.json` beside its allowlisted public
artifacts. Use stable, unique record IDs. The logical condition must pin task
scope, reveal/feedback policy, seed/reset policy, permitted action surface,
evaluator and budget semantics. Hash its exact file bytes, not reserialized
JSON. Hash the exact allowed task instructions and input manifest; an input
manifest in turn lists immutable input hashes. Missing identities stay `null`
with an explanation. Do not hash a live private launch profile and present that
as a safe public configuration: retain a separately reviewed, secret-free
logical model configuration containing exact provider/model IDs and relevant
parameters/budgets. No keys, tokens, accounts, private endpoints or local paths.

`source` identifies the RPNH base plus the exact tested commit if available.
Uncommitted overlays use `tested_commit: null` and final per-file hashes; never
invent a commit for them. Include **every** changed owned file and its base hash
(`null` only for additions), final hash and relative path. Deletions are not in
this first-wave contract. Freeze an immutable source snapshot before computing
hashes. The helper checks listed files only; the integrator must compare the
actual diff/inventory, reject undeclared changes and preserve unrelated files.

Ownership for this wave: cloud `examples/slopcodebench/`, user-local
`examples/erp_bench/`, integrator `examples/example_validation/`. The WNTR/Chama
reserve receives its own explicitly assigned prefix if activated. Do not create
aliases, edit another lane, change core/global catalog, or apply onto a dirty
worktree. Check the same agreed base in a fresh checkout before merge.

## What a stage means

All five stages are independent: `offline` pure contract checks; `mock` fake or
scripted-provider interface checks; `native` real installed-owner/native/service
integration; `provider` actual external-model execution; `evaluation` original
or supplementary grader completion. `passed` means that stage's command
completed its intended check. **Evaluation passed does not mean the business
task passed**: retain the original failing score and every component. Commands
in public records must be reproducible and secret-free; exact private command
logs can remain private evidence with a public parameterized command.

Every passed/failed stage needs retained evidence. Blocked/not-run/unknown are
separate and explained. Do not substitute fake-provider or patched-transport
checks for native acceptance. `model_calls` remain `null` when unknown; a zero
requires observed evidence of the full stated scope. Fake counts and real
provider counts are separate. This validator checks referenced evidence and
never derives counts or execution truth from labels.

Original scores retain exact grader identity and original component names and
values, plus the immutable grader output. `benchmark_result` requires a real
model, original task semantics, native acceptance, actual Registry evidence and
frozen condition/input identities. Fake runs can produce `grader_compatibility`
results; added checks are `supplementary`. Neither is an official model score.
The original scorer may define a zero for a blocked run: retain that original
record without converting it into a completed RPNH benchmark result. Private
or structured grader detail stays in its evidence artifact; scalar components
are an exact projection, not a new aggregation. Do not combine scores across
changed conditions or imply a checkpoint 1→2→3 prefix completes all five SCB
checkpoints. ERP2000 is a separate smoke record, never ERP2299 success.

## Artifacts, Registry and history

Public artifacts use safe relative paths, byte sizes and SHA-256 of the exact
**distributed bytes** (including redaction/newline changes). Private evidence
has no path in this envelope; retain only its digest, size, role and
`retained_private_bytes` scope. Keep raw provider transcripts and Registry DBs
private. Each referenced file is an immutable retained snapshot; avoid mutable
`latest` files. The helper checks integrity when roots are provided; it cannot
enforce filesystem immutability or verify inaccessible private originals.

Copy Registry `ref` payloads unchanged: VersionRef has `entity_type`,
`logical_id`, `version_id`; ResourceVersionRef has `resource_id`,
`resource_version_id`. Keep the owning `task_id`. A sanitized actual TaskControl
or result-evidence projection must contain the exact reference and task; the
helper reparses it. Strip private paths/content without changing retained IDs.
Absent Registry output is `unavailable`, with no made-up identifiers. The
contract never registers a value, invents authority or joins live Registries
across machines. Domain lineage/reuse evidence remains an example-specific
artifact; distinguish code edits, process-definition revision, definition reuse
and valid numerical-result reuse.

Each new attempt, configuration change, remediation or rescoring creates a new
record. `history.previous_record_artifacts` links retained earlier records by
artifact hash. Preserve failed/blocked attempts and untouched original scores;
a correction never overwrites the earlier record. Checks against a merged tree
are new evidence; old results keep their original tested identity.

Route findings to upstream task/scorer, bindings/configuration, harness,
model reasoning, or environment/unknown. Label certainty separately as
`hypothesis`, `observed` (observation exists; cause unresolved), or `confirmed`.
Confirmed model/harness attribution requires configuration to be excluded first,
with concrete evidence and a reproduction. Unconfirmed source-level integration
gaps and design questions remain reportable without that claim; use unknown
when the category itself is unresolved. Include expected behavior, observation,
impact, next step and proposed owner. Source hypotheses and authored reference
numbers are not measured experiment failures or gains.

## Validate, review, then merge

From the repository root, with the return package and clean baseline available:

```sh
python examples/example_validation/validate.py RETURN/result-manifest.json \
  --artifacts-root RETURN --source-root FINAL --base-root BASE \
  --expected-base ae09445fe1d9b973502bc5d2c961976c1d2c0163 \
  --allow-prefix examples/erp_bench/ > merge-summary.json
python -m unittest discover -s examples/example_validation/tests -v
```

Use `examples/slopcodebench/` for the cloud lane. Options omitted are explicitly
unverified in the summary. The template passes structural checks while all
runtime stages remain not run. `merge_review_ready` only means the requested
base/path/hash checks, declared public-file review and offline pass are present;
it is **not** merge approval, real-run acceptance or permission to publish.
Verify the actual Git base and complete diff separately. Review every public
file and free-text field for secrets/private paths before setting privacy to
passed; this helper is not a secret scanner. A reviewer claim is not a scan.

Merge disjoint source changes first, rerun focused checks on the combined tree,
and run affected installed-owner/evaluator acceptance on the actual final
revision. Do not relabel earlier scores. Native/provider/evaluation blockers
remain open even if source preparation is ready. No real models, installations,
large downloads, Actions or pushes are part of these pure checks.
