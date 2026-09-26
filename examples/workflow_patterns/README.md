# Workflow pattern gallery

English | [中文](README_ZH.md)

This gallery turns four common task shapes into fresh, inspectable RPNH
Registries. The default local-process fixture is deterministic and makes no
network call. Supplying `--execution` uses one exact, user-authorized route;
the runner never selects or changes a provider or model.

| Scenario | Shape | What to inspect |
|---|---|---|
| `serial` | `intake -> work -> deliver` | One registered handoff at a time. |
| `parallel` | `prepare -> (facts || risks) -> join` | Two enabled siblings and an all-input join. |
| `document` | `outline -> draft -> review -> publish` | A document represented by four versioned products. |
| `long_process` | Six serial Agent nodes | More canonical checkpoints for timeline replay. |

## Actual dashboard views

All images below were captured from completed deterministic Registry runs, not
from hand-drawn diagrams. Public copies omit exact checkpoint identities.

### Serial

![Serial workflow PetriNet](assets/serial-petrinet.png)

### Parallel fan-out and join

Overview makes the Agent network easy to read:

![Parallel workflow Agent overview](assets/parallel-overview.png)

PetriNet exposes the actual data places, control places and arcs that enforce
the same fan-out and all-input join:

![Parallel workflow PetriNet](assets/parallel-petrinet.png)

### Document task

![Document workflow Detailed flow](assets/document-flow.png)

### Long process

![Six-stage workflow overview](assets/long-process-overview.png)

The calculation example remains
[`hybrid_summary`](../hybrid_summary/README.md), because it demonstrates the
more useful model–native-plugin–model boundary instead of duplicating that
operation here.

From the repository root:

```bash
python -m examples.workflow_patterns.run --list
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-patterns.XXXXXX")"
python -m examples.workflow_patterns.run \
  --scenario serial --run-dir "$DEMO_ROOT/serial"
python -m examples.workflow_patterns.run \
  --scenario parallel --run-dir "$DEMO_ROOT/parallel"
python -m examples.workflow_patterns.run \
  --scenario document --run-dir "$DEMO_ROOT/document"
python -m examples.workflow_patterns.run \
  --scenario long_process --run-dir "$DEMO_ROOT/long-process"
```

Every successful command prints `status: PASS`, a terminal-evidence reference,
the exact result and PetriNet counts. Open any resulting run without changing
it:

```bash
RUN_DIR="$DEMO_ROOT/parallel"
rpnh net --run "$RUN_DIR"
rpnh net --run "$RUN_DIR" --view --no-open
```

In the dashboard, switch between Overview, Detailed flow, PetriNet and
Executions. The parallel case visibly forks after `prepare` and joins only
after both reviewers settle. The long case is the clearest timeline example;
timeline playback observes saved checkpoints and does not execute or resume the
task.

To run one scenario with a separately authorized real model:

```bash
: "${EXECUTION_CONFIG:?Set an authorized execution selection}"
LIVE_RUN="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-pattern-live.XXXXXX")/run"
python -m examples.workflow_patterns.run --scenario parallel \
  --execution "$EXECUTION_CONFIG" --run-dir "$LIVE_RUN"
```

This may make paid external calls. Each Agent instruction requires its exact
Located inputs to be read before publication. The deterministic fixture follows
the same tool boundary, but its canned text is protocol test data, not evidence
of language-model reasoning. See the
[dashboard guide](../../docs/guides/viewer.md) for every view, control and
visual symbol.

The checked-in `examples/workflow_patterns/validation.json` records the
sanitized offline acceptance counts for all four scenarios. It contains no
Registry, run identifier, local path, endpoint or credential.
