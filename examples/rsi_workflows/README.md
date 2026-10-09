[English](README.md) | [中文](README_ZH.md)

# Single-Registry synthetic iteration (R2)

This finite example supplies pure proposer, evaluator and selector HOSTs to the
existing iteration-profile compiler. It adds no runner, scheduler, provider,
budget counter, Registry type or mutable winner store. It requires the R1
terminal-outcome correction with the explicit `terminal_outcomes` profile field.
The earlier frozen R1 profile did not produce usable native terminal bindings.

## Condition and provenance

The synthetic candidate is an integer, proposed as incumbent + 1. Evaluation
computes candidate and incumbent absolute distance from the same registered
request's integer target. Lower is better; ties and worse scores retain the
incumbent. A domain-level `unknown` evaluation has null scores and cannot promote
the candidate. This toy condition does not alter RRSI v0.6, its scorer, limits,
heldout handling or research protocol, and proves no model improvement.

- The proposer receives only its claimed state, never the request body.
- Candidate products name the exact input state resource.
- Evaluation names the exact candidate, request (including synthetic dataset),
  parent state and evaluator operation binding, whose registered identity fixes
  the synthetic scoring implementation.
- Selection consumes this round's incumbent, candidate and evaluation through
  the original PN. Its state product names all three exact input resources and
  preserves the request and scorer binding. Content checks reject inconsistent
  round, candidate or parent-state pairings after native admission.
- Every subsequent proposer consumes the previous selector's registered `next`.
  All iteration bounds and readiness come from R1's finite Module, never a Python
  controller. The test's one-step helper requests an explicitly named firing;
  only `RunOwner.admit` can authorize it.

These are trusted pure HOSTs, not a sandbox for malicious Python scorers.
Registry origin/claim provenance and application content checks have different
responsibilities. The tests establish this example's exact data flow, not general
training/test leakage isolation, malicious-HOST resistance or arbitrary scoring.

## Completion is explicit

The profile maps `stop`, `final_select` and `final_retain` explicitly to native
`complete`. Here `stop` means a normal early protocol conclusion after this
round's evaluation, not user interruption. Final select/retain mean the bounded
process concluded, not that the candidate improved. A separate regression maps
final retain to `failed` and verifies the native reader preserves that outcome.
The example does not patch terminal config after profile compilation.

Domain `unknown` is distinct from physical outcome uncertainty or an unsettled
firing. Missing evaluation, mere publication without settlement, wrong pairing
and role exceptions cannot produce a successful selection. Provider submission
uncertainty, owner-stop/resume and transport loss remain separate native gates.

The native model-call cap is 1 regardless of rounds. All HOSTs are deterministic,
so native returned-model-call counts remain zero even when 12 operations execute.
This retains the existing `actual_returned_calls` accounting meaning; it is not a
limit on all dispatches or all physical attempts. No model binding is resolved.

## Tests and execution boundary

From the repository root in its already prepared Python test environment:

```bash
python -m pytest -q examples/rsi_workflows/tests/test_runtime.py -k 'not original_orchestrator'
python -m pytest -q examples/rsi_workflows/tests/test_runtime.py -k original_orchestrator
```

The first set is socket-free D0 using original
`start_run → RunOwner.admit/start/products/succeed → terminal`. It blocks socket
and subprocess APIs. Results are reconstructed by the original read-only
`read_run_execution/read_run_terminal_bytes`, with a final unchanged-cut check.
Forged/deleted report files cannot change enablement or Registry state.

The second set defaults to actual `OwnerEventLoop` AF_UNIX (D1) and the existing
`Orchestrator.run → Harness.exact_execute`. HOST submission uses an immediate
Future. If the environment disallows AF_UNIX, preserve the failure; do not change
permissions or report a native pass. An explicitly labelled, test-only D0
transport-double run is available:

```bash
python -m pytest -q examples/rsi_workflows/tests/test_runtime.py --rsi-transport=pipe
```

That reuses the unchanged existing
`examples/tool_pipeline/tests/pipe_transport.py`; it replaces only the wake
transport and is never an automatic production fallback. It is not socket,
multi-process, external-provider, registered-model-port or R3 acceptance.

No CLI, author-revision-to-run shortcut, automatic branch promotion, child
orchestration, retry, training or publishing is introduced. Pure Registry tests
do not establish the still-separate author/CAS or complete R2/R3 roadmap gates.
