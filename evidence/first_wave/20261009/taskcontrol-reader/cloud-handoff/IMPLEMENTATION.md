# H2a: existing TaskControl readers converge on Registry

## Result and boundary

The existing `TaskControl.result()` and `_read_terminal_status()` now consume
`read_run_execution`; result bytes come from `read_run_terminal_bytes`. No new
CLI, reader command, Registry, state mirror, checker, owner, or execution policy
was added. Registry retains ownership of identity, terminal closure, provenance,
and publication checks.

Both consumers recheck the exact same captured cut after their output has been
built, including JSON decode and the existing cumulative model-call query.
Status evidence/index counts remain historical canonical counts, explicitly
selected through the read cut. The JSON result keys, exact run outcome, decoded
output, status task-ID string, and generation behavior are preserved. A current
running/stopped generation does not return an older terminal result.

The only additional core change is in `_read_run_descriptor`: if no caller
budget is supplied, the physical read is bounded by the registered object size.
TaskControl body reads use the same registered-size bound. No 4 MiB product cap
was introduced. An explicit caller descriptor budget retains its original
precedence. A physically enlarged or shortened backing file errors; output is
never silently truncated. This protects against backing-file inflation, not
against legitimately large registered objects or large Registry metadata.

Status still combines independent observations of the process, Registry, and
owner socket. It does not promise a cross-channel atomic snapshot. Stop, resume,
OS signals, owner authority, and process status precedence are unchanged.

## Provenance and changed files

Remote main and recursive tree were verified through the connected GitHub
reader at commit `ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4`, tree
`f876ff60993c09cc218d333cf6999e3fe457aca5`. The base is an evidence-only
successor of `674252feb836f631c162979f177d1fe91f22559f`.

This workspace is a materialized source subset, not a Git clone. All cpn files
and all existing tests were matched against remote Git blob IDs before edits;
1,334 repository blobs were materialized. Missing unrelated documentation,
example/support files are enumerated in `baseline-provenance.json`. No missing
cpn or tests file was hidden. The prior Registry-reader source was preferred;
other local baseline artifacts were used only when their bytes matched the same
remote tree. H1 unmerged implementation changes were not imported.

Five changed paths:
- `cpn/rpnh/task_control.py`: migrate the two existing consumers; preserve shape.
- `cpn/rpnh/registry/run_authority.py`: registered-size physical default bound.
- `tests/test_task_control_registry_reads.py`: focused real-Registry regressions.
- `docs/ARCHITECTURE.md` and `docs/ARCHITECTURE_ZH.md`: aligned read contract.

`file-manifest.json` records baseline Git blobs and before/after SHA-256 values.
`tested-source.sha256` and `tested-source-digest.txt` identify the materialized
source. `runtime-provenance.json` records interpreter and actual imported source
paths. The patch applies cleanly and all five applied file hashes match the
source exactly (`patch-apply.log`).

## Validation evidence

See `validation-summary.json` for final machine-readable outcomes. Different
batches overlap; their counts must not be added as distinct tests.

- Focused consumer suite covers complete/failed outcomes, JSON string/object/
  array/null, original keys and task-ID shape, read-only fingerprints, canonical
  wrong/cross-run identities, stale terminal selection, current generations,
  final head/epoch/run-pointer checks, post-decode races, large legal result
  bytes, descriptor/body physical bounds, explicit budgets, Basic/application
  dispatch, existing status error behavior, stop signaling and resume gating.
- A real Registry imported-accounting fixture verifies nonzero historical
  counts across generations without invoking a provider. The separate `(13, 2)`
  spy checks exact accounting delegation and final guard ordering only.
- Existing affected suites cover strict descriptor parsing, package result
  projection, UI/Basic boundaries, and shared core-reader consumers.
- Old baseline: 20 targeted regressions fail as expected (`old-red.log/xml`).
- Independent review includes >4 MiB body and >4 MiB valid native-run descriptor
  checks, same-cut old-red/new-green probes, affected core regression checks,
  and a separate read-only/static review.
- Native AF_UNIX task-control/status: 6 pass, 1 blocked by sandbox EPERM at
  socket construction. This is not a native runtime pass. Local commands and
  the additional native resume gate are in `COMMANDS.md` and review evidence.

Initial author runs exposed only new-test setup mistakes (a noncanonical test
schema ID, a missing fake-handle task_id, and missing fake channel readiness).
They were corrected in test fixtures; the production stop logic was unchanged.
Those raw logs are retained rather than repackaged as product failures or passes.

No real model, external business service, GitHub Actions, remote write, commit,
push, or task business-policy/scoring change was performed. Completion of the
native local gate remains with the supported local environment.
