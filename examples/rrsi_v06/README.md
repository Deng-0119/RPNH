# RRSI v0.6 application example

English | [中文](README_ZH.md)

This example implements the public RRSI v0.6 method as a bounded application on
the existing RPNH Registry and PetriNet runtime. It demonstrates how the current
harness supports an RSI role loop, independent evaluations, and score/cost
selection. Analyst, Digester, Proposer, Critic, and Policy occurrences run as
independent RPNH child runs with their own Registry evidence. The user's
execution profile selects the provider and model.

The included timeout fixture exercises two rounds of Policy evolution. The
reference method and domain adapters are available at
[`google-research/rrsi`](https://github.com/google-research/rrsi). See
`DESIGN.md` for the application design and `THIRD_PARTY_NOTICES.md` for
attribution.

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
PetriNet transition validation. Candidate manifests link literal source text to
explicit run and resource references.

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

Keep your execution profile and generated run artifacts outside the source
checkout. Inspect each experiment through its report and child Registries.

## Completion meaning

`formal_rrsi_v06_local_complete=true` records completion of the frozen timeout
fixture's planned rounds and Policy slots with Registry, PetriNet, and provider
evidence. Scripted tests use `injected_test_runners`; their structural result is
recorded in `execution_evidence.structural_campaign_complete`. Read
`round_decisions`, the H0/final evolve and heldout aggregates, and `usage` to
inspect adoption, scores, and token coverage.
