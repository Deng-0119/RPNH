# H7 bound-origin lowering and declaration-subset independent review

Date: 2026-10-09 UTC. Reviewer: independent `review_bound_origin_lowering` task. Product source was copied after the author froze it. No author source file was edited by this review.

## Decision

**ACCEPT_LIMITED_OFFLINE_CANDIDATE**, limited to the mechanical origin lowering and enumerable declaration-freezing slice described below. No unresolved product blocker was found in that scope. This is not acceptance of complete H7 D0, the two production payload/composition paths, complete installed execution materials, or any native D1 behavior.

The candidate keeps the original Module/compiler/composition/wire reader and original Registry/PN authority. There is no additional scheduler, permission table, readiness authority, worker runner, or native evidence issuer. The new declaration DTO is deliberately insufficient for execution.

## Frozen identity

- Repository lineage: `Deng-0119/RPNH`; frozen input only, no current remote HEAD assertion.
- Input: 994 source files, aggregate SHA-256 `a3af1d85ffbc8acac72e719fed316ed3c848229fce92026c62e571cb77a913a2`.
- Reviewed output: 999 source files, aggregate SHA-256 `7867bec81c0830f19cfd6f8a58b0d1598d5efc8220c69e6cfdd02604f98f1a0e`.
- Final author patch: SHA-256 `2bc8dd99115f2d139f6b17dd8cd1342087a02110233b8d43c5c37c5199a9cc98`.
- The reviewer independently recomputed the input and output manifests, applied the final patch to a separate copy of the exact input using `git apply`, and obtained the identical 999-file output manifest. The earlier patch packaging correction did not change product source.
- File counts and aggregate hashes exclude `__pycache__` and `.pytest_cache`, following the frozen input manifest contract.
- The overlay changes five product paths: adds `bound_child_lowering.py` and `bound_child_declarations.py`, and changes original `compiler.py`, `composition.py`, and `executable_net.py`. Three added test/fixture paths account for the remaining changes. Inherited Registry guards are byte-identical to the input.

## Findings and resolutions

### R1: ordinary Python declaration compatibility regression, closed before freeze

The pre-freeze implementation called `startswith` on raw `designer_constraints` keys. Ordinary directly constructed Modules previously normalized JSON-compatible numeric keys through `ModuleDeclaration.to_dict()`. The added probe therefore caused `AttributeError` on an already covered legacy case.

The reviewer reported this before freeze. The author preserved a failing reproduction and changed the probe to use the original normalized Module view. The frozen source passes `tests/test_typed_author_interoperation.py::test_legacy_direct_python_numeric_constraint_keys_still_normalize` as part of the reviewer's 516-case compiler/ControlIR/serializer regression. This issue is closed in the reviewed bytes.

### No other in-scope product blocker found

- Lowering adds one internal capacity-one reusable resource-lease place, one identity and pool, and exactly one weight-one static read on every transition. It adds no literal token and grants no admission by itself.
- Independent exact-projection tests subtract only that addition from simple Modules, effect-bearing/ref-shaped business JSON and the single-agent builder output. The remaining symbolic business net, operation ABI, fragments, ports, handles, terminal contract, variable-resource structures and other fields agree with the original compilation.
- The original wire reader recomposes through the same function. Independent mutations of origin output/reset/pool/literal token/source flag/fragment, plus author mutations of missing/read-to-consume/duplicate arcs, aliases and schema, are rejected. Reapplying the helper is idempotent; reapplying the mechanical transform to already transformed structure is rejected.
- A qualified business lease named `step.rpnhOrigin` can coexist with mechanical `rpnhOrigin`; actual Registry execution proves they have different exact resource/token identities, both claims are required, and success preserves the protected origin. Business capability-schema collisions are rejected.
- Original Registry admission rejects missing, duplicate, consumed, and same-version/different-logical origin refs. Raw token clones and resource/lease/epoch substitutions fail without committing. Source/catalog integrity damage is rejected, including a same-schema foreign catalog ref and changed installed schema body.
- Real temporary Registry tests cover same-transition concurrent firings, fanout siblings, Start/products/Success, active-claim reconstruction after one sibling completes, and read-only cold hydration. The protected token remains exactly the original token/resource/lease; cold reads do not advance writer epoch or event head.
- The independent structural-revision probe follows the ordinary registered effect callback, products and Success path using an exact declared bound candidate and the original protected resource binding. It reaches the original adoption gate and receives `H7 bound child cannot adopt a successor net`. The initial net, origin and single adoption remain unchanged; no firing settlement is committed. The ordinary unbound structural-revision regression passes separately.
- Owner edit/direct adoption also preserve the original adopted net/checkpoint/origin. The owner command path can retain candidate/command audit publications before rejection; this is not permission to adopt and is not a zero-all-events contract.
- The protected schema body frozen by compilation equals the installed mechanical schema. Public registration cannot override it. Bound config schema references fail offline without URL retrieval. Ordinary compiler and ControlIR behavior remains covered by the original regression suite.

## Declaration-freezing scope

`FrozenBoundChildDeclarations` contains immutable, uniquely named, sorted payload bytes and detached digests. It freezes the actual compiler output, referenced Registration/schema declarations and budgets. Ref-shaped JSON and ordered business arrays remain inert data, not Registry references or readiness authority. Registration mutation during lowering is detected; trusted component lowerers retain the original HOST trust boundary and are not claimed to be sandboxed.

The AgentTask helper uses the existing serializer at the already derived final document root and checks exact target/socket paths before serialization. Tests verify stable bytes across current-working-directory changes, preservation of legal sibling-relative paths, immutable target context, changed-parent/path rejection, and no creation of the target/control directories. This is a declaration/path serialization check, not authentication of the supplied parent/root or a filesystem reservation proof.

The result is not `PreparedChildMaterials`. Both normally produced and handcrafted declaration DTOs are rejected by `register_child_intent`; `require_execution_materials()` always reports unsupported. The helper does not read the referenced private adapter/profile file, and changing that file need not change this declaration digest. That deliberate incompleteness is correctly documented.

## Verification

**Final total: 768 unique PASS, 0 FAIL, 0 SKIP.** This consists of 46 independent new cases, 721 cases shared with the author’s final suites, and one additional original ordinary-revision regression. Author and reviewer repeats are not added together. Individual testcase identities are in `TEST_COUNTS.json`; final source/patch/evidence hashes are in `REVIEW_IDENTITY.json`. Results are counted by distinct testcase identity, not by adding early retries.

Final accepted runs:

- Independent lowering/declaration/Registry probes: 44 PASS.
- Independent protected-catalog damage probes: 2 PASS.
- Author's new source tests rerun independently: 28 PASS.
- Original compiler/ControlIR/worker serializer regression: 516 PASS.
- Original ordinary structural revision: 1 PASS.
- Original parent-child core, acceptance history and static-lease regressions: 177 PASS, 1 native subprocess case deliberately deselected.

All these runs use the existing Python 3.12.14 / pytest 8.4.2 interpreter and the review's import-time offline sentinel blocking socket/URL/subprocess/fork/exec/PTY paths. Completed runs report zero forbidden attempts. No model/business endpoint, installation, Actions, remote push, native worker, real receipt, peer socket or physical target reservation was executed.

### Explicit omissions and retained early failures

- The native new-process static-lease cold test is deliberately deselected because subprocess execution is outside this D0 review. In-process cold read/claim reconstruction is covered; it is not a new-process/native proof.
- Six additionally selected `test_native_net_operations.py` cases could not be collected because the exact frozen input lacks `examples/net_operations/live_agent_replacement.py`, imported at module load. This is recorded as NOT_RUN, not PASS and not a new candidate regression. The missing file was not sourced externally. The ordinary structural-revision test was rerun separately and passed.
- Early independent failures were test-construction/assertion issues: an overly strict zero-event owner-edit assertion, a multistage AgentTask rejected earlier by the original Spec constructor, and revision probe setup that initially failed to preserve the exact declared candidate/resource binding. Their logs and test snapshots remain in evidence. The corrected revision probe reaches the actual successor-adoption prohibition. Those early runs are not added to final unique counts.
- The full repository suite was not run. No current remote main, native recovery, long-running stress or crash-persistence behavior is asserted.

## Remaining unsupported work

1. Complete installed HOST implementation and public configuration dependency inventory, plus opaque secret-binding identity on the original Registration/Registry boundary. Arbitrary HostProfile callables and private adapter selection are not made enumerable by this slice.
2. Complete tagged AgentTask and Module execution-material preparation, actual installed selection, and the production `D -> I -> E` material closure.
3. Sealed transport installation, receipt/peer/PID validation and issuance, one-time dispatch, physical exclusive target reservation, and both original worker compositions.
4. Native protected bootstrap/origin issuer, exact child binding, same-cut terminal observation, parent completion/Success, and cross-process D1 windows.
5. H7b recovery, H8 and consumer migration.

ControlIR plus bound-origin proof composition, recursive native children, workflow graphs/plugins/managed AgentTask variants and extra execution profiles remain explicitly unsupported where the author scope says so. These are not converted into empty HOST configurations or alternate policy channels. The next public-material/opaque-secret-binding proposal is directionally consistent with the original authority design, but remains a proposal, not validated code.

**Final scope statement:** the reviewed bytes are an accepted narrow offline lowering/declaration candidate; complete H7 implementation and native execution remain unverified and unavailable.
