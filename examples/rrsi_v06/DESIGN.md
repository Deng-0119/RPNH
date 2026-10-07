# Design and boundaries

English | [中文](DESIGN_ZH.md)

## Reuse of RPNH

`formal_role.py` and `formal_policy.py` declare application-specific modules,
schemas, tools, and transition handlers. They rely on existing RPNH primitives
for registration, registered-host execution, Registry resources, operation
settlement, the owner event loop, and exact PetriNet execution. There is no
application-owned lifecycle database or replacement scheduler.

`formal_campaign.py` assembles the business sequence and starts independent
role and Policy runs. It records only application-level manifests, score rows,
selection decisions, and opaque RPNH references. A completion claim requires
the production runners plus unique child-run and provider-attempt evidence with
the expected transition traces. Test doubles are deliberately marked
`injected_test_runners` and remain incomplete.

## Frozen and user-owned inputs

`protocol.example.json` freezes the timeout fixture, task splits, two-round
method, role limits, exact scorer, public seeded bootstrap, and local selection
parameters. The execution profile remains user-owned and chooses the provider,
model, adapter, request limits, and runtime policy. The application validates
that it received a complete RPNH selection but does not inspect private account
configuration or require one route implementation.

Heldout and export task bodies are never projected into role input. Candidate
source is literal text from the Proposer result, restricted to `policy.py`,
checked before the Critic and smoke evaluation, and then evaluated in separate
Policy child runs.

## Method and execution profile

The example demonstrates the public RRSI role loop, independent evaluations,
evidence flow, and score/cost selection on the current RPNH harness. Selection
follows the public RRSI Algorithm 2 cost rule. The timeout fixture uses seed 7
and 2,000 bootstrap repetitions, with its coefficients, task manifests, source
fixture, gate, and smoke check frozen in `protocol.example.json`.

Runs use `application_petri_conformance/v1` with the B0 recovery profile. Source
identity is recorded through explicit protocol, run, and resource references.

## Execution-control reporting revision

`formal_reporting.py` writes replace-only application snapshots. It is not an
execution ledger authority: started/returned/failed/interrupted child entries
only describe what this coordinator observed, with Registry references when
available. `completed_evaluations` contains only completed evaluations; unfinished
trials are retained in `child_runs` without synthesizing aggregates. The existing
v1 report envelope is retained with `execution_control_version` identifying the
new observation/control behavior. Only explicitly allowlisted transport codes
are public; arbitrary failure codes and exception messages are omitted.

`formal_execution.py` composes the existing `ExecutionServices` callback and
`Harness.request_owner_stop`. A stop is latched once, checked before execution,
at dispatcher preparation, and through a supporting transport's callback. An
already admitted operation follows the core's existing settlement rules; durable
completion wins over a racing stop. The worker pool drains before returning.
This example creates no new interruption routes and makes no promise to kill
an uncooperative legacy port. No new OS signal or CLI cancellation UX is added.

These changes do not identify causes of historical experiments. Effective
profile/source provenance remains more complete in child Registries than in the
top-level report. The frozen `safe_missing_retry_max=1` allowance remains inactive
at application level; zero retries are performed. The existing Digester limit
is 6000 digest characters with the shared execution target's token limit; this
revision does not reinterpret the frozen `max_output_tokens` field or change it.
Those provenance and unit-contract improvements require separate follow-up.
