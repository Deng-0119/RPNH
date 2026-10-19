---
name: rpnh-install
description: "Install unified RPNH and distinguish its optional presentation and host integration paths."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: installation_ZH.md
  revision: "2026-10-19.3"
  status: source-candidate-not-published
  basis: "unified main; host differences explicitly labelled"
---

[English](installation.md) | [中文](installation_ZH.md)

# Installation and distribution scope

## Goal and prerequisites
Install an explicitly selected RPNH source tree or wheel without calling a model. Runtime support remains **Linux, including WSL2**; native Windows and macOS are not supported by this candidate. Python is declared as `>=3.11`; this is not a claim that every Python/platform combination was tested. The runtime uses Unix sockets, POSIX process control and Linux-specific process inspection. A basic terminal does not require Codex or Node.

The current distribution is named `rpnh-harness`, version `0.1.0rc2` (development candidate, not a published release), with imports under `cpn` and user entry `rpnh`. Required libraries are `jsonschema>=4.20,<5`, `packaging>=24,<27` and `websockets>=12,<16`. There are **no `codex` or `dsh` pip extras**. Do not install differently sourced copies of `cpn` into the same environment.

The canonical public source repository is [`Deng-0119/RPNH`](https://github.com/Deng-0119/RPNH).
Choose one provenance before installing:

- This reviewed source candidate: build from the exact selected checkout/commit.
  Public `main` may be ahead of the old rc1 release, but an ordinary clone does
  not contain unpublished candidate changes. There is no rc2 release download
  promised by these instructions.
- [`v0.1.0rc1`](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc1): the
  historical 2026-09-29 wheel/sdist, without subsequent `main` changes.
- [`benchmark-baseline-2026-10-06`](https://github.com/Deng-0119/RPNH/releases/tag/benchmark-baseline-2026-10-06):
  source-only at `f58a0f061d1daf4c09fc96c43237c613cc43f439`, preserving the dated
  benchmark record; it does not provide a current runtime wheel.

No package-index publication or blanket runtime acceptance is claimed. Older
source snapshots and the published wheel both used `0.1.0rc1` despite different
code. A version string alone cannot identify that source; `pip install --upgrade`
can leave an already installed same-version wheel unchanged. Use a fresh
virtual environment and record the commit or artifact digest. This source
candidate uses rc2 so its wheel can be distinguished from the older rc1 wheel.

## Install from an approved source tree
Run these Bash commands from its root (the directory containing `pyproject.toml`):

```bash
SOURCE_ROOT="$PWD"
git rev-parse HEAD
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
rpnh --help
rpnh config --help
```

Use `python -m pip install -e '.[test]'` only for development. Dependency installation may access package indexes; it is not a provider/model call. Record the source commit and dependency versions. The basic runtime is the same core, not a second implementation.

## Build and install a wheel outside the source directory
Building requires the separate `build` frontend and the declared setuptools backend. In a prepared build environment:

```bash
python -m pip install build
python -m build --wheel --outdir dist
```

Set `WHEEL` to the absolute path of the wheel just built; it is an operator-supplied path, not a published download address. In a separate directory:

```bash
: "${WHEEL:?Set the absolute path of the approved wheel}"
TEST_ROOT=$(mktemp -d)
python3 -m venv "$TEST_ROOT/venv"
"$TEST_ROOT/venv/bin/python" -m pip install "$WHEEL"
cd "$TEST_ROOT"
"$TEST_ROOT/venv/bin/rpnh" --help
"$TEST_ROOT/venv/bin/python" -c 'import cpn; from importlib.metadata import version; print(version("rpnh-harness")); print(cpn.__file__)'
```

For this candidate, the printed version must be `0.1.0rc2`; `0.1.0rc1` identifies an older distribution. The printed package path must be in this environment's `site-packages`, not the checkout. Use `scripts/check_installed_docs.py` from the source bundle for the guarded configuration smoke described in [development](development.md). That smoke does not validate a model or an interactive terminal.

A wheel built from this source candidate contains named reusable example exports
plus the default provider-neutral cross-host task. Exporting is a zero-model
file operation and does not require the source checkout. The older rc1 wheel
has only the adapter task. For current named exports, use
`--example native_plugin`, `hybrid_summary`, `compose_serial` or `package_reuse`;
see [export and customization](examples.md#export-an-example-and-make-it-yours)
and the [runnable v2 tutorial](package-reuse-example.md).

```bash
"$TEST_ROOT/venv/bin/rpnh" examples list
"$TEST_ROOT/venv/bin/rpnh" examples export --output "$TEST_ROOT/adapter-task"
```

Supplying an execution profile and submitting the exported task is a separate,
potentially paid live operation; follow the bundle's host-specific guide. Each
host directory includes a sanitized prior acceptance summary, not credentials
or a substitute for validating the user's own route.

## Select the installation surface

| Surface | What must be present | Entry and current limitation |
|---|---|---|
| Built-in terminal | Approved unified Python package | `rpnh --frontend basic`; a selected model is still required for conversation |
| Codex presentation | Unified package plus exactly `codex-cli 0.163.0` | `rpnh --frontend codex`; TUI compatibility is version-specific |
| OpenCode presentation | Unified package plus exactly OpenCode `1.19.3` | `rpnh --frontend opencode`; explicit only, Linux/WSL2 only, and UI metrics are unavailable |
| Plugins, onboarding and examples | Approved unified Python package | `rpnh plugins`, `rpnh init`, `rpnh doctor`, and `rpnh examples`; local checks and export do not call a model |
| Official DSH Web | Unified package, pinned DSH `687f282` / `0.2.1-rc.2` source, Node `^22.19.0 \|\| >=24.0.0`, installed pnpm dependencies and 31 pinned official Web packages | `rpnh --frontend dsh`; Linux/WSL2, official UI with native controls for one bound RPNH MainSession |
| Legacy DSH headless host | Unified package, bundled `integrations/dsh`, pinned source and toolchain | `rpnh-dsh`; advanced separate-root host, explicit offline numeric or configured text mode |
| Coexistence | One unified source/wheel with applicable optional hosts | Do not overlay multiple same-name wheels in one environment |

With no saved preference, the unified main uses `auto`: in an interactive
terminal it selects compatible Codex `0.163.0` when available, otherwise Basic;
non-interactive use selects Basic. Explicit `--frontend basic|codex|opencode|dsh`
overrides a saved preference. Save or inspect it independently of model setup:

```bash
rpnh config frontend dsh --dsh-source /absolute/path/to/deepseek-harness
rpnh config frontend
# Restore automatic selection:
rpnh config frontend auto
```

Preferences are stored beside the model config as its filename plus
`.frontend.json`; `DSH_SOURCE` overrides the saved DSH path. Install pnpm
`11.28.5` dependencies in the dedicated pinned checkout with
`pnpm install --frozen-lockfile --ignore-scripts`. First launch downloads 31
exact `0.2.1-rc.2` official npm packages into
`SOURCE/rpnh-frontend/node_modules`; a complete cache can be reused offline.
Setup creates `official.json`, `tsconfig.json`, typed Remote descriptors and a
bound-session client under `rpnh-frontend`, leaving official UI unchanged.
Preparation and asset preflight finish before acquiring MainSession. The
checkout must be writable; it and the downloaded packages are not bundled in
the wheel. The launcher prints an authenticated loopback URL and attempts to
open a browser.

Official index/client plugins and native Web transport connect through a typed
Remote BFF to Python MainSession. Stock DSH AgentLoop, LLM, tools and persistence
are not loaded. Support is limited to text, native model/effort selection and
native slash-menu RPNH controls for the bound session; file uploads, arbitrary
sessions, workspace editing and native DSH local tool execution are unsupported.

See [adapters](adapters.md), [OpenCode](opencode.md) and [DSH](dsh.md) for session
sharing, prerequisites and limits. The separate
[unified DSH record](../validation/dsh-unified-20261019.md) separates the component milestone from official Web E2E results;
these installation instructions do not establish live-provider, all-platform
or wheel acceptance. The read-only Viewer and its packaged assets belong to
the unified distribution and have their own inspection interface.

## Verification, update and removal
Installation is successful only after the installed command, packaged schemas/config/static/example assets and the zero-model configuration flow work outside the checkout. A successful editable install or passing link check is insufficient. Viewer wheel validation requires the complete pinned vendor-file set and license files; an empty or self-reduced asset manifest cannot waive them. This documentation batch records wheel verification separately from its static-site checks.

For an update, checkpoint-stop affected tasks, retain a consistent private backup of session/run roots and the canonical catalog, then install the approved wheel into a new environment. Rebuild profiles from the catalog and review exact model identity before resuming. Schema compatibility must be verified; do not repair an old Registry by changing JSON version strings or resetting writer locks.

`python -m pip uninstall rpnh-harness` removes the distribution, not user catalogs, credentials held in the shell, or session/run data. Retain these deliberately. Do not delete active run directories. Roll back an environment using the prior approved artifact; that does not undo external effects or authorize opening an incompatible Registry.

## Source correspondence
`pyproject.toml`; `cpn/rpnh_cli.py:_parser`; `cpn/rpnh/user_config.py`; `cpn/rpnh/frontend_config.py`; `cpn/frontend/dsh_launcher.py`; `cpn/frontend/dsh_assets.py`; `integrations/dsh/frontend/official_host.ts`, `official_bff.ts`, `setup.ts`; legacy adapter `integrations/dsh/run.sh`. Installation commands are instructions; the progress record identifies which were actually executed.
