# RPNH Harness

English | [中文](README_ZH.md)

RPNH is an agent execution framework for composing language models, native tools
and reusable workflows. It provides a conversational main session, independent
tasks, versioned resources and a shared record of how work proceeds.

Use it to bring existing software into an agent application, organize work
across several steps, and inspect the inputs, resources and results behind each
execution. The same foundation supports applications that are refined manually
or through an application-defined optimization loop.

## See a multi-agent run

This is the dashboard from the checked-in `parallel` example after a completed
Registry run. `prepare` enables two independent Agents; `join` becomes eligible
only after both registered products are available.

![Actual RPNH parallel multi-agent overview](examples/workflow_patterns/assets/parallel-overview.png)

After the source installation below, reproduce it without a provider or API
credential:

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-readme.XXXXXX")"
python -m examples.workflow_patterns.run \
  --scenario parallel --run-dir "$DEMO_ROOT/parallel"
rpnh net --run "$DEMO_ROOT/parallel" --view --no-open
```

Overview emphasizes Agent-to-Agent structure. The same run's PetriNet view
shows every transition, handoff place, fan-out arc and all-input join used by
execution:

![Actual RPNH parallel PetriNet projection](examples/workflow_patterns/assets/parallel-petrinet.png)

The screenshots retain the real graph, aggregate execution state and controls;
run-specific checkpoint identities and local timestamps are omitted from public
images. The dashboard is read-only, and completion still comes from Registry
terminal evidence rather than a picture or process exit.

## Example index

| What to try | What it demonstrates | Complete instructions |
|---|---|---|
| Native calculation | A local plugin, registered input/output and zero model calls | [Native tool](examples/native_plugin/README.md) |
| Model–program–model calculation | Agent nodes around a native summary operation | [Hybrid summary](examples/hybrid_summary/README.md) |
| Serial, parallel, document and long workflows | Multi-Agent topology, bounded parallelism, joins and timeline checkpoints | [Workflow gallery](examples/workflow_patterns/README.md) |
| Independent tasks | Separate child Registries controlled from one main session | [Task workspace](examples/task_workspace/README.md) |
| Native Petri-net operations | Definition composition and a live whole-net replacement | [Net operations](examples/net_operations/README.md) |
| Basic, Codex, DSH and OpenCode | One provider-neutral semantic task through four presentations | [Installed adapter task](docs/guides/examples.md) |

Every example page includes an actual dashboard image and states whether it was
captured from a deterministic local run or a previously authorized live run.
Use the [complete example catalog](docs/guides/examples.md) for expected results,
commands and evidence boundaries, and the [dashboard tutorial](docs/guides/viewer.md)
for every view, control and visual symbol.

## What RPNH brings to an application

- **Combine agents with existing code.** Give language-model nodes reasoning and
  communication responsibilities, and use native plugins for calculations or
  existing business logic. Declare their inputs and outputs in one workflow.
- **Keep work and its results connected.** Registered resource versions link
  inputs and products to their executions. Workspace settlement merges compatible
  changes and records conflicts between concurrent revisions.
- **Manage tasks independently of the conversation.** Launch, inspect, message,
  stop and resume independent tasks. Each task keeps its own Registry, execution
  state and results while the main session provides a common entry point.
- **Inspect the structure that drives execution.** The read-only PetriNet
  dashboard shows workflow structure, execution state and declared resources,
  with overview and detailed views of the same run.
- **Reuse components while choosing models and hosts.** Workflows and native
  plugins share the execution lifecycle. Configure model providers separately
  and choose a basic terminal, a supported presentation frontend or an optional
  host integration.

## Where it fits

| Application | How RPNH helps |
|---|---|
| Data analysis and report generation | Combine model interpretation with programmatic calculations and registered outputs. |
| Multi-step work with files and shared resources | Make dependencies, resource access and workspace revisions explicit as work progresses. |
| Reusable domain-specific agents | Assemble registered components into a workflow that can be inspected and revised. |
| Agent optimization and recursive self-improvement (RSI) research | Provide execution records, resource versions and controlled workflow changes for application-defined candidates, evaluators and selection policies. |

A small native-tool task is a useful starting point. The
[source example catalog](examples/README.md) then provides serial, parallel,
document, calculation, long-process and independent-task paths. Add richer
resource declarations as the application needs them. Task logic, evaluation
criteria and domain-specific policies belong to the application components.

## How execution is organized

**Registry** maintains durable identities, records, resource versions,
checkpoints and registered results. The **typed Petri net** expresses which
operations are eligible to start, which inputs and resources they require, and
how settled outcomes advance the process.

A declared operation is admitted before dispatch. Its returned products are
registered and settled before downstream steps rely on them, and a declared
terminal binding identifies the final result. Frontends and the dashboard use
these same records for interaction and observation.

See the [architecture](docs/architecture/design.md) and
[runtime reference](docs/reference/runtime-registry.md) for the execution model,
workspace settlement and workflow revision mechanisms.

## Start from source

RPNH runs on Linux and WSL2 with Python 3.11 or newer. The repository is in
release-candidate preparation; source installation is the current entry point.

From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
rpnh --help
rpnh config init
rpnh config build
rpnh config build --check
```

These setup commands do not call a model. The initial provider/model catalog is
empty; add your chosen provider and exact model using the
[configuration guide](docs/guides/models.md). See
[installation](docs/guides/installation.md) for environment and build details.

### Try a native tool without a model

The bundled `demo/add` plugin adds two numbers through the RPNH execution path.
Install it in the same environment, then create a fresh run:

```bash
python -m pip install ./examples/native_plugin
rpnh plugins --config examples/native_plugin/plugins.json check
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-demo.XXXXXX")"
RUN_DIR="$DEMO_ROOT/run"
rpnh plugins --config examples/native_plugin/plugins.json run demo/add \
  --input examples/native_plugin/input.json --run-dir "$RUN_DIR"
rpnh net --run "$RUN_DIR" --show-resources
```

The supplied input is `{"left": 2, "right": 3}`. The returned JSON contains
`output.value` equal to `5`, together with the run location and terminal evidence
reference. This executes a local plugin and creates run data. The
[examples guide](docs/guides/examples.md) continues with the registered
instruction resource, model–program–model calculation, serial/parallel/document/
long workflow patterns and independent tasks.
The [customization guide](docs/guides/customization.md) explains the plugin
contract.

### Run one real task through a supported host

The installed distribution contains one provider-neutral task bundle for Basic,
Codex, DSH and OpenCode. Export it without contacting a model:

```bash
EXAMPLE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-example-parent.XXXXXX")/adapter-task"
rpnh examples list
rpnh examples export --output "$EXAMPLE_ROOT"
```

The exported per-host guides use the same task and expected semantic result.
They never configure a provider or choose a model; a call occurs only after you
explicitly supply an authorized execution profile and submit the task. See the
[examples guide](docs/guides/examples.md). The bundle includes separate,
sanitized acceptance evidence for each host; raw runs remain private.

### Start a conversation

After configuring a model, start the dependency-light frontend:

```bash
rpnh --frontend basic
```

Use `/agent` for an independent task, `/workflow` to ask the Designer for a
workflow, and `/tasks` to list children. `/switch` changes the selected task;
`/task ID status` and `/task ID result` inspect its progress and output.
Conversation and task execution use the configured models and tools. See
[usage and recovery](docs/guides/usage.md) for messaging, stopping and resuming.

### Inspect a run

Set `RUN_DIR` to an existing run directory, such as the plugin run above:

```bash
: "${RUN_DIR:?Set an existing run directory}"
rpnh net --run "$RUN_DIR"
rpnh net --run "$RUN_DIR" --resources-only
rpnh net --run "$RUN_DIR" --view --no-open
```

The last command starts the local read-only dashboard and prints its address.
Overview, detailed flow and full PetriNet views help explore the run at different
levels. See the [dashboard guide](docs/guides/viewer.md).

## Frontends and host integrations

| Entry | Use |
|---|---|
| `rpnh --frontend basic` | Built-in terminal for the main session and independent task controls. |
| `rpnh --frontend codex` | Pinned Codex presentation; setup is in the [adapter guide](docs/guides/adapters.md). |
| `rpnh --frontend opencode` | OpenCode 1.18.32 presentation; setup and supported interactions are in the [OpenCode guide](docs/guides/opencode.md). |
| `rpnh-dsh` | Optional pinned DSH host integration; installation and offline/configured modes are in the [DSH guide](docs/guides/dsh.md). |

Model selection and managed execution remain with RPNH across these integrations.
Basic, Codex and OpenCode are sequential presentations of the same direct
MainSession root: create it with `--session-dir`, then reopen that exact path with
`--resume` and another frontend after the first exits. They do not copy Registry
state into frontend-specific sessions, and a shared owner lease rejects concurrent
writable presentations. DSH remains a registered host integration with its own
session surface. Each guide describes its dependencies and supported interaction
surface.

## Documentation

[English index](docs/index.md) · [中文文档](docs/index_ZH.md)

| Read about | Guides |
|---|---|
| Getting started | [Installation](docs/guides/installation.md), [examples](docs/guides/examples.md), [repository map](docs/guides/repository-layout.md), [configuration](docs/guides/configuration.md), [models](docs/guides/models.md), [usage](docs/guides/usage.md) |
| Building an application | [Customization and plugins](docs/guides/customization.md), [native Petri-net operations](docs/guides/net-operations.md), [declaration reference](docs/reference/declarations.md) |
| Understanding a run | [Dashboard](docs/guides/viewer.md), [architecture](docs/architecture/design.md), [runtime and Registry](docs/reference/runtime-registry.md) |
| Operating and contributing | [Troubleshooting](docs/guides/troubleshooting.md), [development](docs/guides/development.md), [release validation](docs/guides/release-validation.md) |

## Development

Use focused deterministic offline tests for code changes. Provider-backed checks
are separately authorized and recorded with their model configuration and call
budget. Keep credentials and generated run data in local runtime directories.
The [development guide](docs/guides/development.md) describes the test and
contribution workflow.

## License

RPNH uses the [MIT License](LICENSE). Bundled or pinned third-party components
retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
and the notices shipped with optional integrations.
