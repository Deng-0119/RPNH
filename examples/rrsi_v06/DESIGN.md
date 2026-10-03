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
