# Final offline validation

Independent decision: **ACCEPT_LIMITED_OFFLINE_CANDIDATE** for mechanical origin lowering plus the enumerable declaration subset only. Read the unchanged `review/REVIEW.md` before inferring scope.

## Final byte identity

- Input: 994 files, manifest aggregate `a3af1d85ffbc8acac72e719fed316ed3c848229fce92026c62e571cb77a913a2`.
- Candidate: 999 files, aggregate `7867bec81c0830f19cfd6f8a58b0d1598d5efc8220c69e6cfdd02604f98f1a0e`.
- Root overlay `bound-child-declarations.patch`: `2bc8dd99115f2d139f6b17dd8cd1342087a02110233b8d43c5c37c5199a9cc98`.
- Both author and reviewer independently applied that patch to the exact input and obtained the candidate manifest. Original source input was checked after author tests and remained byte-identical.

The input already includes the original H7 core and acceptance-history implementation. Apply this overlay once to that exact input; do not also reapply either earlier patch. The package includes the exact input under `inputs/acceptance-history-source/` and its source identity under `inputs/acceptance-history-identity.json`.

## Actual final runs and deduplication

| Set | Author | Independent rerun |
|---|---:|---:|
| New lowering/declaration tests | 28 PASS | 28 PASS |
| Existing compiler/ControlIR/worker serializer | 516 PASS | 516 PASS |
| Existing parent-child core/history/static leases | 177 PASS | 177 PASS |
| New independent lowering/Registry/declaration probes | — | 46 PASS |
| Additional ordinary structural revision control | — | 1 PASS |

Author unique: 721. Reviewer unique: 768. Shared identities: 721. **Joint union: 768 distinct PASS, zero final failed/skipped cases.** One subprocess-based native cold-process test was deliberately deselected; six additional native-net-operation cases could not collect because the frozen input lacks their imported example. They are NOT_RUN. Earlier source-input 298-case evidence, old failed executions and successful reruns are not added.

Each accepted author run has exact before/after source manifests equal to the final candidate, real exit 0, collected node IDs, outcomes, logs and XML. The independent review separately verifies the same candidate before/after. All final runs report zero forbidden socket/URL/subprocess/fork/exec/PTY attempts. The existing interpreter is Python 3.12.14 with pytest 8.4.2; nothing was installed.

See `evidence/FINAL_TEST_INVENTORY.json`, `JOINT_TEST_COUNTS.json`, `review/TEST_COUNTS.json` and `review/REVIEW_IDENTITY.json`. `FAILURES.md` and original logs preserve the fixture failure, repaired legacy normalization regression and corrected patch header, as well as the independent review's early failed probes.

## What is and is not established

The origin PN is now supplied mechanically by product composition and checked by the original compiler/wire reader. Real temporary Registry tests establish protected-origin exact references across admission, Start, products, Success, business fanout, same-transition concurrency, variable resource behavior, cold readonly hydration and fail-closed low-level mutation/adoption.

This does not authenticate installed HOST code or prove a complete public execution-material inventory. FrozenBoundChildDeclarations cannot be passed as PreparedChildMaterials or intent authority; `require_execution_materials()` remains unsupported. AgentTask profile contents are deliberately unread and outside its declaration digest. The complete registered public/opaque-binding material contract is future work.

Native issuer, peer/receipt, directory reservation, both worker compositions, native child binding, parent terminal completion, D1, H7b/H8 and consumer migration remain unimplemented/unverified. No real client/model/business execution, native worker, installation, Actions or push occurred. No full-repository or current-remote-main claim is made.
