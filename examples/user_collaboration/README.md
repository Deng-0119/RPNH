# Controlled user collaboration: results and graph inspection

English | [中文](README_ZH.md) | [Measured report](../../docs/results/user-collaboration-20261012/README.md)

This dated example records one native end-to-end collaboration study with three controlled simulated Registry identities and two real model conditions. Read the report for the proposal/adoption chain, fixed 512-route comparisons, failures and business tradeoffs.

## Original runtime panoramas

These unedited 4485 × 5000 PNGs came from the original **development** baseline and adopted-M2 runtime Registries in the official RPNH Viewer. Each shows all 24 batch branches, aggregation, publication and result. The capture has 74 settled firings and zero unsettled; Detailed flow folds 97 handoff places, showing 100 nodes / 123 edges from the full 197-node / 220-arc Petri net. Open the image at original size to inspect the whole workflow.

### Baseline

[![Original baseline runtime Detailed flow panorama](figures/baseline.png)](figures/baseline.png)

### Adopted M2

[![Original adopted-M2 runtime Detailed flow panorama](figures/adopted-M2.png)](figures/adopted-M2.png)

The original footer retains technical checkpoint identifiers and observation times. These identify recorded execution state, not a person or an account. They are not access tokens. The PNGs contain no textual/EXIF metadata. Neither image has been redrawn, relabeled or presented as a new cloud run. Their exact byte identities are in [SOURCES.json](../../docs/results/user-collaboration-20261012/SOURCES.json). Completed execution does not imply all route business constraints passed.

## Open the static structure in the official Viewer

From a source checkout with its compatible RPNH environment already available:

```bash
python examples/view_declaration.py collaboration-baseline
python examples/view_declaration.py collaboration-adopted
```

Add `--no-open` to print the actual loopback URL instead of opening the browser. In the Viewer choose **PetriNet** for every place/transition/arc or **Detailed flow** for the pure-tool flow. Pan, zoom and node inspection use the existing official UI. No model, dataset, provider profile or retained runtime Registry is needed for these bundled frames.

These commands show **initial_configured static declarations** compiled from the recorded baseline and adopted modules, with payloads and runtime identities excluded. Empty/unmarked state is declaration state, not the completed execution shown in the PNGs. There is no execution history, checkpoint replay or live run. Both declarations have the same topology; the candidate uses `deadline_first` on batches 0–10 and `zone_nearest` on 11–23, replacing baseline `nearest_neighbor` everywhere. The exact configuration declarations are [baseline](declarations/BASELINE_MODULE.json) and [adopted M2](declarations/ADOPTED_M2_MODULE.json).

## What is reproducible here

You can read the public aggregate results, inspect both retained runtime images and open the two static structures. The entire real-model experiment is not a standalone download-and-run package: it used an existing installation, admitted routing data, user-owned profiles and private Registry state. Dataset terms and scorer/source pins are in the report. Do not interpret the static Viewer as a public replay of that private runtime.

For building a collaboration application, see the [native collaboration guide](../../docs/guides/user-collaboration.md). For a small self-contained executable workflow, start with [workflow patterns](../workflow_patterns/README.md).
