# 3-DOF powered descent

[English](README.md) | [中文](README_ZH.md)

This provider-backed example gives the main RPNH agent a numerical optimal
control problem and requires it to design the multi-agent workflow. It supplies
acceptance equations and an independent verifier, not a fixed workflow or
solver implementation.

[![Complete initial PN of the original accepted Designer child: 20 nodes / 52 arcs](assets/child-initial-petri.png)](assets/child-initial-petri.png)

Read-only projection of the complete original Registry-registered compiled declaration, retaining every place, transition, return arc and branch. This is an `initial_configured` static declaration without historical tokens, firings or completion coloring; no model or business rerun was made. The original compiler Git version is unknown; the exact registered revision, original-byte hash and capture-time official Viewer revision are in the [provenance](figures/child-initial/provenance.json).

[Read-only Viewer: zoom and node/arc details](#static-pn-viewer) · [Original runtime Overview](assets/three-dof-petrinet.png)
<a id="static-pn-viewer"></a>

### Read-only static PN Viewer

Run from the source checkout root with a compatible RPNH environment. This reads the bundled static frame without opening a Registry or executing the task.

```bash
python - <<'PY'
import json
from pathlib import Path
from cpn.frontend.server import serve_projection
frame = json.loads(Path("examples/three_dof_powered_descent/figures/child-initial/declaration-frame.json").read_text(encoding="utf-8"))
class Snapshot:
    def __call__(self):
        return frame["net"]
    def dashboard(self, cursor=None):
        if cursor is not None:
            raise ValueError("Static declaration has no execution history")
        return frame
serve_projection(Snapshot(), host="127.0.0.1", port=0, open_browser=False)
PY
```

Open the printed localhost URL and choose **PetriNet → Fit to view**. Scroll to zoom, drag to pan, and select a node or arc for native details and full labels. Static declarations have no execution history. Stop the server with Ctrl+C.

## Public source

The problem family follows Acikmese and Ploen, “Convex Programming Approach to
Powered Descent Guidance for Mars Landing,” DOI
[10.2514/1.27553](https://doi.org/10.2514/1.27553).  also records
the SCvx paper [arXiv:1804.06539](https://arxiv.org/abs/1804.06539) and the
public G-FOLD numerical example used only as parameter provenance. No upstream
code is copied.  is an independent, compact statement of the
dynamics, limits and acceptance tolerances.

## Reproduce

Use Linux/WSL2, a source checkout with RPNH installed, and one explicitly
authorized execution selection. The workspace used by the selected profile
must make the numerical packages chosen by the agent available; NumPy/SciPy is
the common path for this example. This command can make many paid/external
calls. The example never chooses or changes a provider/model.



The runner registers ,  and the independent verifier as
the exact user task. The main agent chooses the graph, launches one child
Registry, and the runner waits for terminal authority. It prints the task ID,
child , registered result and PetriNet summary.

Inspect the actual run without executing another model:



This task declares no resource places, so  is empty. The
workflow must run the supplied verifier against its own
; the same file can be checked again after copying its
exact settled workspace version outside the Registry:



## Acceptance

Acceptance requires verifier , no failures, terminal position
error at most 0.5 m, terminal velocity error at most 0.05 m/s, Euler recurrence
at substeps no larger than 0.01 s, and all thrust, altitude, glide-slope, speed
and dry-mass limits.  records one observed feasible
trajectory (41.67 s, approximately 395.28 kN·s impulse); it is a comparison
point, not a required optimum or guarantee about arbitrary solver iterates.

Graph topology, numerical method and objective value may differ. Registry
terminal evidence is necessary but does not replace independent numerical
acceptance.  records only the sanitized final-success boundary;
the raw Registry, generated trajectory, transcript, route and model identity
remain private.

If a run is intentionally stopped, follow the separate
[checkpoint recovery guide](../../docs/guides/checkpoint-recovery.md) and
continue the same child Registry.
