# Local acceptance handoff / 本地验收任务书

## Objective and authority / 目标与范围

Apply and validate the generic static resource-lease read primitive on the user's authoritative Deng-0119/RPNH checkout. This is not H7 implementation. Do not add child launch, bootstrap/origin guards, B2 target ownership, a parallel marking, reader table, new lock policy, or provider/model calls. This package has not been pushed or submitted.

在用户电脑的权威 RPNH checkout 上应用并验收通用 lease read primitive。环境必须由父任务确认；不要私自换环境或覆盖用户未提交修改。只有确认用户已授权本地发布后才执行其指定 commit/push；云端候选本身没有做任何远端写入。

## Before application / 应用前

1. Read that checkout's AGENTS.md and relevant local test instructions.
2. Verify repository origin is Deng-0119/RPNH and report current local HEAD, origin/main, branch and git status. If origin/main or touched files differ from the candidate base, inspect the actual delta before applying. Do not blindly reset or overwrite.
3. Baseline identity is in source-identity.json; the tested main is 1f191645c4d60c8b190d42e9fad99c85e8981c03, with product baseline d92ff37.
4. Run git apply --check --whitespace=error with static-lease-reads.patch, then apply only in the authorized review branch/worktree. Verify all changed-file hashes with tools/verify_changes.py. A rebase or conflict resolution invalidates the old exact-byte test identity until reverified.

## Offline gate / 离线门槛

Run the seven test modules listed in VALIDATION.md using the local existing supported interpreter. The package's tools/offline_pytest.py installs fail-closed socket and URL-retrieval sentinels. Do not install dependencies without permission. Require all final cases to pass, record JUnit and exact source hashes, and inspect for unintended source/schema/config changes.

The test list includes three new modules and four pre-existing modules. Counts are deduplicated by node ID; repeated attempts, intermediate failures and independent review overlap are not extra coverage.

## Local native gates / 本地原生门槛

These gates are NOT RUN in the cloud candidate. Use a real local OwnerEventLoop/Harness with the project's existing deterministic/offline registration and host adapters, under the user's local authorization. No real model/provider call is needed.

- Native owner dispatch: two transitions with separate consumed control/input tokens share one static lease reference. Verify two live firings, then settle one while the sibling remains active. Verify the same exact lease ref/state survives both Successes and the original Registry reader reports coherent active/publication state.
- Same-transition occurrences: two consuming inputs permit two live occurrences of the same transition sharing the lease. Confirm the reference does not itself serialize or impose a firing limit.
- Stop/drain boundary: stop new admissions, drain or retain outstanding claims under existing owner semantics, and verify no duplicate/missing lease token or invented successful terminal. Do not introduce a new stop policy.
- Native reentry/recovery: after a durable executor completion but before ordinary Success, close the host and use the supported resume path. Confirm one settlement and exact reference retention. Repeating recovery must follow existing idempotency/reentry rules, without a second output mint.
- Existing variable coexistence and ordinary read: exercise one same-pool read union and one legal variable consume-return through the local owner transport. Confirm data/control read still consumes and returns its own occurrence.
- Exact selection: the new dead-first/valid-later carrier case remains an explicit exact-admission check. The default selector still need not search past a dead first carrier; do not report a scheduler liveness fix.
- Dynamic changes: active reference replacement remains DRAINING; lease reset/ordinary retirement is rejected. After drain, removing the generic static read while preserving the pool may adopt through existing exact token mapping. This is intentionally not an H7 origin guard.

A blocked native gate must be reported with the exact environment or test blocker. Do not substitute passing cloud D0 tests for native acceptance, and do not count a denied socket attempt as a completed gate.

## Publication / 发布

After the authorized local gate and review pass, follow the user's explicit publication scope. Do not add Actions push triggers, push generated Registries/databases/run directories, include credentials, install native clients or invoke models. Verify the exact remote commit after any authorized push and report its hash plus the final test evidence. If publication was not authorized in the local task, stop with an applied/verified candidate for review.
