# RRSI v0.6 application example

English | [中文](README_ZH.md)

This example implements a bounded, local RRSI v0.6 application on the existing
RPNH Registry and PetriNet runtime. It does not add a second harness, change
`cpn/`, or require a particular provider or model. Analyst, Digester, Proposer,
Critic, and Policy occurrences are independent RPNH child runs with their own
Registry evidence.

The project objective is framework-level feasibility: demonstrate that a
state-of-the-art RSI design, represented here by the public RRSI method, can be
implemented and operated normally as an application on the current RPNH
harness. "State of the art" qualifies the RSI framework being implemented; it
does not turn this local fixture into a reproduction of the paper's benchmark
scores.

The checked-in timeout fixture demonstrates the application and selection
mechanics. It is not an official Google RRSI benchmark domain and is not a
reproduction of the paper's reported results. The official source and domain
adapters are public at
[`google-research/rrsi`](https://github.com/google-research/rrsi); their external
benchmark dependencies are not vendored here. Design boundaries and third-party
notices are in the adjacent `DESIGN.md` and `THIRD_PARTY_NOTICES.md` files.

## Architecture

```text
fixed protocol + user execution profile
                 |
                 v
calibration -> H0 evolve -> two protocol rounds -> H0/final heldout -> export
                              |
                              +-> Analyst child Registry
                              +-> Digester child Registries
                              +-> Proposer child Registry
                              +-> Critic child Registry

every scored slot ---------------------------> Policy child Registry
```

The campaign is application coordination. Each model-facing occurrence uses
the existing registered-host binding, `start_run`, `Harness.exact_execute`, and
PetriNet transition validation. Candidate manifests use explicit run/resource
references and literal source text; they do not introduce hashes, checksums,
fingerprints, a second Registry, or a new workflow service.

## Install and test

From the RPNH source root:

```bash
python -m pip install -e .
python -m pip install -e './examples/rrsi_v06[test]'
python -m pytest examples/rrsi_v06/tests -q
```

The tests use scripted input ports and make no provider calls. The root RPNH
test discovery does not automatically include this standalone example; run the
explicit command above.

## Run with your profile

The following command can contact the model configured by your own RPNH
execution profile. Use it only after authorizing that experiment. Keep the run
directory and profile outside the repository.

```bash
: "${RPNH_EXECUTION_PROFILE:?Set an authorized RPNH execution profile}"
export RRSI_WORK="$(mktemp -d)"
python examples/rrsi_v06/example.py \
  --run-root "$RRSI_WORK/run" \
  --execution "$RPNH_EXECUTION_PROFILE" \
  --protocol examples/rrsi_v06/protocol.example.json
```

The destination must not already exist. The report is written to
`$RRSI_WORK/run/formal-report.json`; each child Registry remains under `roles/`
or `policy/`. For example, inspect one generated Policy run with:

```bash
rpnh net --run "$RRSI_WORK/run/policy/evolve-h0/H0/evolve-missing-0" \
  --view --no-open
```

No execution profile, Registry database, run directory, provider transcript,
or historical experiment result is checked into this example.

## Completion meaning

`formal_rrsi_v06_local_complete=true` means this frozen local fixture completed
all planned rounds and policy slots using real Registry/PetriNet/provider
evidence. Injected test runners can prove structural behavior but cannot produce
that completion claim. The claim does not cover official coding, workspace, or
engineering benchmark payloads, and it does not claim strict AgentLoop
conformance or B1 abrupt-loss reconciliation.
