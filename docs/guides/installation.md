---
name: rpnh-install
description: "Install unified RPNH and distinguish its optional presentation and host integration paths."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: installation_ZH.md
  revision: "2026-11-24.1"
  status: source-installation
  basis: "unified main; host differences explicitly labelled"
---

[English](installation.md) | [中文](installation_ZH.md)

# Installation and distribution scope

## Install from npm (recommended)

For the public RC9 prerelease, use **Node.js 20+ with npm**, `uv` on PATH, and **persistent CPython 3.12.13 with `venv`** available as `python3` on PATH. This installation path covers **WSL2 Linux x86_64 / Basic**.

```bash
npm exec --package=rpnh-bootstrap@0.1.0-rc.9 -- rpnh
```

Run in a terminal. Confirm npm's first download if prompted, then type `UPDATE` to install and activate Basic. The original `rpnh init` starts automatically. Enter your provider and exact model configuration, then choose `basic`. Initialization saves configuration without signing in or calling a model. Use the managed `rpnh` launcher path printed after activation for later commands.

RC9 is published on the npm `next` channel. The command above pins the prerelease verified in a fresh anonymous installation.

To install the command globally:

```bash
npm install -g rpnh-bootstrap@0.1.0-rc.9
rpnh
```

## Goal and prerequisites
Install an explicitly selected RPNH source tree or wheel without calling a model. Runtime support remains **Linux, including WSL2**; native Windows and macOS are not supported by this candidate. Python is declared as `>=3.11`; this is not a claim that every Python/platform combination was tested. The runtime uses Unix sockets, POSIX process control and Linux-specific process inspection. A basic terminal does not require Codex or Node.

The distribution is `rpnh-harness`, with imports under `cpn` and command `rpnh`.
The RC9 source declares `0.1.0rc9`. For the published Basic Linux/WSL2 path, see the [RC9 installation and support limits](linux-npm-candidate.md), including the exact npm version and the tested environment. Record whether you installed source, the GitHub wheel or the npm bootstrap.

The earlier [v0.1.0rc3 release](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3) included a Python wheel and stock DSH operator tarball; it predates the November 1 source fixes. See [compatibility](compatibility.md) before using that historical adapter.

Dependencies are `jsonschema>=4.20,<5`, `packaging>=24,<27` and
`websockets>=12,<16`. There are no `codex` or `dsh` pip extras. Do not overlay
differently sourced copies of `cpn` in one environment.

## One-command source installation

Have Python 3.11+ with `venv` and pip bootstrapping, Git, curl and Bash available.
This downloads and executes the [public installer](../../scripts/install.sh);
review it before running. GitHub and package-index access are required, but no
model credentials or model calls are needed.

```bash
bash -c 'set -e; installer=$(mktemp); curl -fsSL https://raw.githubusercontent.com/Deng-0119/RPNH/main/scripts/install.sh -o "$installer"; bash "$installer"; rm -- "$installer"'
```

The installer creates `$HOME/.local/share/rpnh/source` and an isolated
`$HOME/.local/share/rpnh/venv`, installs RPNH plus the native demo plugin, and
checks the installed CLI. It prints the exact source commit. It refuses any
existing destination (including a symlink), uses no sudo, and does not edit
system Python, PATH or shell startup files. A failed installation is retained
for diagnosis; choose a fresh destination rather than overwriting it.

To inspect before execution, choose another destination or pin a reviewed
commit, download first and run separately:

```bash
INSTALLER="$(mktemp)"
curl -fsSL https://raw.githubusercontent.com/Deng-0119/RPNH/main/scripts/install.sh -o "$INSTALLER" &&
less "$INSTALLER" &&
bash "$INSTALLER" --prefix "$HOME/rpnh-source-install" --ref ab5e4bfc5582870b56e23f9539ccad6a8c4be649
```

The pinned example installs that existing source commit, not a new release.
The script itself is fetched from `main`; review the downloaded bytes. The
one-command default installs public `main` at execution time, not unpublished
development work.

After the default install:

```bash
. "$HOME/.local/share/rpnh/venv/bin/activate"
cd "$HOME/.local/share/rpnh/source"
```

With a custom prefix, substitute it in both paths. Run the
[zero-model addition example](../../README.md#example-add-two-numbers-and-inspect-the-run).
The installer does not set up providers, optional frontends, or live tasks.
If a prerequisite is missing, install it using your normal OS procedure; this
script does not install system packages or change security settings.

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

For the RC9 source, the printed version is `0.1.0rc9`. Verify the selected source revision as well. The printed package path must be in this environment's `site-packages`, not the checkout. Use `scripts/check_installed_docs.py` from the source bundle for the guarded configuration smoke described in [development](development.md). That smoke does not validate a model or an interactive terminal.

A wheel built from RC8 contains named reusable example exports
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

## Optional frontends and hosts

The basic terminal and read-only Viewer are included; basic conversation still
requires an authorized provider and exact model. Use the [model guide](models.md).
Optional clients are not installed by the bootstrap script.

| Surface | Current boundary |
|---|---|
| Codex | Default `0.163.0`; `0.166.0` and RC8’s `0.171.0` are explicit opt-ins. See the [tested scope](compatibility.md). |
| OpenCode | Default `1.19.4`; `2.2.5` is only a [REST/authentication/non-durable event subset](../reference/opencode-v2-candidate.md), not a complete native frontend. |
| Stock DSH operator | Manually pair the RPNH Python companion and operator with DSH `0.2.2-alpha.2`; follow the [operator installation guide](../../integrations/dsh/operator/README.md). No managed-upgrade qualification. |
| DSH Web / headless | Separate from the stock operator. DSH Web defaults to `0.2.2-alpha.2`; RC8’s `0.2.2-rc.5` is an explicit opt-in with scoped evidence. See the [DSH guide](dsh.md) for each path. |

Use [frontend setup and switching](frontends.md) for other presentation clients.
Do not combine old DSH Web setup pins with the stock alpha.2 operator pairing.
These are independent RPNH adaptations, not upstream endorsement or blanket
compatibility. See [compatibility and known limitations](compatibility.md) when choosing an installation.

## Verification, update and removal
Installation is successful only after the installed command, packaged schemas/config/static/example assets and the zero-model configuration flow work outside the checkout. A successful editable install or passing link check is insufficient. Viewer wheel validation requires the complete pinned vendor-file set and license files; an empty or self-reduced asset manifest cannot waive them. This documentation batch records wheel verification separately from its static-site checks.

For an update, checkpoint-stop affected tasks, retain a consistent private backup of session/run roots and the canonical catalog, then install the approved wheel into a new environment. Rebuild profiles from the catalog and review exact model identity before resuming. Schema compatibility must be verified; do not repair an old Registry by changing JSON version strings or resetting writer locks.

`python -m pip uninstall rpnh-harness` removes the distribution, not user catalogs, credentials held in the shell, or session/run data. Retain these deliberately. Do not delete active run directories. Roll back an environment using the prior approved artifact; that does not undo external effects or authorize opening an incompatible Registry.

## Source correspondence
`pyproject.toml`; `cpn/rpnh_cli.py:_parser`; `cpn/rpnh/user_config.py`; `cpn/rpnh/frontend_config.py`; `cpn/frontend/dsh_launcher.py`; `cpn/frontend/dsh_assets.py`; `integrations/dsh/frontend/official_host.ts`, `official_bff.ts`, `setup.ts`; legacy adapter `integrations/dsh/run.sh`. Installation commands are instructions; the progress record identifies which were actually executed.
