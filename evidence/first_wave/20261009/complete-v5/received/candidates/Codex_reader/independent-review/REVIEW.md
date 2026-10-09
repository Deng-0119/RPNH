# Independent review: native fixed-cut main-thread history

## Verdict

No remaining blocking finding in the reviewed native, trusted-host, refs-only package. This is approval of the Registry history prerequisite only, not a public transcript API, frontend adapter, RPC compatibility, authorization grant, or release-wide certification.

The initial implicit 4 MiB cap was a compatibility blocker. It has been removed. New entry points accept an explicit optional `max_object_bytes`; default fixed-view and legacy current projection reads use each registered object's size as the existing ObjectStore physical bound. Large valid input/answer JSON remains accepted.

## Baseline and scope

- Repository: `Deng-0119/RPNH`.
- Baseline: `ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4`.
- Independently recomputed the Git blob identities of all 1,015 materialized baseline files against the recorded baseline provenance: zero mismatches.
- Production changes are confined to `cpn/rpnh/registry/main_thread.py` and new `cpn/rpnh/registry/main_thread_history.py`.
- Other changes: one focused test file and four English/Chinese reference documents.
- No changes to core EventStore/ObjectStore, Registry schemas, writer ownership, TaskControl semantics, MainSession implementation, permission catalogs, RPC endpoints, or transport codecs were found.

## Verified behavior

- MainThreadReadCut binds task, branch, physical Registry ordinal, exact event identity at that ordinal, and exact thread version. Bare CanonicalView is not accepted by public native history entry points.
- Existing canonical visibility and lineage checks are used at the supplied cut. Reads bypass ambient positive object memo for fixed-view admission. Future/provisional objects do not enter the requested history, and future corrupt payloads do not contaminate an older cut.
- Anchors bind cut, query kind, order, exact filter, exact turn ref, ordinal, and field slot. Ascending/descending, inclusive starts, exclusive continuation, final empty continuation, changing page size, and exact-turn filtering do not skip or duplicate entries.
- Only committed turns yield conversation slots. Empty, accepted, running, interrupted, and failed states retain their existing display membership semantics.
- Reopening the same logical Registry preserves old-cut results. Another Registry with equal task/branch but different event history is rejected. A faithful copy of the same logical Registry can accept the cut; this is source identity equivalence, not authorization.
- Typed history returns field references and structural facts, not user_input/answer JSON, plugin configurations, private task instructions, child paths, or child bodies. Existing MainSession safe rendering and its explicitly live launch annotation remain separate.
- Existing project_current_thread/recover_thread dictionary values and shape match the baseline implementation on committed, active, stopped, and child-linked fixtures.
- Read-only tests deny writer acquisition/begin/prewrite/metadata writes and SQL mutation, and compare persisted Registry/object/profile/owner-lock state and event/object/head/epoch counters. SQLite volatile SHM read marks are appropriately excluded.

## Independent test evidence

Final guarded command, from the package source directory:

    $HISTORICAL_WORKSPACE/rpnh-recovery-20261003/source/.venv/bin/python ../independent-review/run_guarded.py -q tests/test_main_thread_history.py tests/test_main_thread_registry.py ../independent-review/test_independent_history.py --junitxml=../independent-review/results.xml

Result: **32 passed in 24.56 seconds**: 14 history regressions, 12 existing main-thread regressions, and 6 independent probes. No socket connect/bind or subprocess launch is permitted by the review runner's Python audit hook. Tests use real local Registry fixtures and mocked receipt observations, not a provider/model or child execution service.

The six independent probes cover same-task/branch foreign event identity, faithful logical Registry copy, future corrupt object isolation, old dictionary equivalence against the baseline class, variable page limit plus empty terminal continuation, and fail-closed current symlink validation. Review helpers live outside the candidate source.

A preliminary overbroad command mistakenly included all MainSession tests and was interrupted after entering an out-of-scope native child execution test. It produced no completed result and is excluded from all validation counts. No claim is made that the full MainSession or release suite passed. Subsequent accepted evidence uses the explicit socket/subprocess guard.

## Native limits and next boundary

- Each page still reconstructs and validates the entire main-thread lineage at the cut. The maximum 100 response entries is not a total scan, CPU, or memory bound.
- The explicit object budget does not bound metadata already materialized by native Registry row queries. Registered-size body reads are bounded, but total history allocation is not.
- Existing child-link path safety checks inspect the current filesystem/symlink boundary, without opening child Registries or bodies. A filesystem boundary change can make an old cut unreadable; it does not rewrite old-cut facts.
- A cut or anchor is not a bearer capability, authenticated cursor, or user permission. Existing trusted-host session/source access checks remain the caller's responsibility. No new user-level authorization mechanism is introduced.
- A public adapter still needs the existing safe display filter at an authorized fixed-cut content/renderer boundary, plus an explicit treatment of live TaskControl annotations. This package does not certify Codex thread listing RPCs, itemsView, cold resume, or client transport behavior.

## File identities

See `reviewed-sha256.txt` for the exact seven reviewed candidate files and `results.xml`/`results.log` for the completed independent result.

## Frozen package confirmation

The author confirmed the seven-file source freeze. The final manifest hashes all match this review's recorded files. Independently ran `git apply --check`, applied the patch to fresh baseline target files, and compared every resulting file byte-for-byte with the frozen source: all seven matched.

Patch SHA-256: `f4726d7c1a230c8935b22310bb1d29f7c8e68342ef9b660cabb6430b8a3d4654`.

The author's separate latest-main compatibility work is outside this independent 32-test count and does not change the frozen patch scope.
