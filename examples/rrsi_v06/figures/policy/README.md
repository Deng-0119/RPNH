# Policy: prepare → model → grade

English | [中文](README_ZH.md)

[Return to the example guide](../../README.md).

These figures show the `initial_configured` declaration, without execution state, token markings or a success result. Use the full views for orientation and the numbered panels to read nodes, arcs and labels.

Declaration source: [formal_policy.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/rrsi_v06/src/rpnh_rrsi/formal_policy.py). Rendered by the [official Viewer at `34d42d17`](https://github.com/Deng-0119/RPNH/tree/34d42d171d7e9a7f803b71467063b55786c2cea8).

request → prepare → state → model → response → grade → result. Flow folds the intermediate state and response places while preserving the operation sequence.

[Petri](#petri) · [Flow](#flow)

## Petri

<img src="../../assets/rrsi.policy.prepare-model-grade/petri-full.png" width="730" alt="Policy — complete Petri declaration" />

[Open the original orientation image](../../assets/rrsi.policy.prepare-model-grade/petri-full.png).

[P01](#p01) → [P02](#p02) → [P03](#p03) → [P04](#p04) → [P05](#p05) → [P06](#p06) → [P07](#p07)

### P01

**Request.** `request` → [prepare](#p02).

Complete nodes (top to bottom, then left to right): `policy.request`.

<img src="../../assets/rrsi.policy.prepare-model-grade/P01.png" width="730" alt="Policy: prepare → model → grade — petri P01 — Request" />

### P02

**Prepare.** [request](#p01) → `prepare` → [state](#p03).

Complete nodes (top to bottom, then left to right): `policy.prepare`.

<img src="../../assets/rrsi.policy.prepare-model-grade/P02.png" width="730" alt="Policy: prepare → model → grade — petri P02 — Prepare" />

### P03

**State.** [prepare](#p02) → `state` → [model](#p04).

Complete nodes (top to bottom, then left to right): `policy.state`.

<img src="../../assets/rrsi.policy.prepare-model-grade/P03.png" width="730" alt="Policy: prepare → model → grade — petri P03 — State" />

### P04

**Model.** [state](#p03) → `model` → [response](#p05).

Complete nodes (top to bottom, then left to right): `policy.model`.

<img src="../../assets/rrsi.policy.prepare-model-grade/P04.png" width="730" alt="Policy: prepare → model → grade — petri P04 — Model" />

### P05

**Response.** [model](#p04) → `response` → [grade](#p06).

Complete nodes (top to bottom, then left to right): `policy.response`.

<img src="../../assets/rrsi.policy.prepare-model-grade/P05.png" width="730" alt="Policy: prepare → model → grade — petri P05 — Response" />

### P06

**Grade.** [response](#p05) → `grade` → [result](#p07).

Complete nodes (top to bottom, then left to right): `policy.grade`.

<img src="../../assets/rrsi.policy.prepare-model-grade/P06.png" width="730" alt="Policy: prepare → model → grade — petri P06 — Grade" />

### P07

**Result.** [grade](#p06) → `result`.

Complete nodes (top to bottom, then left to right): `policy.result`.

<img src="../../assets/rrsi.policy.prepare-model-grade/P07.png" width="730" alt="Policy: prepare → model → grade — petri P07 — Result" />

## Flow

<img src="../../assets/rrsi.policy.prepare-model-grade/flow-full.png" width="730" alt="Policy — complete Flow declaration" />

[Open the original orientation image](../../assets/rrsi.policy.prepare-model-grade/flow-full.png).

[F01](#f01) → [F02](#f02) → [F03](#f03) → [F04](#f04) → [F05](#f05)

### F01

**Request.** `request` → [prepare](#f02).

Complete nodes (top to bottom, then left to right): `policy.request`.

<img src="../../assets/rrsi.policy.prepare-model-grade/F01.png" width="730" alt="Policy: prepare → model → grade — flow F01 — Request" />

### F02

**Prepare.** [request](#f01) → `prepare` → [model](#f03). The line to model folds [state](#p03).

Complete nodes (top to bottom, then left to right): `policy.prepare`.

<img src="../../assets/rrsi.policy.prepare-model-grade/F02.png" width="730" alt="Policy: prepare → model → grade — flow F02 — Prepare" />

### F03

**Model.** [prepare](#f02) → `model` → [grade](#f04). The line to grade folds [response](#p05).

Complete nodes (top to bottom, then left to right): `policy.model`.

<img src="../../assets/rrsi.policy.prepare-model-grade/F03.png" width="730" alt="Policy: prepare → model → grade — flow F03 — Model" />

### F04

**Grade.** [model](#f03) → `grade` → [result](#f05).

Complete nodes (top to bottom, then left to right): `policy.grade`.

<img src="../../assets/rrsi.policy.prepare-model-grade/F04.png" width="730" alt="Policy: prepare → model → grade — flow F04 — Grade" />

### F05

**Result.** [grade](#f04) → `result`.

Complete nodes (top to bottom, then left to right): `policy.result`.

<img src="../../assets/rrsi.policy.prepare-model-grade/F05.png" width="730" alt="Policy: prepare → model → grade — flow F05 — Result" />

Node IDs identify generic or shortened Viewer labels; the images retain the original display.
