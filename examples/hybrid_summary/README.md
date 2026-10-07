# Hybrid summary workflow

English | [中文](README_ZH.md)

Obtain the complete editable closure from installed RPNH:

```bash
rpnh examples export --example hybrid_summary --output /absolute/absent/my-hybrid
cd /absolute/absent/my-hybrid
python -m pip install -e ./examples/native_plugin
```

Keep `examples/_support` and `examples/native_plugin` beside this directory.
Copying `hybrid_summary` alone is not sufficient. The same commands also work
from a source checkout root. Edit `input.txt`, `graph.json`, or the installed
editable plugin handler. The scripted fixture understands only the included
task protocol: arbitrary prompt/graph changes may require editing
`examples/_support/scripted_model.py` or explicitly authorizing a live profile.
Scripted runs still require supported local process/IPC permissions.

This three-node DAG reads `graph.json` and executes it through
`AgentWorkflowGraph` and `run_agent_task`:

```text
scripted/model normalize -> demo/summarize -> scripted/model explain
```

This actual Detailed flow view comes from the completed deterministic run. It
shows the two Agent nodes around the native `demo/summarize` operation and the
registered handoffs between them.

![Completed hybrid calculation flow](assets/hybrid-summary-flow.png)

After installing RPNH and `examples/native_plugin`, run the deterministic mode:

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-hybrid.XXXXXX")"
python examples/hybrid_summary/run.py --run-dir "$DEMO_ROOT/run"
rpnh net --run "$DEMO_ROOT/run" --show-resources
```

Use `--input examples/hybrid_summary/variant-input.txt` for the `39`/`13`
variant. To use an authorized real route, add
`--execution /absolute/path/to/execution.json`; no provider or model is
hard-coded and there is no fallback.

See the [examples guide](https://github.com/Deng-0119/RPNH/blob/main/docs/guides/examples.md) for expected evidence and
the distinction between scripted and real-model modes.
