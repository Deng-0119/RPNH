# Static resource-lease read references / 静态 lease read 引用

## Scope / 范围

Authoritative source: Deng-0119/RPNH main was read through the GitHub connector at 2026-10-08 21:23 UTC and resolved to `1f191645c4d60c8b190d42e9fad99c85e8981c03` (product code baseline d92ff37). The frozen seam source is copied, never edited. This patch is a generic PN/Registry capability; it does not implement H7, child launching, bootstrap authority, transport ownership, a scheduler, or a new permission mechanism.

权威基线为以上 main。冻结 seam 包不修改。这里只补原 PN/Registry 的通用静态引用能力，不代表 H7 或 child origin 闭环已完成。

## Reproduced failure / 已复现失败

`tests/test_static_lease_reads.py` constructs a real RunOwner/Core with a declared exact resource lease pool, static read arc, normal input/output and budget. Compilation and M0 succeed. Existing `owner.admit` fails at `checks.py` with `resource lease has no exact variable consume-return inscription`. Evidence is retained in `evidence/baseline-failure.txt`; an earlier collection error caused by an incomplete optional example dependency is separately retained.

## Contract / 契约

1. The adopted compiled PN is the only classification source. Input arcs with `mode=read` and place `token_kind=resource_lease` supply exact non-consuming references. Other read arcs keep their existing local consume-return and Registry admission behavior.
2. Reuse `_PendingClaim`/`_ActiveClaim.reference_token_ids`, exact token refs, `lease_accesses`, and Registry's existing claim/consume delta. No schema, parallel marking, reader table, external counter or new RW policy is introduced.
3. Selection and exact reconstruction require the declared static read weights, current exact token identity, epoch/freshness, same net and consumer. A static and variable read of the same occurrence share one ref (union, no extra capacity requirement). Static read plus a variable consuming occurrence must use distinct tokens; conflicts reuse existing claim exclusion.
4. Pure-reference transitions are enabled by their declared references and yield one finite occurrence per enumeration. Ordinary input occurrences can coexist in parallel while sharing a reference.
5. A reference is never stamped consumed, returned, cloned or minted at settlement. All predecessor bytes and refs survive; sibling references reconstruct on the latest checkpoint. Preflight and effect validation derive the same classification.
6. Add a narrow check to the existing Registry admission transaction boundary for transitions with this new static-read form: reconstruct against the adopted PN/checkpoint and reject omitted, extra, foreign or mode-mismatched references. This does not change admission on declarations without this form.
7. Existing resource-byte reads retain their exact claimed-input provenance checks; this patch adds no physical-resource usage or edit authority. Dynamic resource access still requires its existing variable arc. Lease reset/retirement remains forbidden by existing validators; owner replacement drains active work and operation revision requires its existing exclusive active claim.

以上规则均沿原字段与验证边界。静态引用和 variable read 同 token 去重；consume 与引用冲突沿原规则；不限制 Module 不能已有 variable arc。引用的 token 结算后准确保留，不归还/复制。动态改网、reset 和字节读取沿既有规则，不增加 origin 不可删除或新的权限策略。

## Planned validation / 验证计划

Real Registry admission → Start → products → Success; concurrent same-transition and distinct transitions sharing a ref; exact retention after each sibling settlement; genuine cold Core reopen/claim reconstruction; static plus variable read and consume-return; direct malformed/missing/foreign/mode-mismatched claims; reference-only and weighted arcs; ordinary data/control reads; preflight and declared effects; reset/replacement existing rules. All execution is offline using the supplied existing venv. No socket, PTY escalation, native client, model/provider call, installation, Actions or remote write is allowed. Validation results will distinguish PASS, FAIL and NOT RUN.

## Exact-selection refinement / 准确选择修订

A real Registry regression found an existing helper mismatch: two predecessor Successes can publish a first unsatisfiable variable carrier and a later satisfiable carrier. The default selector remains disabled on the first; `_try_reserve(allowed_token_ids=exact_selection)` proves the later exact occurrence, but `claim_exact_registered_firing` used to reject it by first checking unrestricted default enabledness. The static admission guard exposed that old mismatch. The exact helper now passes the same allowed token IDs through the existing enabledness/reservation path, retaining count/verdict/timed checks. Ordinary/default scheduling is unchanged; no claim of fixing scheduler search or global liveness is made. The real pre-fix failure and post-fix full Registry success are retained separately.

真实前序 Success 产生首个不可满足、后一个可满足的 carrier，证明这个 exact helper 的限制不只是合成对象。现在仅 exact 路径把同一准确 refs 对应的 token IDs 传入原 enabledness/reservation；原 count/verdict/timed guard 保留，默认 owner 调度策略不变。未声称修复默认调度搜索或活性。
