---
name: rpnh-pn-validation
description: "Use finite, input-bound Petri-net analysis at an explicit owner policy boundary."
metadata:
  document-kind: reference
  audience: developer
  language: en
  counterpart: pn-validation_ZH.md
  revision: "2026-10-09.1"
  status: experimental
---

[English](pn-validation.md) | [中文](pn-validation_ZH.md)

# Finite Petri-net validation

`cpn.rpnh.pn_validation` analyzes a fixed compiled declaration and an exact
marking with explicitly modeled operation outcomes. Analysis uses production
marking reservation and deposit functions without calling an executor, tool,
provider or model. A report describes its input, assumptions and finite scope;
it grants no Registry execution, settlement or terminal authority.

Each property returns `HOLDS`, `VIOLATED`, `UNKNOWN` or `NOT_APPLICABLE`.
Enabledness, safety, proper completion, possible successful completion,
allowed completion from every reachable state, dead transitions and inevitable
completion without fairness are separate conclusions. An allowed failure
terminal is distinct from success. A terminal marker cannot hide unfinished
work, active claims or unreleased leases. Persistent data and released resource
states must be declared in the terminal contract.

## Supported analysis

The initial subset supports weighted consume arcs, ordinary read and return,
static lease read/borrow and return, finite boolean/string colours, reader/all
count guards, finite modeled outcomes and simultaneous occurrences of a
transition. Boolean `True`, string `"true"` and string `"True"` remain distinct.
Ordinary reads claim and return a new token occurrence; static lease reads
retain the exact reference. Each outcome selects its actual output arcs.

Models describe contentless control outputs. HOST
fidelity to the finite model is an explicit assumption. Unmodeled data products,
variable leases, logical resource updates, selected/reset effects, dynamic nets,
external reply/timeout behavior and fairness are currently `UNKNOWN`. Local
agent/task progress obligations are also unmodeled and return `UNKNOWN`.
The scheduler contract is explicitly `any-exact-binding`; it does not certify
arbitrary scheduling callbacks or the production default selector's ordering.

State identity includes exact token/firing refs, colours, consumers, resources,
claims, attempts and allocation counters. It never merges equal place counts.
Retry can continually create new identities and reach an exploration cutoff;
that is `UNKNOWN`, not an asserted cycle. State, edge, depth, binding and time
limits preserve the unexpanded frontier. Global `HOLDS` conclusions require a
complete supported graph. Valid finite success paths or counterexamples can
establish their specific existential or negative conclusion before cutoff.

## Explicit owner policy

Existing runs use compatibility behavior: absent finite models are unverified
(`UNKNOWN`). An explicit `ValidationConfiguration` passed as `pn_validation`
to `start_run` records immutable operation/environment/scheduler/terminal
contracts and policy after real owner inputs and resource bindings exist,
before the first execution admission.

Advisory policy records conclusions and retains the existing Registry gates.
Strict policy requires every named mandatory property to be `HOLDS`; missing
models, cutoffs and unmodeled required properties block. The registered policy
persists across reopen and cannot be omitted from a later owner adoption.

For a declared contentless control step named `step.run`, this function builds
a finite policy. Supply a matching compiled Module and real owner inputs to
`start_run`; it is not a standalone workflow or an executed example.

```python
from cpn.rpnh.pn_validation import (
    AnalysisPolicy, OperationCase, OperationModel, ProducedSpec, TerminalContract,
)
from cpn.rpnh.pn_validation.runtime_gate import ValidationConfiguration


def control_step_validation():
    return ValidationConfiguration(
        operation_models=(OperationModel(
            "step.run", "v1",
            (OperationCase("complete", "complete", (ProducedSpec("step.result"),)),),
        ),),
        terminal_contract=TerminalContract(
            success_places=("step.result",), unfinished_places=("step.request",),
            stop_on_terminal=True,
        ),
        policy=AnalysisPolicy(
            mode="strict", max_states=8, max_edges=8, max_depth=4,
            properties=("safety", "possible_successful_completion"),
            required_properties=("safety", "possible_successful_completion"),
        ),
    )
```

Owner edits pause admission and drain existing firings normally. Analysis then
uses the latest checkpoint and a no-write mapping preview. Proposed token IDs
have no authority; allocation preserves their exact one-to-one source mapping.
Mapping tokens, lineage, report, checkpoint and adoption commit together. The
commit gate rechecks the exact input and bounded report against that transaction
snapshot. Verification checks the stored graph prefix, exact successors, exhaustive
expanded-node closure and property conclusions without starting another BFS.
Valid advisory UNKNOWN remains valid when the verification clock is faster. No partial registered mapping/adoption is accepted on rejection.
Object-store prewrite files can remain after an aborted transaction and confer
no Registry authority.

Use [Registry/runtime semantics](runtime-registry.md) and
[native net operations](../guides/net-operations.md) for existing execution and
replacement contracts. The diagnostics plugin ABI remains read-only warnings.
