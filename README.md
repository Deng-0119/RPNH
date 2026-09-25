# RPNH Harness

English | [中文](README_ZH.md)

RPNH is a Registry-governed agent harness with typed PetriNet execution. It
provides a conversational main session, independent single-agent tasks,
Designer-authored graph workflows, user-configured model providers, optional
host adapters, and a read-only PetriNet dashboard.

Registry owns durable identities, records, checkpoints and final results.
PetriNet admission and settlement own execution structure. Frontends, provider
adapters, plugins and the dashboard project those authorities; they do not
replace them.

## Status and platform

This repository is a local public-candidate preparation tree. It is not yet a
published package or hosted service. Runtime support is Linux and WSL2 with
Python 3.11 or newer. Native Windows and macOS are not currently supported.

The unified product currently includes:

- the basic RPNH terminal frontend;
- the pinned Codex compatibility frontend;
- configurable local-process and external provider routes;
- native managed plugins and workspace resources;
- a pinned, optional DSH integration;
- the read-only PetriNet dashboard and host run selectors.

OpenCode frontend support is being developed separately and is not claimed by
this candidate until its implementation and offline acceptance are incorporated.

## Install without calling a model

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
rpnh --help
rpnh config init
rpnh config build
rpnh config build --check
```

The initial provider/model catalog is empty. Add only providers and exact models
you are authorized to use; RPNH does not prescribe a default model.

See [installation](docs/guides/installation.md) and
[provider/model configuration](docs/guides/models.md).

## Use RPNH

Start the dependency-light frontend:

```bash
rpnh --frontend basic
```

Start the pinned Codex presentation after installing its documented dependency:

```bash
rpnh --frontend codex
```

Inspect an existing run without acquiring writer authority:

```bash
rpnh net --run /path/to/run
rpnh net --run /path/to/run --show-resources
rpnh net --run /path/to/run --resources-only
rpnh net --run /path/to/run --view --no-open
```

The default projection hides resource places. Explicit resource views show only
resources declared by that real net; they never fabricate nodes.

## Optional DSH integration

The wheel contains one pinned DSH integration and the `rpnh-dsh` launcher. DSH
uses the same RPNH provider input port, Registry grants, managed tools and result
accounting as other hosts; it does not implement a private provider stack.

```bash
rpnh-dsh --help
rpnh-dsh ../deepseek-harness-rpnh \
  --offline --root "$HOME/.rpnh/dsh" \
  --data-file numbers.json --task "Read the numbers and compute their sum"

rpnh-dsh ../deepseek-harness-rpnh \
  --execution /absolute/path/to/selection.json \
  --root "$HOME/.rpnh/dsh" --task "Reply with READY."
```

Read [DSH usage](docs/guides/dsh.md) and
[adapter guidance](docs/guides/adapters.md) before enabling it.

## Documentation

- [Documentation index](docs/index.md)
- [Installation](docs/guides/installation.md)
- [Provider and model configuration](docs/guides/models.md)
- [Usage, tasks, workflows and recovery](docs/guides/usage.md)
- [PetriNet dashboard](docs/guides/viewer.md)
- [Host adapters](docs/guides/adapters.md)
- [Customization and plugins](docs/guides/customization.md)
- [Troubleshooting](docs/guides/troubleshooting.md)
- [Architecture](docs/architecture/design.md)
- [Runtime and Registry reference](docs/reference/runtime-registry.md)

The [Chinese documentation index](docs/index_ZH.md) mirrors the English entry.

## Development and validation

Automated tests are deterministic and offline. A passing offline suite does not
prove that a user-supplied provider route is reachable. Real model tests require
a separate authorization, exact profile/model/route recording, physical-call
budget and private evidence directory.

Do not commit credentials, generated profiles, Registry databases, run outputs,
provider transcripts or local absolute paths. See
[development](docs/guides/development.md).

## License

RPNH is released under the [MIT License](LICENSE). Bundled or pinned third-party
components retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
and the notices shipped with optional integrations.
