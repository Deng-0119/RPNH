# 3-DOF powered descent

[English](README.md) | [中文](README_ZH.md)

This provider-backed example gives the main RPNH agent a numerical optimal
control problem and requires it to design the multi-agent workflow. It supplies
acceptance equations and an independent verifier, not a fixed workflow or
solver implementation.

![Actual accepted 3-DOF workflow overview](assets/three-dof-petrinet.png)

## Public source

The problem family follows Acikmese and Ploen, “Convex Programming Approach to
Powered Descent Guidance for Mars Landing,” DOI
[10.2514/1.27553](https://doi.org/10.2514/1.27553). `sources.json` also records
the SCvx paper [arXiv:1804.06539](https://arxiv.org/abs/1804.06539) and the
public G-FOLD numerical example used only as parameter provenance. No upstream
code is copied. `problem.json` is an independent, compact statement of the
dynamics, limits and acceptance tolerances.

## Reproduce

Use Linux/WSL2, a source checkout with RPNH installed, and one explicitly
authorized execution selection. The workspace used by the selected profile
must make the numerical packages chosen by the agent available; NumPy/SciPy is
the common path for this example. This command can make many paid/external
calls. The example never chooses or changes a provider/model.

```bash
: "${EXECUTION_CONFIG:?Set an authorized exact execution selection}"
python -c 'import numpy, scipy; print(numpy.__version__, scipy.__version__)'
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-3dof.XXXXXX")"
python examples/three_dof_powered_descent/run.py \
  --execution "$EXECUTION_CONFIG" \
  --session-dir "$DEMO_ROOT/session"
```

The runner registers `task.md`, `problem.json` and the independent verifier as
the exact user task. The main agent chooses the graph, launches one child
Registry, and the runner waits for terminal authority. It prints the task ID,
child `run_dir`, registered result and PetriNet summary.

Inspect the actual run without executing another model:

```bash
: "${RUN_DIR:?Use the child run_dir printed by run.py}"
rpnh net --run "$RUN_DIR"
rpnh net --run "$RUN_DIR" --show-resources
rpnh net --run "$RUN_DIR" --resources-only
rpnh net --run "$RUN_DIR" --view --no-open
```

This task declares no resource places, so `--resources-only` is empty. The
workflow must run the supplied verifier against its own
`descent_solution.json`; the same file can be checked again after copying its
exact settled workspace version outside the Registry:

```bash
python examples/three_dof_powered_descent/verify_solution.py \
  /path/to/copied/descent_solution.json
```

## Acceptance

Acceptance requires verifier `accepted: true`, no failures, terminal position
error at most 0.5 m, terminal velocity error at most 0.05 m/s, Euler recurrence
at substeps no larger than 0.01 s, and all thrust, altitude, glide-slope, speed
and dry-mass limits. `reference_result.json` records one observed feasible
trajectory (41.67 s, approximately 395.28 kN·s impulse); it is a comparison
point, not a required optimum or guarantee about arbitrary solver iterates.

Graph topology, numerical method and objective value may differ. Registry
terminal evidence is necessary but does not replace independent numerical
acceptance. `validation.json` records only the sanitized final-success boundary;
the raw Registry, generated trajectory, transcript, route and model identity
remain private.

If a run is intentionally stopped, follow the separate
[checkpoint recovery guide](../../docs/guides/checkpoint-recovery.md) and
continue the same child Registry.
