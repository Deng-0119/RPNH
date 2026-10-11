# Four-round synthetic iteration

English | [中文](README_ZH.md)

[Return to the example guide](../../README.md).

These figures show the `initial_configured` declaration, without execution state, token markings or a success result. Use the full views for orientation and the numbered panels to read nodes, arcs and labels.

Declaration source: [deterministic.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/rsi_workflows/deterministic.py), [test_runtime.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/rsi_workflows/tests/test_runtime.py), [iteration_profile.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/cpn/rpnh/iteration_profile.py). Rendered by the [official Viewer at `34d42d17`](https://github.com/Deng-0119/RPNH/tree/34d42d171d7e9a7f803b71467063b55786c2cea8).

Each round follows proposer → evaluator → selector. Rounds 1–3 feed next into the following round; every stop and round 4 selected_state are declared outputs. Dashed open arrows read candidate. Flow folds each round’s incumbent and evaluation places while preserving request, candidate, next and terminal branches.

[Petri](#petri) · [Flow](#flow)

## Petri

<img src="../../assets/rsi_rounds_4/petri-full.png" width="730" alt="Four-round synthetic iteration — complete Petri declaration" />

[Open the original orientation image](../../assets/rsi_rounds_4/petri-full.png).

Jump by round: [inputs](#p01) · [round 1](#p02) · [round 2](#p08) · [round 3](#p15) · [round 4](#p21) · [outputs](#p26)

Read the overlapping numbered panels from left to right; lines cut at an edge continue in adjacent panels. Node IDs follow top-to-bottom, then left-to-right order and identify generic or shortened Viewer labels.

P27/P28 are extra branch views for rounds 1/3, alongside P06–P07 / P19–P20. Bridge-shaped crossings do not join the lines.

### P01

**Initial inputs.** Initial state → [round 1 proposer](#p02); the four requests go directly to their matching evaluators: [1](#p04), [2](#p10), [3](#p17), [4](#p23).

Complete nodes (top to bottom, then left to right): `round_4_evaluator.request`, `round_3_evaluator.request`, `round_2_evaluator.request`, `round_1_proposer.state`, `round_1_evaluator.request`.

<img src="../../assets/rsi_rounds_4/P01.png" width="730" alt="Four-round synthetic iteration — petri P01 — Initial inputs" />

### P02

**Round 1 proposer.** [state](#p01) → proposer → [candidate](#p03) and [incumbent](#p03).

Complete nodes (top to bottom, then left to right): `round_1_proposer.run`.

<img src="../../assets/rsi_rounds_4/P02.png" width="730" alt="Four-round synthetic iteration — petri P02 — Round 1 proposer" />

### P03

**Round 1 candidate.** [proposer](#p02) → candidate; [evaluator](#p04) reads candidate and [selector](#p06) consumes it. The incumbent below also feeds [selector](#p06).

Complete nodes (top to bottom, then left to right): `round_1_evaluator.candidate`, `round_1_proposer.incumbent`.

<img src="../../assets/rsi_rounds_4/P03.png" width="730" alt="Four-round synthetic iteration — petri P03 — Round 1 candidate" />

### P04

**Round 1 evaluator.** [request](#p01) + [candidate](#p03) → evaluator → [evaluation](#p05); the dashed open arrow reads candidate without consuming it.

Complete nodes (top to bottom, then left to right): `round_1_evaluator.run`.

<img src="../../assets/rsi_rounds_4/P04.png" width="730" alt="Four-round synthetic iteration — petri P04 — Round 1 evaluator" />

### P05

**Round 1 evaluation.** [evaluator](#p04) → evaluation → [selector](#p06).

Complete nodes (top to bottom, then left to right): `round_1_evaluator.evaluation`.

<img src="../../assets/rsi_rounds_4/P05.png" width="730" alt="Four-round synthetic iteration — petri P05 — Round 1 evaluation" />

### P06

**Round 1 selector.** [candidate](#p03) + [incumbent](#p03) + [evaluation](#p05) → selector → [next](#p07) (retain/select) / [stop](#p26); branch bends: [P27](#p27).

Complete nodes (top to bottom, then left to right): `round_1_selector.run`.

<img src="../../assets/rsi_rounds_4/P06.png" width="730" alt="Four-round synthetic iteration — petri P06 — Round 1 selector" />

### P07

**Round 1 next.** [selector](#p06) → next → [round 2 proposer](#p08).

Complete nodes (top to bottom, then left to right): `round_1_selector.next`.

<img src="../../assets/rsi_rounds_4/P07.png" width="730" alt="Four-round synthetic iteration — petri P07 — Round 1 next" />

### P08

**Round 2 proposer.** [round 1 next](#p07) → proposer → [candidate](#p09) and [incumbent](#p10).

Complete nodes (top to bottom, then left to right): `round_2_proposer.run`.

<img src="../../assets/rsi_rounds_4/P08.png" width="730" alt="Four-round synthetic iteration — petri P08 — Round 2 proposer" />

### P09

**Round 2 candidate.** [proposer](#p08) → candidate; [evaluator](#p10) reads candidate and [selector](#p12) consumes it. The full incumbent node is in [P10](#p10).

Complete nodes (top to bottom, then left to right): `round_2_evaluator.candidate`.

<img src="../../assets/rsi_rounds_4/P09.png" width="730" alt="Four-round synthetic iteration — petri P09 — Round 2 candidate" />

### P10

**Round 2 evaluator and incumbent.** [request](#p01) + [candidate](#p09) → evaluator → [evaluation](#p11); the dashed open arrow reads candidate without consuming it. Incumbent also goes to [selector](#p12).

Complete nodes (top to bottom, then left to right): `round_2_evaluator.run`, `round_2_proposer.incumbent`.

<img src="../../assets/rsi_rounds_4/P10.png" width="730" alt="Four-round synthetic iteration — petri P10 — Round 2 evaluator and incumbent" />

### P11

**Round 2 evaluation.** [evaluator](#p10) → evaluation → [selector](#p12).

Complete nodes (top to bottom, then left to right): `round_2_evaluator.evaluation`.

<img src="../../assets/rsi_rounds_4/P11.png" width="730" alt="Four-round synthetic iteration — petri P11 — Round 2 evaluation" />

### P12

**Round 2 selector.** [candidate](#p09) + [incumbent](#p10) + [evaluation](#p11) → selector → [next](#p13) (retain/select) / [stop](#p26).

Complete nodes (top to bottom, then left to right): `round_2_selector.run`.

<img src="../../assets/rsi_rounds_4/P12.png" width="730" alt="Four-round synthetic iteration — petri P12 — Round 2 selector" />

### P13

**Round 2 next.** [selector](#p12) → next → [round 3 proposer](#p15).

Complete nodes (top to bottom, then left to right): `round_2_selector.next`.

<img src="../../assets/rsi_rounds_4/P13.png" width="730" alt="Four-round synthetic iteration — petri P13 — Round 2 next" />

### P14

**Round 2 → 3 connecting routes.** [Round 2 next](#p13) → [round 3 proposer](#p15). This connecting strip contains a partial node; the long request/stop routes continue to their respective evaluators/outputs.

<img src="../../assets/rsi_rounds_4/P14.png" width="730" alt="Four-round synthetic iteration — petri P14 — Round 2 → 3 connecting routes" />

### P15

**Round 3 proposer.** [round 2 next](#p13) → proposer → [candidate](#p16) and [incumbent](#p16).

Complete nodes (top to bottom, then left to right): `round_3_proposer.run`.

<img src="../../assets/rsi_rounds_4/P15.png" width="730" alt="Four-round synthetic iteration — petri P15 — Round 3 proposer" />

### P16

**Round 3 candidate.** [proposer](#p15) → candidate; [evaluator](#p17) reads candidate and [selector](#p19) consumes it. The incumbent below also feeds [selector](#p19).

Complete nodes (top to bottom, then left to right): `round_3_evaluator.candidate`, `round_3_proposer.incumbent`.

<img src="../../assets/rsi_rounds_4/P16.png" width="730" alt="Four-round synthetic iteration — petri P16 — Round 3 candidate" />

### P17

**Round 3 evaluator.** [request](#p01) + [candidate](#p16) → evaluator → [evaluation](#p18); the dashed open arrow reads candidate without consuming it.

Complete nodes (top to bottom, then left to right): `round_3_evaluator.run`.

<img src="../../assets/rsi_rounds_4/P17.png" width="730" alt="Four-round synthetic iteration — petri P17 — Round 3 evaluator" />

### P18

**Round 3 evaluation.** [evaluator](#p17) → evaluation → [selector](#p19).

Complete nodes (top to bottom, then left to right): `round_3_evaluator.evaluation`.

<img src="../../assets/rsi_rounds_4/P18.png" width="730" alt="Four-round synthetic iteration — petri P18 — Round 3 evaluation" />

### P19

**Round 3 selector.** [candidate](#p16) + [incumbent](#p16) + [evaluation](#p18) → selector → [next](#p20) (retain/select) / [stop](#p26); branch bends: [P28](#p28).

Complete nodes (top to bottom, then left to right): `round_3_selector.run`.

<img src="../../assets/rsi_rounds_4/P19.png" width="730" alt="Four-round synthetic iteration — petri P19 — Round 3 selector" />

### P20

**Round 3 next.** [selector](#p19) → next → [round 4 proposer](#p21).

Complete nodes (top to bottom, then left to right): `round_3_selector.next`.

<img src="../../assets/rsi_rounds_4/P20.png" width="730" alt="Four-round synthetic iteration — petri P20 — Round 3 next" />

### P21

**Round 4 proposer.** [round 3 next](#p20) → proposer → [candidate](#p22) and [incumbent](#p23).

Complete nodes (top to bottom, then left to right): `round_4_proposer.run`.

<img src="../../assets/rsi_rounds_4/P21.png" width="730" alt="Four-round synthetic iteration — petri P21 — Round 4 proposer" />

### P22

**Round 4 candidate.** [proposer](#p21) → candidate; [evaluator](#p23) reads candidate and [selector](#p25) consumes it. The full incumbent node is in [P23](#p23).

Complete nodes (top to bottom, then left to right): `round_4_evaluator.candidate`.

<img src="../../assets/rsi_rounds_4/P22.png" width="730" alt="Four-round synthetic iteration — petri P22 — Round 4 candidate" />

### P23

**Round 4 evaluator and incumbent.** [request](#p01) + [candidate](#p22) → evaluator → [evaluation](#p24); the dashed open arrow reads candidate without consuming it. Incumbent also goes to [selector](#p25).

Complete nodes (top to bottom, then left to right): `round_4_evaluator.run`, `round_4_proposer.incumbent`.

<img src="../../assets/rsi_rounds_4/P23.png" width="730" alt="Four-round synthetic iteration — petri P23 — Round 4 evaluator and incumbent" />

### P24

**Round 4 evaluation.** [evaluator](#p23) → evaluation → [selector](#p25).

Complete nodes (top to bottom, then left to right): `round_4_evaluator.evaluation`.

<img src="../../assets/rsi_rounds_4/P24.png" width="730" alt="Four-round synthetic iteration — petri P24 — Round 4 evaluation" />

### P25

**Round 4 selector.** [candidate](#p22) + [incumbent](#p23) + [evaluation](#p24) → selector → [next](#p26) (retain/select) / [stop](#p26).

Complete nodes (top to bottom, then left to right): `round_4_selector.run`.

<img src="../../assets/rsi_rounds_4/P25.png" width="730" alt="Four-round synthetic iteration — petri P25 — Round 4 selector" />

### P26

**Declared terminal outputs.** Round 4 retain/select → selected_state; each round’s stop branch → its corresponding stop output. Selector origins: [1](#p06), [2](#p12), [3](#p19), [4](#p25).

Complete nodes (top to bottom, then left to right): `round_4_selector.next`, `round_4_selector.stop`, `round_3_selector.stop`, `round_2_selector.stop`, `round_1_selector.stop`.

<img src="../../assets/rsi_rounds_4/P26.png" width="730" alt="Four-round synthetic iteration — petri P26 — Declared terminal outputs" />

### P27

**Round 1 retain/select branches.** Two distinct branches from [selector](#p06) → [next](#p07); the neighboring panels show the complete endpoint nodes. The stop route continues to [outputs](#p26).

<img src="../../assets/rsi_rounds_4/P27.png" width="730" alt="Four-round synthetic iteration — petri P27 — Round 1 retain/select branches" />

### P28

**Round 3 retain/select branches.** Two distinct branches from [selector](#p19) → [next](#p20); the neighboring panels show the complete endpoint nodes. The stop route continues to [outputs](#p26).

<img src="../../assets/rsi_rounds_4/P28.png" width="730" alt="Four-round synthetic iteration — petri P28 — Round 3 retain/select branches" />

## Flow

<img src="../../assets/rsi_rounds_4/flow-full.png" width="730" alt="Four-round synthetic iteration — complete Flow declaration" />

[Open the original orientation image](../../assets/rsi_rounds_4/flow-full.png).

Jump by round: [inputs](#f01) · [round 1](#f02) · [round 2](#f07) · [round 3](#f12) · [round 4](#f17) · [outputs](#f21)

Read the overlapping numbered panels from left to right; lines cut at an edge continue in adjacent panels. Node IDs follow top-to-bottom, then left-to-right order and identify generic or shortened Viewer labels.

### F01

**Initial inputs.** Initial state → [round 1 proposer](#f02); the four requests go directly to their matching evaluators: [1](#f04), [2](#f09), [3](#f14), [4](#f19).

Complete nodes (top to bottom, then left to right): `round_4_evaluator.request`, `round_3_evaluator.request`, `round_2_evaluator.request`, `round_1_evaluator.request`, `round_1_proposer.state`.

<img src="../../assets/rsi_rounds_4/F01.png" width="730" alt="Four-round synthetic iteration — flow F01 — Initial inputs" />

### F02

**Round 1 proposer.** [state](#f01) → proposer → [candidate](#f03); the incumbent route is folded into the direct line to [selector](#f05).

Complete nodes (top to bottom, then left to right): `round_1_proposer.run`.

<img src="../../assets/rsi_rounds_4/F02.png" width="730" alt="Four-round synthetic iteration — flow F02 — Round 1 proposer" />

### F03

**Round 1 candidate.** [proposer](#f02) → candidate; [evaluator](#f04) reads candidate and [selector](#f05) consumes it.

Complete nodes (top to bottom, then left to right): `round_1_evaluator.candidate`.

<img src="../../assets/rsi_rounds_4/F03.png" width="730" alt="Four-round synthetic iteration — flow F03 — Round 1 candidate" />

### F04

**Round 1 evaluator.** [request](#f01) + [candidate](#f03) → evaluator → [selector](#f05); the dashed open arrow reads candidate without consuming it. The evaluation place is folded here.

Complete nodes (top to bottom, then left to right): `round_1_evaluator.run`.

<img src="../../assets/rsi_rounds_4/F04.png" width="730" alt="Four-round synthetic iteration — flow F04 — Round 1 evaluator" />

### F05

**Round 1 selector.** [candidate](#f03) + [proposer](#f02) + [evaluator](#f04) → selector → [next](#f06) (retain/select) / [stop](#f21).

Complete nodes (top to bottom, then left to right): `round_1_selector.run`.

<img src="../../assets/rsi_rounds_4/F05.png" width="730" alt="Four-round synthetic iteration — flow F05 — Round 1 selector" />

### F06

**Round 1 next.** [selector](#f05) → next → [round 2 proposer](#f07).

Complete nodes (top to bottom, then left to right): `round_1_selector.next`.

<img src="../../assets/rsi_rounds_4/F06.png" width="730" alt="Four-round synthetic iteration — flow F06 — Round 1 next" />

### F07

**Round 2 proposer.** [round 1 next](#f06) → proposer → [candidate](#f08); the incumbent route is folded into the direct line to [selector](#f10).

Complete nodes (top to bottom, then left to right): `round_2_proposer.run`.

<img src="../../assets/rsi_rounds_4/F07.png" width="730" alt="Four-round synthetic iteration — flow F07 — Round 2 proposer" />

### F08

**Round 2 candidate.** [proposer](#f07) → candidate; [evaluator](#f09) reads candidate and [selector](#f10) consumes it.

Complete nodes (top to bottom, then left to right): `round_2_evaluator.candidate`.

<img src="../../assets/rsi_rounds_4/F08.png" width="730" alt="Four-round synthetic iteration — flow F08 — Round 2 candidate" />

### F09

**Round 2 evaluator.** [request](#f01) + [candidate](#f08) → evaluator → [selector](#f10); the dashed open arrow reads candidate without consuming it. The evaluation place is folded here.

Complete nodes (top to bottom, then left to right): `round_2_evaluator.run`.

<img src="../../assets/rsi_rounds_4/F09.png" width="730" alt="Four-round synthetic iteration — flow F09 — Round 2 evaluator" />

### F10

**Round 2 selector.** [candidate](#f08) + [proposer](#f07) + [evaluator](#f09) → selector → [next](#f11) (retain/select) / [stop](#f21).

Complete nodes (top to bottom, then left to right): `round_2_selector.run`.

<img src="../../assets/rsi_rounds_4/F10.png" width="730" alt="Four-round synthetic iteration — flow F10 — Round 2 selector" />

### F11

**Round 2 next.** [selector](#f10) → next → [round 3 proposer](#f12).

Complete nodes (top to bottom, then left to right): `round_2_selector.next`.

<img src="../../assets/rsi_rounds_4/F11.png" width="730" alt="Four-round synthetic iteration — flow F11 — Round 2 next" />

### F12

**Round 3 proposer.** [round 2 next](#f11) → proposer → [candidate](#f13); the incumbent route is folded into the direct line to [selector](#f15).

Complete nodes (top to bottom, then left to right): `round_3_proposer.run`.

<img src="../../assets/rsi_rounds_4/F12.png" width="730" alt="Four-round synthetic iteration — flow F12 — Round 3 proposer" />

### F13

**Round 3 candidate.** [proposer](#f12) → candidate; [evaluator](#f14) reads candidate and [selector](#f15) consumes it.

Complete nodes (top to bottom, then left to right): `round_3_evaluator.candidate`.

<img src="../../assets/rsi_rounds_4/F13.png" width="730" alt="Four-round synthetic iteration — flow F13 — Round 3 candidate" />

### F14

**Round 3 evaluator.** [request](#f01) + [candidate](#f13) → evaluator → [selector](#f15); the dashed open arrow reads candidate without consuming it. The evaluation place is folded here.

Complete nodes (top to bottom, then left to right): `round_3_evaluator.run`.

<img src="../../assets/rsi_rounds_4/F14.png" width="730" alt="Four-round synthetic iteration — flow F14 — Round 3 evaluator" />

### F15

**Round 3 selector.** [candidate](#f13) + [proposer](#f12) + [evaluator](#f14) → selector → [next](#f16) (retain/select) / [stop](#f21).

Complete nodes (top to bottom, then left to right): `round_3_selector.run`.

<img src="../../assets/rsi_rounds_4/F15.png" width="730" alt="Four-round synthetic iteration — flow F15 — Round 3 selector" />

### F16

**Round 3 next.** [selector](#f15) → next → [round 4 proposer](#f17).

Complete nodes (top to bottom, then left to right): `round_3_selector.next`.

<img src="../../assets/rsi_rounds_4/F16.png" width="730" alt="Four-round synthetic iteration — flow F16 — Round 3 next" />

### F17

**Round 4 proposer.** [round 3 next](#f16) → proposer → [candidate](#f18); the incumbent route is folded into the direct line to [selector](#f20).

Complete nodes (top to bottom, then left to right): `round_4_proposer.run`.

<img src="../../assets/rsi_rounds_4/F17.png" width="730" alt="Four-round synthetic iteration — flow F17 — Round 4 proposer" />

### F18

**Round 4 candidate.** [proposer](#f17) → candidate; [evaluator](#f19) reads candidate and [selector](#f20) consumes it.

Complete nodes (top to bottom, then left to right): `round_4_evaluator.candidate`.

<img src="../../assets/rsi_rounds_4/F18.png" width="730" alt="Four-round synthetic iteration — flow F18 — Round 4 candidate" />

### F19

**Round 4 evaluator.** [request](#f01) + [candidate](#f18) → evaluator → [selector](#f20); the dashed open arrow reads candidate without consuming it. The evaluation place is folded here.

Complete nodes (top to bottom, then left to right): `round_4_evaluator.run`.

<img src="../../assets/rsi_rounds_4/F19.png" width="730" alt="Four-round synthetic iteration — flow F19 — Round 4 evaluator" />

### F20

**Round 4 selector.** [candidate](#f18) + [proposer](#f17) + [evaluator](#f19) → selector → [next](#f21) (retain/select) / [stop](#f21).

Complete nodes (top to bottom, then left to right): `round_4_selector.run`.

<img src="../../assets/rsi_rounds_4/F20.png" width="730" alt="Four-round synthetic iteration — flow F20 — Round 4 selector" />

### F21

**Declared terminal outputs.** Round 4 retain/select → selected_state; each round’s stop branch → its corresponding stop output. Selector origins: [1](#f05), [2](#f10), [3](#f15), [4](#f20).

Complete nodes (top to bottom, then left to right): `round_4_selector.next`, `round_4_selector.stop`, `round_3_selector.stop`, `round_2_selector.stop`, `round_1_selector.stop`.

<img src="../../assets/rsi_rounds_4/F21.png" width="730" alt="Four-round synthetic iteration — flow F21 — Declared terminal outputs" />

