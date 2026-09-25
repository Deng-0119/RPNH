---
name: rpnh-install
description: "Install the core and distinguish optional frontend and source integration paths."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: installation_ZH.md
  revision: "2026-09-24.1"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](installation.md) | [中文](installation_ZH.md)

# Installation and distribution scope

## Goal and prerequisites
Install an explicitly selected RPNH source tree or wheel without calling a model. Runtime support remains **Linux, including WSL2**; native Windows and macOS are not supported by this candidate. Python is declared as `>=3.11`; this is not a claim that every Python/platform combination was tested. The runtime uses Unix sockets, POSIX process control and Linux-specific process inspection. A basic terminal does not require Codex or Node.

The current distribution is named `rpnh-harness`, version `0.1.0`, with imports under `cpn` and user entry `rpnh`. Required libraries are `jsonschema>=4.20,<5` and `websockets>=12,<16`. There are **no `codex` or `dsh` pip extras**. Do not install differently sourced copies of `cpn` into the same environment.

Public repository, release artifact and package-index coordinates are **not yet assigned in these documents**. Obtain the approved source/archive or wheel from the maintainer. The commands below deliberately do not require access to a private repository or claim that a public package exists.

## Install from an approved source tree
Run these Bash commands from its root (the directory containing `pyproject.toml`):

```bash
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
"$TEST_ROOT/venv/bin/python" -c 'import cpn; print(cpn.__file__)'
```

The printed package path must be in this environment's `site-packages`, not the checkout. Use `scripts/check_installed_docs.py` from the source bundle for the guarded configuration smoke described in [development](development.md). That smoke does not validate a model or an interactive terminal.

## Select the installation surface

| Surface | What must be present | Entry and current limitation |
|---|---|---|
| Core/basic | Approved Python package | `rpnh --frontend basic`; selected model required for conversation |
| Codex presentation | Python core plus exactly `codex-cli 0.155.0` | `rpnh --frontend codex`; TUI compatibility is version-specific |
| Native plugin/onboarding overlay | Source containing `cpn/plugins` and `cpn/rpnh/onboarding.py` | Adds `rpnh plugins`, `rpnh init`, `rpnh doctor`, and frontend `auto`; these are not on the reviewed core main |
| Managed DSH | DSH-capable package, bundled `integrations/dsh`, pinned upstream checkout and toolchain | `rpnh-dsh`; explicit offline numeric or shared configured text mode |
| Coexistence | One unified source/wheel with applicable optional hosts | Do not overlay multiple same-name wheels in one environment |

The core main defaults to `codex`; the reviewed adapter overlay defaults to `auto`. Explicit `--frontend basic` works across those configurations. See [adapters](adapters.md) before using optional-host commands. Enhanced viewer work is not automatically part of this documentation candidate.

## Verification, update and removal
Installation is successful only after the installed command, packaged schemas/config/static assets and the zero-model configuration flow work outside the checkout. A successful editable install or passing link check is insufficient. This documentation batch records wheel verification separately from its static-site checks.

For an update, checkpoint-stop affected tasks, retain a consistent private backup of session/run roots and the canonical catalog, then install the approved wheel into a new environment. Rebuild profiles from the catalog and review exact model identity before resuming. Schema compatibility must be verified; do not repair an old Registry by changing JSON version strings or resetting writer locks.

`python -m pip uninstall rpnh-harness` removes the distribution, not user catalogs, credentials held in the shell, or session/run data. Retain these deliberately. Do not delete active run directories. Roll back an environment using the prior approved artifact; that does not undo external effects or authorize opening an incompatible Registry.

## Source correspondence
`pyproject.toml`; `cpn/rpnh_cli.py:_parser`; `cpn/rpnh/user_config.py`; adapter `integrations/dsh/run.sh`. Installation commands are instructions; the progress record identifies which were actually executed.
