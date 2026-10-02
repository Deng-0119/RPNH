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

## Method scope

The acceptance target is framework-level: the public RRSI role loop,
independent evaluations, evidence flow, and score/cost selection can be
implemented and used on the current RPNH harness. This establishes RPNH
feasibility for a state-of-the-art RSI framework, not parity with the paper's
reported benchmark performance.

The selection implementation follows the public RRSI Algorithm 2 cost rule and
uses a deterministic bootstrap with seed 7 and 2,000 repetitions for this local
fixture. The local coefficients, task manifests, source fixture, gate, and smoke
check are example choices rather than official paper-domain settings.

This is B0 application conformance. It does not claim strict AgentLoop
conformance, B1 abrupt-loss reconciliation, hostile-code sandboxing, or a paper
result. Source identities are explicit protocol/run/resource values; no
content-derived hash, checksum, or fingerprint is introduced.
