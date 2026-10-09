# Static lease-read core candidate / 静态 lease read 核心候选

Status: independently approved, source-frozen offline candidate. Final local acceptance/push remains separate and pending. This package does not implement H7.

状态：独审已批准、源码已冻结的离线候选；最终本地验收/推送仍待完成，不代表 H7 已实现。

## Source / 来源

- Authoritative repository: `Deng-0119/RPNH`, branch `main`.
- Verified main: `1f191645c4d60c8b190d42e9fad99c85e8981c03`; product baseline `d92ff3704b6002bf5ecbccb3e6a3d1489809a805`.
- The baseline is an independent copy of the frozen seam source, a curated partial repository snapshot. All 973 baseline-manifest files were hash-checked; the frozen package was not edited.
- `source-identity.json` identifies the exact changed source, tests and guides. `static-lease-reads.patch` applies to the authoritative repository at the stated baseline. `evidence/live-main-verification.json` records the latest read-only main check.

## Change / 修改

Static input/read arcs on resource_lease places now use the original reference-token claim path. They are prerequisites and exact resource references, not consume-return, a physical-resource use permission, or a concurrency/firing-count limit. Ordinary data/control reads and existing variable read/produce/edit contracts remain in place.

静态 lease read 沿原 reference_token_ids/refs；Success 保留准确 token，不复制、不补归还。可与已有 VariableResourceArc 共存，包括同池同 token 去重、同池不同 token 的合法并集。没有第二 marking、reader 表、外部计数器或新调度/权限机制。

The Registry admission check is deliberately narrow: only transitions with this new static-read form receive the additional adopted-PN/checkpoint claim reconstruction and consume-index comparison. The original Module consume-index convention is preserved. Other historical low-level invocation shapes are not advertised as globally audited or repaired.

本包只在含新静态 read 的 transition 上补原 transaction 边界复验。直接 claim 省略 read、伪 logical ID、ref/index 不一致、误标 consumed 和清空原 consume 索引均拒绝。不能把这说成全部低层 claim/index 已全局闭合。

## Contents / 内容

- `static-lease-reads.patch`: minimal repository delta
- `replacement/`: exact changed files, for inspection only
- `source-identity.json`: before/after SHA-256 identities
- `baseline-source-manifest.json`: retained provenance of the tested baseline
- `DESIGN.md`: design and boundary decisions
- `VALIDATION.md`: final verification inventory and limits
- `evidence/`: retained baseline failure, intermediate failures, final test logs/XML and source checks
- `tools/verify_changes.py`: compare an applied checkout with exact candidate hashes
- `independent_review/`: full independent review, unchanged evidence, portable replay scripts and exact original script snapshots
- `PACKAGE_MANIFEST.json`: SHA-256 and byte size of every other packaged file
- English/Chinese user guides under `replacement/docs/guides/`

## Local gate / 本地门槛

1. Verify the current authoritative checkout and its clean/dirty state. Do not overwrite unrelated local changes. If main has advanced, reconcile the changed files and rerun the same focused tests; the old candidate result does not certify a new base.
2. Check the patch against the matching base: `git apply --check static-lease-reads.patch`.
3. Apply in a separate review branch/worktree only after the local task is authorized: `git apply static-lease-reads.patch`.
4. Verify exact changed bytes: `python tools/verify_changes.py --source /path/to/RPNH --manifest source-identity.json`.
5. Run the focused commands in `VALIDATION.md` using the existing supported environment. No dependency installation or external provider/model call is required.
6. Review the source and evidence, then follow the user's local acceptance and publication instructions. This cloud task made no commit, push, PR/submit or Actions change.

## Explicit limits / 明确边界

- No H7 child launcher, origin guard, bootstrap gate, transport target ownership/B2 fix, native client, socket transport or model/provider/API execution.
- The original reset/revision rules remain: lease resets/ordinary retirement are rejected; active replacement drains. After that, a generic static read arc may be removed while preserving the lease through existing mapping. Application-specific origin invariants still need their own later work.
- Read-only cold reconstruction, ordinary Success and completion recovery are covered. No assertion of full repository, native-transport or real-provider acceptance is made.
