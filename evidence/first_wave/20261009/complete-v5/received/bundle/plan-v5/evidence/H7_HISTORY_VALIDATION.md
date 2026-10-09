# Validation: narrow read-only historical acceptance slice

Final source: 994 files, aggregate `a3af1d85ffbc8acac72e719fed316ed3c848229fce92026c62e571cb77a913a2`.
Overlay over the frozen H7 core: `b8ad5b5c18346cf9fa71cd17c96d17deb67ecbb336329d83c78c271f521bedfa`.
The input remains the unchanged 992-file H7 core, aggregate `ac68327e442b7bda8a6aa2ba93c0cd20ad72181a661b0497627b08713334907e`.

## Final evidence

Final frozen-source result: **298 distinct parameterized cases PASS**, 0 failure/error/skip: 247 executions by the implementer and 51 by the independent history reviewer. The implementer groups were 139 author (108 history + 31 core), 68 adjacent, and 40 original core-review cases. All three exited 0 with unchanged source; the independent 51 exited 0 with matching before/after source and a narrow-D0 approval. These are distinct test IDs, not 298 distinct semantic capabilities.

The final results are recorded in `evidence/FINAL_VALIDATION_INVENTORY.json`, `evidence/final2-*.{log,xml}`, their real-exit result files and before/after source manifests, and `review/REVIEW.md`. No historical run, repeated smoke test or old-source pass contributes to the final count. Source identity excludes Python/pytest caches; they are not packaged.

The final selections consist of 108 history tests, the full previously selected 139 original core cases (31 author, 68 adjacent and 40 original independent boundary tests), and 51 newly independent history cases. The independent count is by distinct parameterized test IDs, not a claim to 51 distinct semantic defects or a substitute for full-system coverage.

The three adjacent tests that explicitly spawn another Python process remain deselected. In the core-review group, 31 duplicate author cases are deliberately deselected because the author group runs those same cases. See the inventory for exact IDs.

## What is established

- Real original temporary Registries, original immutable object bytes, transaction/outbox/stream and task-control structures, complete temporary membership, exact strong causal edges and native Start facts are checked.
- One read-only original SQLite snapshot supplies the actual source identity and each original committed cut. The internal SELECT-only CTE projection is ephemeral and writes no ledger, marker, table, view or marking.
- Historical epoch is rechecked against original phase events, transaction/outbox facts, initial native writer and execution lease. It is not today's fence and grants no action.
- Mechanical authority remains in the shared original parent/child matcher and original Start/Registry/PN checks. The live writer separately requires opaque native-boundary evidence. No history proof is consumed by live operations.
- Every inspected transaction's retained-member inventory is checked in both directions. Original fixed H7 and Start event envelopes are checked rather than reconstructed from just valid payloads. An unsupported PUBLISHED label cannot mask corrupt original evidence. Broken SQLite sources return a finite fail-closed classification.
- The integrity walk follows schema-declared exact reference properties, arrays and maps and the existing resource origin/provenance authority contracts. Legal reference-shaped business values in request definition/configuration remain unchanged and are not treated as dependencies. Both shapes, direct/nested list/nested map containers, and real authority-reference negative controls are tested.
- Real API positive changes cover accepted PROVISIONAL history after durable owner stop, new writer acquisition and later provisional products. Classification does not mutate canonical tables and does not expose provisional bodies or make them canonical.

## Preserved earlier outcomes

All small original results remain in `evidence/` and `review/evidence/`; they are excluded from final-source statistics.

- `first` and the first diagnostic run rejected a valid baseline acceptance due to a relation-publication comparison mismatch. The original failure and follow-up diagnostic are retained.
- `suite1` had 86 PASS and 1 FAIL. The failing terminal-ready construction lacked the original required exact activation reference. It never produced a closed H7 producer. The replacement test explicitly verifies the rejection, no terminal-ready fact, no table mutation and still-valid historical acceptance. It does not claim producer-close coverage or change core completion semantics.
- Original independent round1 found physical-head, earlier-phase epoch and fixed event-envelope integrity omissions. Round1-extra also found missing original Start membership. SQL mutations are corruption probes, not authorized normal-API actions.
- Some initial independent PUBLISHED and foreign-root probes failed during injection due to SQLite CHECK/FK constraints. Those raw errors are preserved and are not themselves product-classification results. Corrected explicit corruption injection was separately rerun against the old source: the PUBLISHED cases demonstrated early UNAVAILABLE masking, while the foreign-root case already failed closed. The foreign original source returning UNAVAILABLE is also fail-closed, not a permission bypass. The review report carries the exact per-run distinctions.
- The old `d009afc8…` source passed 110 author, 68 adjacent and 40 original core-review cases, but new round2 found orphan retained members, original Start fixed-envelope omissions, an uncaught broken-SQLite exception, and two actual-API false rejections of legal request data. These drove the final hardening; those old-source passes are not reused as final evidence.
- A separate exploratory operation-configuration fixture initially violated its existing declared configuration schema. Its failure is retained as fixture invalidity. The corrected legal configuration positive passed even on the old source, so it is a regression control rather than an additional discovered defect.
- An initial static overlay reconstruction failed because the generated patch omitted new-file mode headers. The raw failure record is retained. The corrected final patch passes local `git apply --check`, applies to an exact disposable core copy and reconstructs the final source manifest byte-for-byte. This is a packaging correction, not a product test result.

## Exclusions and limits

This is selected deterministic D0 evidence. It is not full-repository PASS, H7 full D0, native D1, H7b, current remote compatibility, a merge/push, or an installed-host integration claim. Test-only native evidence is injected explicitly after real temporary Registry setup; it proves no kernel peer, target reservation, wrapper composition, receipt delivery or actual worker creation.

Producer closure, later execution generation, H7 publication/abandon and native parent terminal completion remain unsupported or unreachable for the unresolved native H7 claim. Generic Success/recovery was not opened to manufacture coverage. Copied JSON, booleans and historical proofs never become fresh permission, a receipt or native evidence. Whole-run uniqueness remains enforced beyond the historical cut.

The trust boundary is the original Registry plus trusted installed code, not a cryptographic external ledger. Internally consistent wholesale replacement of that trusted store and absent external installed payload files are not authenticated by this slice. Resource/request bytes and their recorded schema authority are checked; the frozen preparer still records supplied public material inventory rather than implementing a full installed HOST collector.

SQLite mode=ro/query_only prevents canonical database/event writes but may create SHM/WAL coordination files. No zero-directory-write claim is made. The test launchers block sockets, URL requests, process/exec and PTY calls before product imports. No native worker, provider/model/API call, installation, Actions, upload, remote push or consumer migration was performed. Frozen source/design packages remain unchanged.
