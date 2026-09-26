# RPNH user examples

English | [中文](README_ZH.md)

Choose an example by the behavior you want to see:

| Goal | Example | Default model boundary |
|---|---|---|
| Local calculation and registered resource | [Native plugin](native_plugin/README.md) | No model |
| Serial model–program–model calculation | [Hybrid summary](hybrid_summary/README.md) | Scripted; optional exact live profile |
| Serial, parallel, document and long workflow shapes | [Workflow pattern gallery](workflow_patterns/README.md) | Scripted; optional exact live profile |
| Two independently managed child tasks | [Task workspace](task_workspace/README.md) | Scripted |
| Definition operations and live replacement | [Native net operations](net_operations/README.md) | Definition-only plus one live task |
| One semantic task through every supported host | `rpnh examples export --output DIR` | User-owned exact live profile |

Each runnable workflow creates a real Registry that can be opened with
`rpnh net --run RUN_DIR --view --no-open`. Scripted fixtures exercise the same
protocol and settlement boundaries but are not claims about model reasoning.

Start with the [complete examples guide](../docs/guides/examples.md) and the
[dashboard guide](../docs/guides/viewer.md). Generated
profiles, Registry directories and provider transcripts stay outside this tree.
