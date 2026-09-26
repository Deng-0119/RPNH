# Hybrid summary workflow

English | [中文](README_ZH.md)

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

See the [examples guide](../../docs/guides/examples.md) for expected evidence and
the distinction between scripted and real-model modes.
