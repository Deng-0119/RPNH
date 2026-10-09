# RPNH historical acceptance validator: offline candidate

This isolated overlay adds a source-qualified, read-only H7 mechanical acceptance classifier to the frozen 992-file parent/child core. It does not change the frozen product/design packages or assert the current remote HEAD. See `SOURCE_IDENTITY.json` and `VALIDATION.md` for the final source and execution evidence.

## What the result means

`EventStore.classify_child_acceptance_history(assertion)` reads the original Registry with a read-only connection and one SQLite snapshot. The exact typed assertion names the original source binding, run, task, acceptance transaction/event/commit cut and copied-material digests. The classifier independently reads original registered immutable bytes, events, outbox, producer, temporary membership, causal edges, native Start and execution authority at each original phase cut.

A VALID result establishes only the recorded mechanical acceptance at that cut. It contains finite identities and digests. It does not expose request bodies or provisional product contents, issue a receipt, authenticate an OS peer, grant a fresh action, reserve a target, or supply native execution evidence. No live command, bootstrap, recovery, scheduler or completion path consumes it.

- VALID: original evidence matches the exact historical assertion and this supported slice.
- INVALID: the supplied assertion or inspected original evidence is inconsistent.
- UNAVAILABLE: the original source/evidence is unavailable or the lifecycle needs an unsupported contract. It fails closed and supplies no proof.

The original Registry and trusted installed implementation remain the trust boundary. This is not a signed external ledger and cannot detect a wholesale internally consistent replacement of the original trusted store.

## Supported and unproven lifecycle

Real temporary Registry API tests cover accepted PROVISIONAL history after a durable owner stop, a new writer fence, and later provisional products. The old live execution still loses current permission where appropriate. The history result never makes provisional data canonical.

Producer closure, later execution generation, H7 root publication/abandon, native child binding and native parent terminal completion remain unsupported or unreachable in this frozen slice. A generic terminal-ready attempt is preserved as rejected for its missing exact activation reference; it is not relabeled as a successful close. The unresolved H7 claim is never released just to make a test pass. A damaged PUBLISHED label is invalid evidence, not a way to skip validation.

## Apply in the existing dependency order

Use the exact frozen H7 core first, then apply `acceptance-history.patch` once. The package is self-contained for that step: `inputs/H7-core-source/` is the exact 992-file input. Do not apply this overlay to S1 alone, reapply the H7 core patch over this input, or overwrite another frontend candidate. Exact reconstruction is verified in `evidence/patch-application.json`.

Final evidence is 108 new history cases + 139 original core regression cases + 51 independent history cases = 298 distinct passing test IDs. This does not mean 298 new features or full H7 completion.

## Remaining work

Actual native peer/receipt/target-reservation/worker integration still needs its separately authorized native environment and missing issuers. Other offline implementation and review, including installed-material contracts and lowering work, can continue in the cloud within its own authorized scope. This package neither implements those remaining adapters nor shifts all remaining work to a local machine.

## Package map

- `source/`: exact standalone candidate snapshot, without environment or cache files
- `acceptance-history.patch`: narrow overlay over `inputs/H7-core-source/`
- `inputs/`: exact frozen core source/identities, frozen v3 design, and original core-review tests
- `evidence/`: author runs, source identities, failures and reproduction checks
- `review/`: independent review, tests and preserved old/final results
- `tools/`: identity checking and strictly offline test launchers

Use `REPRODUCE.md`. The package intentionally contains no interpreter, dependencies, temporary Registry databases, native run output, credentials or provider transcripts. Nothing was installed, pushed, uploaded or run natively for this work.

## 中文边界摘要

这是原 Registry 历史事实的只读核验切片。VALID 只证明原 acceptance 在原 cut 的机械闭合，不生成新许可、receipt、native evidence，也不公开临时正文。实际正向覆盖 accepted → durable stop / new writer / provisional products；producer 真正关闭、later generation、publish/abandon 与 native 端到端仍未覆盖。SQL 损坏负测只检验完整性，不代表正常 API 可以绕过许可。旧失败和不可达构造原样保留，最终统计只使用最终冻结源码的完整运行。
