# RPNH Harness

English | [中文](README_ZH.md)

RPNH is a provider-neutral agent execution framework for combining language
models, native tools and reusable workflows.

A durable **Registry** records execution and resource history. A typed
**PetriNet** controls when work can run: declared inputs and settled outputs
enable later steps, including parallel branches and joins. Frontends and the
read-only Viewer inspect the same runtime state rather than owning separate
execution or recovery logic.

## What you can do

- Combine agents and existing code in explicit workflows.
- Create independent child tasks, each with its own Registry and result.
- Track versioned workspace inputs and outputs; inspect runs in the PetriNet Viewer.
- Resume owner-stopped work or reopen a selected committed checkpoint while
  preserving prior history. This does not undo external effects.

## Quick start

Use **Linux or WSL2, Python 3.11+**, and a shell with local process and Unix-socket
permissions. This source-based example runs a native addition tool without a
model or API credential; dependency installation may access package indexes.

```bash
git clone https://github.com/Deng-0119/RPNH.git
cd RPNH
git rev-parse HEAD
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python -m pip install ./examples/native_plugin
rpnh --help
RUN_DIR="$(mktemp -d)/add-run"
rpnh plugins --config examples/native_plugin/plugins.json run demo/add \
  --input examples/native_plugin/input.json --run-dir "$RUN_DIR"
rpnh net --run "$RUN_DIR" --view --no-open
```

The tool returns `output.value: 5` and a terminal-evidence reference. The final
command prints a local read-only dashboard URL. Keep the generated run directory
to inspect it again.

For model-backed conversation, configure your own provider and exact model using
the [model guide](docs/guides/models.md), then run `rpnh --frontend basic`.
No provider is preselected. See [workflow examples](examples/workflow_patterns/README.md)
for parallelism and joins without model credentials.

## Compatibility and releases

Current source retains version `0.1.0rc3`, but the October 30
[v0.1.0rc3 wheel and DSH plugin](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3)
do **not** include the November 1 source fixes; see the
[source/release boundary](docs/validation/host-adapter-source-20261101.md).

- **Codex:** default 0.163.0; 0.166.0 is an explicitly selected candidate.
- **OpenCode:** default 1.19.4; 2.2.5 is only an opt-in REST/authentication/non-durable events
  subset, not a complete native frontend.
- **Stock DSH plugin:** manual pairing with DSH 0.2.2-alpha.2; not a qualified
  managed upgrade. Follow the [operator guide](integrations/dsh/operator/README.md).

These integrations have bounded compatibility, not blanket support across versions.

## Learn more

[Documentation](docs/index.md) · [Examples](examples/README.md) ·
[Architecture](docs/architecture/design.md) · [Contributing](CONTRIBUTING.md)

## License

[MIT](LICENSE). Third-party components retain their own licenses; see
[third-party notices](THIRD_PARTY_NOTICES.md).
