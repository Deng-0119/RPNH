---
name: rpnh-getting-started
description: "Install, run a no-model example, inspect its evidence, then start and resume a configured session."
metadata:
  document-kind: tutorial
  audience: operator-and-developer
  language: en
  counterpart: getting-started_ZH.md
  revision: "2026-10-13.3"
  status: source-reviewed
  basis: "public source b16ecce83ba603b3c15f1c770bce172d4f44f88e; not a new runtime validation"
---

[English](getting-started.md) | [中文](getting-started_ZH.md)

# Start here: your first RPNH run

New interactive `rpnh` sessions now show a frontend menu. `rpnh init` configures a model and at least one selected frontend; `rpnh --resume PATH` reuses the last interface. See [frontend setup and switching](frontends.md).

[Documentation home](../index.md) · [Command reference](../reference/cli.md) · [Troubleshooting](troubleshooting.md)

This path goes from an installed package to a small registered tool run, its
PetriNet, and an optional model-backed session. Commands are instructions for
your environment. This page was checked against the linked source; it does not
record a new installation, runtime test or provider test.

## 1. Choose and install one source

Use **Linux or WSL2 and Python 3.11+**. Follow [installation](installation.md)
to install one approved checkout or a wheel built from it in a dedicated
virtual environment. Basic needs no Codex or Node. Native macOS and Windows
are not supported by this source candidate.

The distribution is `rpnh-harness`, Python imports use `cpn`, and the user
command is `rpnh`. The source candidate declares `0.1.0rc2`; record the exact
commit or wheel digest as well. The older published rc1 wheel does not contain
all commands and named exports described here. See the [release and version
notes](installation.md#goal-and-prerequisites) before choosing an artifact.

From that environment:

```bash
rpnh --help
rpnh config --help
rpnh examples list
```

These commands do not call a model. The last command lists the installed
export choices and identifies source-only examples. If a command is absent,
check the executable and imported package before going further; see
[installation problems](troubleshooting.md#start-with-the-failing-boundary).

## 2. Run a small example without a model

The `native_plugin` export contains a trusted Python plugin and its inputs.
It demonstrates a registered tool operation, not agent reasoning. Listing is read-only;
exporting copies files; plugin installation changes your environment; `run`
executes the plugin and creates real Registry/run state. It requires the
supported local process and Unix-domain socket permissions.

Use an absent export directory and an absent run directory. Review the exported
plugin before installing it. Keep this shell active so the variables remain set:

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-first.XXXXXX")"
EXAMPLE_ROOT="$DEMO_ROOT/native"
rpnh examples export --example native_plugin --output "$EXAMPLE_ROOT"
# After reviewing the exported source, install into this dedicated environment.
python -m pip install "$EXAMPLE_ROOT/examples/native_plugin"
PLUGIN_CONFIG="$EXAMPLE_ROOT/examples/native_plugin/plugins.json"
rpnh plugins --config "$PLUGIN_CONFIG" inspect
rpnh plugins --config "$PLUGIN_CONFIG" check
RUN_DIR="$DEMO_ROOT/add-run"
rpnh plugins --config "$PLUGIN_CONFIG" run demo/add \
  --input "$EXAMPLE_ROOT/examples/native_plugin/input.json" \
  --run-dir "$RUN_DIR"
```

`inspect` reads installed metadata and selection without loading factories.
`check` imports selected trusted factories and compiles declarations without
invoking the operation. For the stock input, inspect the run response for
`output.value` equal to `5`, a non-null `terminal_evidence_ref`, and zero model
calls. A shell exit or a displayed number alone is not terminal evidence.
See the [native example](../../examples/native_plugin/README.md) for the
complete contract and wheel self-checks.

If export says the destination already exists, choose a new directory; export
protects your edits. If execution is blocked by process/IPC permissions,
retain the error and run the same documented example in a supported environment.
Do not change Registry files to manufacture a completed result.

## 3. Inspect what happened

Using `RUN_DIR` from the preceding step:

```bash
rpnh net --run "$RUN_DIR" --format json
rpnh net --run "$RUN_DIR" --show-resources
rpnh net --run "$RUN_DIR" --result-evidence
rpnh net --run "$RUN_DIR" --view --no-open
```

The final command starts a local read-only Viewer; open its printed loopback
URL in your browser. `--no-open` only suppresses automatic browser launch.
The Viewer observes the Registry, but starting it still creates a server
process/listener. Stop that server when finished.

Use the [Viewer guide](viewer.md) to move between the full PN, agent Overview,
firing details, resources and checkpoints. A full PN shows possible structure;
a selected run's firings show what executed. Version comparison is a separate
view. The business PN models agent operations and handoffs when agents are
present; subordinate execution PNs record harness mechanisms such as file
materialization and workspace finalization. AgentLoop logs are not themselves
execution PNs.

For a next no-provider workflow with agent stages, follow the
[hybrid copy-and-change tutorial](examples.md#change-a-dependency-complete-hybrid-copy).
Its scripted model is a deterministic fixture, not a live-model evaluation.
For a complete agent collaboration project, use the
[project example](../../examples/project_collaboration/README.md) and its
[operator guide](project-entry.md).

## 4. Configure a model when you are ready

In an interactive terminal:

```bash
rpnh init
rpnh config list
rpnh config show
rpnh doctor
```

Choose your provider and exact model, and set the requested credential
variable in your own shell. Keep secret values out of repository files,
command arguments and issue reports. `doctor` checks local configuration
without a provider request; a ready report does not prove credentials,
network or model availability. Use `rpnh doctor --json` for structured output.
For non-interactive setup use the documented catalog flow in
[models](models.md), not the interactive `init` command.

[Configuration](configuration.md) explains profile precedence, runtime limits
and persisted identity. [Provider/model configuration](../PROVIDER_MODEL_CONFIGURATION.md)
explains the catalog fields. Select optional clients using the versions in
[adapters](adapters.md), [OpenCode](opencode.md) or [DSH](dsh.md).

## 5. Start a session and keep its identity

The next command and any submitted task can execute models/tools and incur
provider charges. Run it only with the selected route and work authorized:

```bash
SESSION_DIR="$DEMO_ROOT/session"
rpnh --frontend basic --session-dir "$SESSION_DIR"
```

At the RPNH prompt, `/help` lists controls. To launch a child, enter
`/agent` followed by your task. Retain the returned task ID. Use:

```text
/tasks
/task TASK_ID status
/task TASK_ID result
/task TASK_ID checkpoints
```

Replace `TASK_ID` with that exact ID. `result` reads registered output; it does
not wait for work to finish. `/quit` leaves the frontend while independent
children may continue. Use `/task TASK_ID stop` if a checkpoint stop is wanted,
and check the resulting state before treating the task as stopped.

After exiting, reopen the **same session root**:

```bash
rpnh --frontend basic --resume "$SESSION_DIR"
```

Omitting `--execution` on resume uses that session's persisted profile.
`/task TASK_ID resume` continues an owner-stopped child; `reopen CHECKPOINT`
is a different operation that appends a generation from an exact older cut.
If a call is `submission_unknown`, preserve its evidence and do not blindly
repeat it. Follow [checkpoint recovery](checkpoint-recovery.md) for the
state-by-state decision. [Usage](usage.md) covers main `/resume`, main
`/rollback`, workflow targets and switching frontends.

## Where to go next

- Change inputs, graphs or tools: [examples](examples.md), [customization](customization.md)
- Reuse a dependency-complete package: [package tutorial](package-reuse-example.md), [environment preparation](../environment-preparation.md)
- Query and compare authorized sources: [independent Registry reader](independent-registry-reader.md), [cross-net comparison](comparison-context.md)
- Integrate Python or another frontend: [API reference map](../reference/api-index.md), [source map](repository-layout.md)
- Diagnose a failure: [troubleshooting](troubleshooting.md), [safe recovery](checkpoint-recovery.md)

Source correspondence: [installed entry points](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/pyproject.toml),
[CLI and session controls](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh_cli.py),
[example export](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/examples/cli.py),
[export catalog](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/examples/catalog.json),
[plugin command effects](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/cli.py).
