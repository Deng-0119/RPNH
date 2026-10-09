# Verification record / 验证记录

## Final implementation window / 实施最终窗口

**PASS: 71 unique cases, 71 executions, 0 failures, 0 skips; 129.29 seconds.**
The final suite ran against the exact candidate identified by source-identity.json and patch SHA-256 `bd0e2a8d362179fd68bd5451a932449f059b7038db16ca627eb84843f809bd4d`.

- 39 new cases: 22 static-reference tests, 16 interaction/legacy tests, 1 exact-carrier regression.
- 32 pre-existing targeted pure regressions: marking facade, Petri marking delta, optional resource request and firing activity.
- Every case has a unique node ID in evidence/final-case-inventory.json. Repeated windows and independent-review overlap are not added to the 71.
- All 979 candidate snapshot files matched their recorded hashes after the final run. All 973 original baseline-manifest files matched their hashes and the frozen source remained unchanged.
- The final runner installed automatic fail-closed socket and URL-retrieval sentinels. The cold-read child process also blocked socket creation. No dependencies were installed.

证据：evidence/final-focused.txt、final-focused.xml、final-case-inventory.json、python-environment.json。最终计数去重，不把中间窗口、失败尝试、独审重复运行算成新增覆盖。

## Exact command / 准确命令

The supplied existing Python environment was used throughout. From the candidate source directory:

```sh
PYTHONDONTWRITEBYTECODE=1 /workspace/scratch/18c810e6dd59/rpnh-recovery-20261003/source/.venv/bin/python ../tools/offline_pytest.py -p no:cacheprovider -q tests/test_static_lease_reads.py tests/test_static_lease_interactions.py tests/test_static_lease_exact_selection.py tests/test_marking_modularization.py tests/test_petri_marking_delta.py tests/test_optional_resource_request.py tests/test_firing_activity.py --junitxml=../evidence/final-focused.xml
```

For a local checkout, substitute its existing supported Python and the actual path to tools/offline_pytest.py. Do not install or change environments merely to preserve an old command path.

## Real Registry coverage / 真实 Registry 覆盖

- Compilation, exact M0, normal owner admission, Start, products and Success; lease reference state/ref unchanged, no duplicate token mint.
- Same-transition concurrent occurrences and distinct transitions with separate consumed control inputs; one shared reference remains valid after a sibling settles.
- Reference-only finite enumeration and repeated legal firing; no implied one-shot or concurrency cap.
- Weighted static references; missing, extra, foreign, duplicate and forged logical refs; false consume modes, missing consume index and ref/index mutation at actual transaction commit. Rejections leave events/firings/checkpoint unchanged where asserted.
- Nonempty variable claims are created by genuine predecessor Successes. Same-pool dedup, separate pools, legal A+B non-maximal-overlap union, and over-weight rejection all run through the original Registry path.
- Variable read, produce and edit consume-return work both with and without a new static read arc. Ordinary data/control reads keep consume-return both with and without lease arcs.
- Declared reset-effect Success retains the lease. Lease reset is rejected. Active replacement waits; removing only the static arc after drain may adopt through original exact token mappings, without retirement or added owner inputs.
- A fresh Python process opens the actual persisted Registry read-only at 2, 1 and 0 active firings, reconstructs active claims and settled firing state, and observes unchanged Registry event counts and exact lease refs.
- Durable completion followed by supported resume settles once and retains the reference.
- Static reference alone does not authorize dynamic physical/edit access without the existing variable arc.
- Real bad/good predecessor Successes produce a dead first carrier and a valid later carrier. Direct exact admission for the later carrier passes, the bad exact claim is rejected without mutation, and default scheduling remains unchanged.

## Retained before evidence / 保留旧失败

- evidence/baseline-failure.txt and final-test-on-baseline.txt: original 1f191645 ordinary owner admission fails with `resource lease has no exact variable consume-return inscription`; the final test was rerun against untouched baseline and still fails as expected.
- evidence/exact-selection-real-before-fix.txt: real Registry exact selection was incorrectly rejected after default enabledness selected the dead first carrier.
- evidence/exact-selection-real-after-fix.txt: the same actual Registry path passes after exact-only allowed-set propagation; bad exact refs remain rejected.
- evidence/exact-carrier-selection-probe.txt and tools/probe_exact_selection.py are explicitly synthetic typed-marking evidence, not claimed as Registry publication. The later real test supersedes that limitation.
- Earlier collection/fixture assertion failures are retained as intermediate work, not final failures or successful coverage.

## Apply check / 应用校验

`git apply --check --whitespace=error` and actual `git apply` succeeded on a fresh baseline copy. All 979 resulting candidate files matched the frozen source manifest. See evidence/patch-application.json.

## Independent review / 独立审阅

Final independent review is APPROVE for the same frozen source/patch, limited to this generic D0 repair. Its 42 pytest executions overlap the author window in 13 cases, yielding 100 unique pytest cases; 4 read/edit scenarios are separate probes. See INDEPENDENT_REVIEW_STATUS.md and independent_review/REVIEW.md. Portable-script packaging reruns preserve that count.

## Not run and compatibility limits / 未运行与兼容边界

- No cloud socket/PTY escalation, native owner transport, native client, external provider/model/API execution, installation, Actions, commit, push or submit.
- Full repository suite and local native gates are NOT RUN here. The baseline is a curated partial snapshot, not a full checkout. LOCAL_ACCEPTANCE.md describes the local gates and blocker-reporting requirements.
- Static read is a generic PN prerequisite/reference; it does not implement H7, a child origin guard, bootstrap gate, B2 target ownership, physical use permission, or application-specific undeletable arcs.
- The new Registry claim/index check is narrow to static-read transitions and preserves the Module's original consume-index convention. Other historical low-level invocation shapes remain outside this audit. Local consume-return is not equated with that Registry index.
- The exact-carrier repair does not change default selector search or promise global liveness.
