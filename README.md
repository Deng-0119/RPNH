> [RPNH v0.1.0rc9](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc9) is a GitHub prerelease. [`rpnh-bootstrap@0.1.0-rc.9`](https://www.npmjs.com/package/rpnh-bootstrap/v/0.1.0-rc.9) provides the command below to install and automatically begin setup for WSL2 x86_64 / Basic.

# RPNH Harness

English | [中文](README_ZH.md)

RPNH is a provider-neutral agent execution framework for **building, running and
reusing workflows that combine language models and native tools**.

A typed **PetriNet** makes task dependencies and resource requirements executable:
a step can run only after its declared prerequisites are satisfied, and settled
outputs feed later work. A durable **Registry** connects workflow definitions,
resource versions, execution records and checkpoints.

[First run](docs/guides/getting-started.md) ·
[Technical report](docs/technical-report.md) · [Examples](examples/README.md) ·
[Documentation](docs/index.md) ·
[Repository map](docs/guides/repository-layout.md)

## Quick start: install from npm

For the public RC9 prerelease, use **Node.js 20+ with npm**, `uv` on PATH, and **persistent CPython 3.12.13 with `venv`** available as `python3` on PATH. This installation path covers **WSL2 Linux x86_64 / Basic**.

```bash
npm exec --package=rpnh-bootstrap -- rpnh
```

Run in a terminal. Confirm npm's first download if prompted, then type `UPDATE` to install and activate Basic. The original `rpnh init` starts automatically. Enter your provider and exact model configuration, then choose `basic`. Initialization saves configuration without signing in or calling a model. Use the managed `rpnh` launcher path printed after activation for later commands.

In a fresh environment this command selects the newest public version through the npm `latest` tag. For this release both `latest` and `next` point to `0.1.0-rc.9`.

To install the command globally:

```bash
npm install -g rpnh-bootstrap
rpnh
```

[Release notes and install scope](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc9).

## What you can do

- **Compose agents, tools and workflow modules.** Define supported workflows
  directly or use the built-in Designer. Connect registered operations through
  typed dependencies, parallel branches and all-input joins, and compose
  reusable modules through explicit interfaces.
- **Coordinate independent tasks through versioned resources.** Run tasks with
  their own Registry and result. Give agents explicit access to the materials
  and resource versions they need for each handoff.
- **Review and merge workflow definitions.** Propose and compare supported
  definition revisions, resolve conflicts, then explicitly adopt a chosen
  revision into a run.
- **Package and reuse a working method.** Share supported definitions with their
  inputs and environment requirements, so another project can prepare and run
  them with its own Registry, local configuration and authorization.

The [technical report](docs/technical-report.md) explains composition,
definition-specific merge contracts and how revisions enter execution.

**Inspect and continue real runs.** The [read-only Viewer](docs/guides/viewer.md)
shows structure, recorded inputs, outputs and workspace versions.
[Resume owner-stopped work](docs/guides/checkpoint-recovery.md) from its latest
committed checkpoint, or explicitly reopen a checkpoint in the active net
lineage. Earlier history is retained; reopening does not undo external effects.

## Alternative: install from source

Requires **Linux/WSL2, Python 3.11+ with venv, Git, curl and Bash**.
[Review the installer](scripts/install.sh), then run:

```bash
bash -c 'set -e; installer=$(mktemp); curl -fsSL https://raw.githubusercontent.com/Deng-0119/RPNH/main/scripts/install.sh -o "$installer"; bash "$installer"; rm -- "$installer"'
```

This installs current public source and the demo plugin into
`~/.local/share/rpnh/{source,venv}`. It refuses an existing destination, uses no
sudo, and changes neither system Python nor shell startup files. Network access
is needed; no model is called. [Custom directory, pinned commit and manual installation](docs/guides/installation.md).

## Example: add two numbers and inspect the run

After the source installation above, run this zero-model example. Local subprocess
and Unix-socket permissions are required:

```bash
cd "$HOME/.local/share/rpnh/source"
. ../venv/bin/activate
RUN_DIR="$(mktemp -d)/add-run"
rpnh plugins --config examples/native_plugin/plugins.json run demo/add \
  --input examples/native_plugin/input.json --run-dir "$RUN_DIR"
rpnh net --run "$RUN_DIR" --view --no-open
```

The input is `2 + 3`; expected output contains `output.value: 5` and a terminal-evidence
reference. The last command prints the local read-only Viewer URL. Keep the run
directory for later inspection.

![Recorded native-add run in the PetriNet Viewer](examples/native_plugin/assets/native-plugin-petrinet.png)

Existing capture of a completed native-tool run, not a model run or a new test
of this revision. [Example details](examples/native_plugin/README.md) ·
[More workflows](examples/workflow_patterns/README.md).

For conversation, configure your provider and exact model with the
[model guide](docs/guides/models.md), then run `rpnh --frontend basic`.
No provider is preselected.

## See RPNH in a complete project

| Goal | Example | What to expect |
|---|---|---|
| Combine agents with existing code | [Hybrid summary](examples/hybrid_summary/README.md) | Agent → native calculation → Agent; scripted by default, with an explicitly selected live-model route |
| Collaborate on and reuse a project | [Project collaboration](examples/project_collaboration/README.md) | Controlled contributors resolve a definition conflict; implementer/reviewer agents execute the task, including an independent receiver run |
| Coordinate independent roles around shared resources | [Multi-user release handoff](examples/multiuser_release_handoff/README.md) | Four task Registries and a separate resource Source; recorded role handoffs and resource versions |

[![Recorded project collaboration business PetriNet: Implementer and Reviewer](examples/project_collaboration/figures/source-petri.png)](examples/project_collaboration/figures/source-petri.png)

**Recorded project run:** an Implementer hands work to a Reviewer in the official
read-only Viewer. The walkthrough connects this execution to definition revisions,
explicit adoption and independent receiver reuse. This is a historical capture;
[the complete case](examples/project_collaboration/README.md) retains its
provenance and validation scope.

## Compatibility and releases

See [RC9 release notes](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc9) for the WSL2 x86_64 / Basic installation scope. The October 30
[v0.1.0rc3 wheel and DSH plugin](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3)
do **not** include the November 1 source fixes; see the
[source/release boundary](docs/guides/compatibility.md).

- **Codex:** default 0.163.0; 0.166.0 and 0.171.0 are explicitly selected candidates.
  The 0.171.0 WSL2 live `batch-summary-v1` task and nonempty same-root reopen
  passed; see [the exact model and validation scope](docs/guides/compatibility.md).
- **OpenCode:** default 1.19.4; 2.2.5 is only an opt-in REST/authentication/non-durable events
  subset, not a complete native frontend. The existing 2.2.6 opt-in supports one
  bound ordinary-text session; it does not grant native tool execution or full-client certification.
- **DSH Web frontend:** default 0.2.2-alpha.2 official Web UI bound to one RPNH
  MainSession; 0.2.2-rc.1 is an existing opt-in. The 0.2.2-rc.5 candidate passed
  scoped WSL2 checks without model calls. A separately approved live task with a
  new input set and same-root read-only reopening passed; the earlier driver-stopped
  unknown submission remains separate historical evidence.
  Follow the [Web guide](docs/guides/dsh.md).
- **Stock DSH operator plugin:** manual pairing with DSH 0.2.2-alpha.2 to
  inspect existing RPNH tasks, with optional controls; not a qualified managed
  upgrade. Follow the [operator guide](integrations/dsh/operator/README.md).

These integrations have bounded compatibility, not blanket support across versions.

## Explicit managed-tool scheduling

[Controlled managed tools](docs/controlled-managed-tools.md) documents opt-in
parallel execution for explicitly bound managed operations under the selected
policy and effect/conflict limits. Mixed builtin/managed turns retain sequential
execution. The shared capacity applies within one run, not as a cross-run lock.
Selecting a policy does not grant additional business authority.

## Learn more

[Documentation](docs/index.md) · [Examples](examples/README.md) ·
[Registry materials and annotated controls](docs/guides/viewer-materials.md) ·
[Technical report](docs/technical-report.md) ·
[Architecture](docs/architecture/design.md) · [Contributing](CONTRIBUTING.md)

## License

[MIT](LICENSE). Third-party components retain their own licenses; see
[third-party notices](THIRD_PARTY_NOTICES.md).
