# R1 terminal configuration gap: independent addendum

## Confirmed conclusion

The frozen R1 compiler output does not carry the native terminal reader’s required run classification. This is an authoring-to-native-terminal integration omission in R1, not a need for a second runtime or validation engine. A derived R2 HOST overlay can diagnose the gap, but it must not be presented as proof that the unchanged R1 output reaches the native terminal boundary.

Precise source evidence in frozen `rpnh-rsi-profile-fix/source`:

- `cpn/rpnh/iteration_profile.py:240–245` sets `config: {}` for every stop, final-select and final-retain terminal binding.
- `cpn/rpnh/registry/module_terminal.py:251–253` reads `terminal.config['run_outcome']` and returns `None` unless the value is `complete` or `failed`.
- `cpn/examples/iteration_profile/README.md:49–52` and `README_ZH.md:44–46` explicitly describe empty handler config and rejection of incompatible HOST requirements.
- `PreparedIteration` lists entry and model obligations, but no required terminal-config obligation. The general future runtime gate at README.md:136–144 does not explicitly require callers to rewrite the compiled terminal declaration.

Accordingly, the earlier independent review statement that the static terminal structure is compatible with the native reader must be narrowed: the operation/outcome/carrier topology is compatible, but the emitted configuration is not sufficient for native terminal publication.

## Previous results remain valid within their actual scope

The earlier author 127-test and independent 27-test compiler results are not revoked. They verified inert compilation, frozen input, structural PN dependencies and rejection boundaries. They did not establish native terminal publication. The original frozen package and its source hashes must remain unchanged; a new explicitly described revision should correct the gap and its claims.

## Minimal follow-on correction

Require an explicit profile mapping `terminal_outcomes` with exactly `stop`, `final_select` and `final_retain`, each `complete` or `failed`. Map these into the existing `TerminalBinding.config.run_outcome`; do not invent a new terminal reader, success flag, runtime field or inference rule. The `stop` choice applies at every round, including the final round. Missing or unknown fields must fail closed, and the existing Registration/compiler must still validate the selected terminal tool’s config schema.

This is a breaking author-input correction: the old profile lacks required data. If the still-unpublished candidate schema ID is retained, the new package revision must state that old profiles are rejected and need the three explicit classifications; it must not claim unchanged wire compatibility or silently rewrite old source material.

## Business meaning must remain explicit

For the R2 synthetic example, its author confirmed:

- `stop` means a registered request for normal early termination after processing the current round, distinct from owner interruption.
- Domain evaluation `unknown` means retain the already valid incumbent without promoting the candidate. This can end a valid process with framework `complete`.
- Final select/retain means the bounded process has finished, not that optimization improved.
- Score/protocol exceptions do not publish selector success or terminal evidence.

Thus that example may explicitly select three `complete` mappings. The generic template must not infer those classifications. Physical execution `outcome_unknown`, interruption, provisional output or an unsettled firing is a different condition; a declared `complete` mapping cannot replace actual settlement or authorize success.

## Negative evidence obtained so far

`test_terminal_gap.py`: 6 passed in 1.98 seconds.

1. Frozen R1 emits empty config for all four terminals of the two-round example and reports no terminal-config obligation.
2. A caller Registration whose terminal schema requires native `run_outcome` causes the original profile to fail native compilation with `Invalid registered tool config`.
3. The real `_terminal_material_for_binding` function is exercised with inert prestate seams. All four original bindings return `None` at the outcome gate; each of `complete` and `failed` advances past that gate. No Registry was created or run for these unit negatives.

Real Registry positive/negative evidence and the successor patch are reviewed separately when available. The negative unit test alone is not native Registry execution evidence.

## Actual historical Registry negative, independently inspected

The R2 worker’s `step-first.xml` confirms `test_unbound_r1_has_no_runtime_terminal_and_overlay_does_not_mutate_it` passed. Its original three native steps settle proposer/evaluator/selector, yet `RunOwner.terminal()` remains `None` and the original read API reports `running`. The reviewed runtime copy retains the exact frozen R1 compiler hash `87bae6a32c96c5902dec06dd69ce2da70df5dd76520c2f425a9fbf62772ead2f`.

That worker’s first batch as a whole was 6 passed / 4 failed / 4 deselected. The four failures were an incorrect `VerifiedResourceArtifact.payload` test helper; this batch is not overall R2 acceptance. The specific original-R1 negative passed independently of that helper. The worker’s newer complete application tests are outside this addendum.

## Successor patch reviewed

The successor is `rpnh-rsi-profile-terminal-revision`, delivery revision 2, based on the recorded main `715468dab0b1bea07d7e94a7aa0606eaf194365c`. It adds only the same six profile/example/doc/test files to that main; it does not modify a native Registry/compiler implementation. All six source SHA-256 values match its frozen `file-manifest.json`; all six old source hashes remain unchanged.

The implementation now requires exactly the explicit mapping recommended above. It changes only the native terminal config values in the compiled topology, while using the original Module compiler and native reader. Terminal tests use a separate caller-owned schema that requires `run_outcome`; executor config remains closed and empty. The two language docs distinguish domain evaluation UNKNOWN from unresolved physical execution, make old-profile rejection explicit, describe delivery revision versus still-unpublished schema ID, and do not claim wire compatibility or full runtime campaign validation.

### Independent successor compiler checks

`successor-independent.xml`: 33 passed in 27.49 seconds.

- All eight combinations of three `complete`/`failed` classifications, at 1, 2 and 32 rounds, compile and round-trip through the original compiled-net reader.
- Every final-round stop uses the `stop` mapping, not the final-select/retain mapping.
- Missing, partial, extra or invalid classifications fail closed.
- Profile input mutation after preparation does not alter the prepared source.
- Independently generated frozen-original Modules at 1, 2 and 3 rounds are byte-canonically equivalent to the successor Modules after removing only the new terminal config values. This checks that the successor did not alter resource ports, arcs, outcomes, budget bindings or round topology.

### Independent real Registry checks

`registry-independent.xml`: 2 passed in 57.20 seconds.

1. Two rounds: a first-round retain remains nonterminal; the second/final round’s stop follows an explicit `failed` mapping. Original read-only Registry reconstruction sees exactly one failed terminal, six operation results, exact final bytes, zero model calls, stable read cut and idempotent terminal publication. All business callbacks are fail-on-execution test registrations; the test uses original owner step APIs to supply generic schema-valid products.
2. A last-round selector has only provisional products, without settlement. Even with all mappings explicitly `complete`, the native terminal call creates no evidence and read-only reconstruction remains `running`.

`registry-owner-stop-independent.xml`: 1 passed / 2 deselected in 18.28 seconds.

3. After the final selector product has actually settled, an owner stop is recorded before terminal publication. A later terminal request raises the original native `ResourceIntegrityFault` for conflicting run authority. Status remains `stopped_by_owner`, no terminal evidence appears and no new Registry event is appended by the rejected request. This is stronger than stopping an untouched run and directly checks that the mapping cannot overwrite the owner’s stop.

All three reviewer-run Registry tests block sockets/socketpair and subprocess creation. They use temporary local SQLite Registries, no event loop or scheduler, no provider, no model, and no external publication.

### Author terminal evidence independently inspected

`terminal-first.xml`: 16 passed, comprising seven compiler checks and nine real Registry checks. The Registry subset covers all three mappings with each of `complete` and `failed`, historical empty-config control material, provisional products and an initial owner stop. The author’s final `final-tests.xml` and log were independently inspected: 150 passed, zero errors/failures/skips, in 174.42 seconds. `py_compile` also passed. These are author-run aggregate results, not an additional reviewer-run aggregate.

## Disposition and remaining boundary

The R1 terminal-configuration defect is corrected in delivery revision 2. Do not integrate the superseded original patch. The original inert-test claims remain historical compiler evidence; this addendum corrects the broader native-terminal compatibility wording.

These results establish the narrow native terminal seam, including actual settlement and owner-authority negatives. They do not establish a provider’s persisted physical `outcome_unknown`, interruption/recovery races, native transport, full RSI/RRSI domain correctness, train/test isolation, model/resource permission resolution or parent-child execution. Domain evaluation UNKNOWN may legitimately produce a completed retain decision under its domain contract; it is not the same as any unresolved physical execution state.
