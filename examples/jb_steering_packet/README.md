# JB clinical steering packet

[English](README.md) | [中文](README_ZH.md)

This provider-backed example asks the main RPNH agent to design its own
multi-agent workflow for a real clinical-document/data task. It does not load a
predefined experiment graph. The workflow must combine evidence review and
participant-level analysis, then publish a concise design memo and appendix.

![Actual accepted JB workflow overview](assets/jb-steering-petrinet.png)

## Public source

The task is based on Penna et al., *PLOS Neglected Tropical Diseases* 11(7):
e0005725, DOI [10.1371/journal.pntd.0005725](https://doi.org/10.1371/journal.pntd.0005725),
trial registration NCT00669643. The preparation script obtains the article
manuscript, S2 statistical analysis plan and S5 data file with codebook from
the official PLOS links recorded in `sources.json`.

Participant rows and source supplements are not copied into this repository.
The article page identifies the article as CC BY 4.0; the example does not
assume an additional redistribution grant for the separate source files. They
remain in the private directory selected by the user.

### Data-transfer boundary

`prepare_inputs.py` embeds the participant-level workbook text in `prompt.txt`.
The run stores that packet in the example Registry and sends it through the
user-selected execution route, which may be a remote provider. Before running,
confirm that the source terms and your privacy policy permit that route. The
required acknowledgement flag records this operator decision; it does not
alter, redact or approve the data.

## Reproduce

Use Linux/WSL2, a source checkout with RPNH installed, and one explicitly
authorized execution selection. These commands download public files and can
make paid/external model calls. The example never chooses or changes a
provider/model.

```bash
: "${EXECUTION_CONFIG:?Set an authorized exact execution selection}"
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-jb.XXXXXX")"
python examples/jb_steering_packet/prepare_inputs.py \
  --output-dir "$DEMO_ROOT/inputs"
python examples/jb_steering_packet/analyze_workbook.py \
  "$DEMO_ROOT/inputs/workbook.tsv" \
  --output "$DEMO_ROOT/reference-analysis.json"
python examples/jb_steering_packet/run.py \
  --execution "$EXECUTION_CONFIG" \
  --input-dir "$DEMO_ROOT/inputs" \
  --session-dir "$DEMO_ROOT/session" \
  --acknowledge-participant-data-transfer
```

`prepare_inputs.py` also accepts `--article`, `--sap` and `--workbook` together
for already-downloaded official files. The output directory must be absent.
The runner prints the task ID and child `run_dir`, waits for Registry terminal
authority, and prints the registered final result plus the PetriNet summary.

Inspect the actual run without executing a model:

```bash
: "${RUN_DIR:?Use the child run_dir printed by run.py}"
rpnh net --run "$RUN_DIR"
rpnh net --run "$RUN_DIR" --show-resources
rpnh net --run "$RUN_DIR" --resources-only
rpnh net --run "$RUN_DIR" --view --no-open
```

This task declares no resource places, so the first two projections are
structurally identical and `--resources-only` is empty. Do not add display-only
resources to change that result.

## Acceptance

Compare the memo and appendix with `reference_result.json` and the independent
analysis JSON. A sound result keeps 278 **evaluable** participants per arm
separate from enrollment, uses the exact observed completion fraction
`439/613`, and therefore rounds upward to 389 randomized participants per arm
(778 total). It also decodes 323 U-MDT and 290 R-MDT participants, reports the
baseline summaries, and treats only reaction dates inside each participant's
first-to-last-visit interval as observed events.

The checked aggregate values are reproducible; prose and graph topology may
differ because the main agent designs the workflow. Registry terminal evidence
is necessary but does not replace the business checks. `validation.json`
records the sanitized accepted-run boundary; no participant rows, Registry,
transcript, route or model identity is published.

If a run is intentionally stopped, follow the separate
[checkpoint recovery guide](../../docs/guides/checkpoint-recovery.md). Do not
start a replacement task merely because the frontend was closed.
