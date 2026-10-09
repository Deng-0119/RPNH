from pathlib import Path
import json
p=Path(__file__).resolve().parents[1]
v=json.loads((p/'evidence/FINAL_VALIDATION_INVENTORY.json').read_text())
assert v['unique_testcases']==139 and v['final_execution_count']==184
identity=json.loads((p/'SOURCE_IDENTITY.json').read_text())
s=f'''# Final validation: narrow H7 Registry core offline candidate

Final source: 992 files, aggregate `{v['source_aggregate_sha256']}`.
H7 overlay patch: `{identity['H7_patch_sha256']}`.

## Completed final verification

- `final21`: 99 PASS, 0 fail/error/skip, 3 explicitly deselected subprocess tests; real pytest exit 0 and byte-identical source before/after. This is 31 author core cases and 68 adjacent Registry/Module/S1/recovery cases.
- Independent `final-v2`: 85 PASS, 0 fail/error/skip, exit 0, zero sentinel-blocked events, byte-identical source before/after. This includes 40 reviewer-authored boundary cases plus reruns of the same 31 author and 14 existing adjacent cases.
- Across those final groups: 184 successful executions, 45 overlapping node IDs, **139 unique parameterized testcases**: 31 author core, 68 adjacent and 40 reviewer-authored. Repeated runs and earlier-source results do not inflate this count. Exact IDs, overlap and exclusions are in `evidence/FINAL_VALIDATION_INVENTORY.json`.
- Exact S1 input: 979 files, aggregate `4a7841ac16432aeb173562ec797bc062839a006fdb684d5ea1e3b161c6c49fbc`; S1 patch `bd0e2a8d362179fd68bd5451a932449f059b7038db16ca627eb84843f809bd4d`. Source-identity and patch hashes match the accepted dependency.
- Applying this H7 patch to a disposable exact S1 copy passes `git apply --check` and `git apply`, yielding all 992 final files with the exact final aggregate. See `evidence/patch-application.json` and `patch-reconstruction-manifest.json`.
- `tools/verify_identity.py` rechecks candidate, S1, both patch identities and full manifests without importing the product. See `evidence/final-identity-verification.json`.

## What the execution demonstrates

The tests use original Registry facts, SQLite transactions, compiler/admission, native operation Start, products and Success. Protected origin is a real resource/lease reference under the S1 semantics. The independent tests cover low-level replay/stop/writer closure, child reentry, protected provenance, weak-edge atomicity and post-fix protected lifetime rejection before commit.

The last independently reproduced defect was a wrong-but-locally-valid capability lifetime committing before a later integrity check rejected it. The protected prospective validator now checks exact bootstrap lifetime and origin kind; same-scope defensive checks also close marker self/exact identities and complete relation sets. `PROTECTED_CLOSURE_AUDIT.md` explains which facts are checked here and which remain in the original generic resource validator. The independent extra-edge probe already failed closed on the old source and is not called an additional old defect.

The author/native fixtures explicitly construct `_NativeBoundaryEvidence` in test-only code. A temporary child Core is made before that injection. This is **not** production evidence issuance, kernel peer authentication, physical target reservation, Core-before-reservation safety or an actual AgentTask/Module bound wrapper. Normal in-memory stop/drain is only established with a socket-free owner-channel shim. No native transport or end-to-end parent terminal completion is claimed.

## Preserved failures, incomplete runs and superseded results

All small original logs/results are retained. They contribute zero to the final count above.

- Exploratory runs 1–6 had fixture/catalog setup errors; 7–12 drove relation/catalog binding fixes; 13/14 passed two initial cases; 15 had five fixture/exception errors. Run 16 is INVALID_FOR_FINAL_EVIDENCE because source changed while it was running. Run 17 had 26 PASS plus an exception expectation mismatch; run 18 had 28 PASS plus an invalid replay expectation.
- Source-v1 `core19` completed 31 PASS. Source-v1 `regressions03` completed 68 PASS with 3 explicit subprocess exclusions. The first independent 38 and later 45 replay results are preserved against that old aggregate, not reused as final-source passes.
- `regressions01` could not collect a module because the curated source lacks `examples/net_operations/live_agent_replacement.py`. That file was not fabricated or fetched into the frozen snapshot.
- Source-v1 `regressions02` has only a 38-byte partial progress log containing 37 dots and one failure marker, without final traceback/XML/result/after-manifest. The old independent `final-rerun.log` is also incomplete. Their unresolved historical outcomes are not silently recategorized as PASS or inferred to be harmless environment failures.
- Patched-source `core20` and `regressions04` produced complete passing pytest XML/logs, but a runner edit while their Bash wrappers were active broke the trailing bookkeeping (wrapper exit 2). The error and manually captured source-after identity are recorded. They are INVALID_FOR_FINAL_RUN and were repeated in the clean, frozen-runner `final21` above.
- The independent old-source lifetime probe is a real FAIL with a committed protected-origin transaction. Its untouched test passes on the final source and the reviewer preserves both results.

## Exclusions and limits

The three deselected tests explicitly spawn another Python process: S1 cold-process reconstruction, invocation cold import, and operation facade cold import. No subprocess permission was assumed. The full names are in the inventory and reproducible selection scripts.

This is a curated partial snapshot and a selected regression run. It is not full-repository PASS, does not certify the missing examples/history, and makes no assertion about the current remote HEAD or merge state.

H7 full 66-item D0 is incomplete; all 36 D1 specifications and all 10 H7b specifications remain NOT_RUN. Missing production work includes selected-installed inventory/material collection, native issuer/ticket/Popen observation, two-way peer/strict receipt, sticky-stop owner ordering, physical target reservation, both actual wrapper compositions/general lowering, historical acceptance classifier, and parent native terminal observation plus registered completion. Parent native-launch Success remains UNSUPPORTED and capacity/claim is retained. Test-only inputs cannot be promoted into native proof.

No product source, frozen design or S1 input was changed after the final manifest. No upload, push, install, Actions or consumer migration is part of this candidate. See the independent `FINAL_REVIEW.md`, `NATIVE_HANDOFF.md`, `LOCAL_FOLLOWUP_TASK.md` and `REPRODUCE.md`.
'''
(p/'VALIDATION.md').write_text(s)
readme=p/'README.md';text=readme.read_text();text += '\n## Final package\n\nFinal validation: 139 unique selected offline testcases (184 executions across author/adjacent and independent groups), with exact deduplication and preserved failures in VALIDATION.md and evidence/FINAL_VALIDATION_INVENTORY.json. This count is not the frozen 66-item H7 D0 matrix.\n\nThe self-contained candidate includes exact S1 source, H7 source/patch, frozen design and both reviews. See REPRODUCE.md for static verification and sentinel-constrained tests, PROTECTED_CLOSURE_AUDIT.md for the final atomicity repair, and LOCAL_FOLLOWUP_TASK.md for the unexecuted native work. Generated Registry databases, caches and interpreter environments are intentionally excluded.\n'
readme.write_text(text)
print('Final validation document written from asserted final result inventory.')
