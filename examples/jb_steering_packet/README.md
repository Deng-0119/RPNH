# JB clinical steering packet

[English](README.md) | [中文](README_ZH.md)

This provider-backed example asks the main RPNH agent to design its own
multi-agent workflow for a real clinical-document/data task. It does not load a
predefined experiment graph. The workflow must combine evidence review and
participant-level analysis, then publish a concise design memo and appendix.

[![Complete initial PN of the original accepted Designer child: 24 nodes / 49 arcs](assets/child-initial-petri.png)](assets/child-initial-petri.png)

Read-only projection of the complete original Registry-registered compiled declaration, retaining every place, transition, return arc and branch. This is an `initial_configured` static declaration without historical tokens, firings or completion coloring; no model or business rerun was made. The original compiler Git version is unknown; the exact registered revision, original-byte hash and capture-time official Viewer revision are in the [provenance](figures/child-initial/provenance.json).

[Read-only Viewer: zoom and node/arc details](#static-pn-viewer) · [Original runtime Overview](assets/jb-steering-petrinet.png)
<a id="static-pn-viewer"></a>

### Read-only static PN Viewer

Run from the source checkout root with a compatible RPNH environment. This reads the bundled static frame without opening a Registry or executing the task.

```bash
python - <<'PY'
import json
from pathlib import Path
from cpn.frontend.server import serve_projection
frame = json.loads(Path("examples/jb_steering_packet/figures/child-initial/declaration-frame.json").read_text(encoding="utf-8"))
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

The task is based on Penna et al., *PLOS Neglected Tropical Diseases* 11(7):
e0005725, DOI [10.1371/journal.pntd.0005725](https://doi.org/10.1371/journal.pntd.0005725),
trial registration NCT00669643. The preparation script obtains the article
manuscript, S2 statistical analysis plan and S5 data file with codebook from
the official PLOS links recorded in .

Participant rows and source supplements are not copied into this repository.
The article page identifies the article as CC BY 4.0; the example does not
assume an additional redistribution grant for the separate source files. They
remain in the private directory selected by the user.

### Data-transfer boundary

 embeds the participant-level workbook text in .
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



 also accepts ,  and  together
for already-downloaded official files. The output directory must be absent.
The runner prints the task ID and child , waits for Registry terminal
authority, and prints the registered final result plus the PetriNet summary.

Inspect the actual run without executing a model:



This task declares no resource places, so the first two projections are
structurally identical and  is empty. Do not add display-only
resources to change that result.

## Acceptance

Compare the memo and appendix with  and the independent
analysis JSON. A sound result keeps 278 **evaluable** participants per arm
separate from enrollment, uses the exact observed completion fraction
, and therefore rounds upward to 389 randomized participants per arm
(778 total). It also decodes 323 U-MDT and 290 R-MDT participants, reports the
baseline summaries, and treats only reaction dates inside each participant's
first-to-last-visit interval as observed events.

The checked aggregate values are reproducible; prose and graph topology may
differ because the main agent designs the workflow. Registry terminal evidence
is necessary but does not replace the business checks. 
records the sanitized accepted-run boundary; no participant rows, Registry,
transcript, route or model identity is published.

If a run is intentionally stopped, follow the separate
[checkpoint recovery guide](../../docs/guides/checkpoint-recovery.md). Do not
start a replacement task merely because the frontend was closed.
