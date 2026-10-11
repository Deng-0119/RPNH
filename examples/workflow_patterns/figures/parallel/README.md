# Parallel fan-out and join

English | [中文](README_ZH.md)

[Return to the example guide](../../README.md).

These figures show the `initial_configured` declaration, without execution state, token markings or a success result. Use the full views for orientation and the numbered panels to read nodes, arcs and labels.

Declaration source: [parallel.json](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/workflow_patterns/scenarios/parallel.json), [run.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/workflow_patterns/run.py). Rendered by the [official Viewer at `34d42d17`](https://github.com/Deng-0119/RPNH/tree/34d42d171d7e9a7f803b71467063b55786c2cea8).

prepare branches to facts and risks, then converges at join. Overview shows four Agents and their four relations; Petri retains data/control places, weights and interrupted return branches.

[Overview](#overview) · [Petri](#petri)

## Overview

<img src="../../assets/pattern_parallel/overview-full.png" width="730" alt="Parallel — complete Agent Overview declaration" />

## Petri

<img src="../../assets/pattern_parallel/petri-full.png" width="730" alt="Parallel — complete Petri declaration" />

[Open the original orientation image](../../assets/pattern_parallel/petri-full.png).

[P01](#p01) → [P02](#p02) → [P03](#p03) → [P04](#p04) → [P05](#p05)

P01–P05 are overlapping left-to-right panels. Follow lines cut by an edge into the adjacent panel; bridge-shaped crossings do not join the lines.

### P01

**Request inlet.** The request enters prepare in [P02](#p02); an interrupted prepare returns it to this inlet.

Complete nodes (top to bottom, then left to right): `team.ingress__prepare__request`.

<img src="../../assets/pattern_parallel/P01.png" width="730" alt="Parallel fan-out and join — petri P01 — Request inlet" />

### P02

**Prepare and sibling Agents.** prepare supplies the control places and two copies of brief in [P03](#p03). facts and risks produce their separate join inputs in [P03](#p03) and [P05](#p05); interrupted branches return their inputs.

Complete nodes (top to bottom, then left to right): `team.facts`, `team.prepare`, `team.risks`.

<img src="../../assets/pattern_parallel/P02.png" width="730" alt="Parallel fan-out and join — petri P02 — Prepare and sibling Agents" />

### P03

**Fan-out handoffs and risks control.** The first three places connect prepare to facts/risks in [P02](#p02); the bottom place carries risks completion to join in [P04](#p04). The shared brief has weight 2 on production and weight 1 on each consumption.

Complete nodes (top to bottom, then left to right): `team.control__prepare_to_facts`, `team.data__prepare__brief`, `team.control__prepare_to_risks`, `team.control__risks_to_join`.

<img src="../../assets/pattern_parallel/P03.png" width="730" alt="Parallel fan-out and join — petri P03 — Fan-out handoffs and risks control" />

### P04

**All-input join.** join consumes both control signals and both branch products from [P03](#p03) and [P05](#p05). Completion produces result; interruption restores the four inputs.

Complete nodes (top to bottom, then left to right): `team.join`.

<img src="../../assets/pattern_parallel/P04.png" width="730" alt="Parallel fan-out and join — petri P04 — All-input join" />

### P05

**Facts control, branch products and result.** facts and risks from [P02](#p02) supply join in [P04](#p04); join produces the result shown here. The remaining risks control place is in [P03](#p03).

Complete nodes (top to bottom, then left to right): `team.control__facts_to_join`, `team.data__facts__facts`, `team.data__join__result`, `team.data__risks__risks`.

<img src="../../assets/pattern_parallel/P05.png" width="730" alt="Parallel fan-out and join — petri P05 — Facts control, branch products and result" />

Node IDs identify generic or shortened Viewer labels; the images retain the original display.
