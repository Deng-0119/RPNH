---
name: rpnh-environment-preparation
description: "Check, resolve, prepare and launch a selected receiver environment through the existing RPNH owner."
metadata:
  document-kind: reference-guide
  audience: operator-and-developer
  language: en
  counterpart: environment-preparation_ZH.md
  revision: "2026-10-07.1"
  status: source-reviewed-pre-release
---

[English](environment-preparation.md) | [中文](environment-preparation_ZH.md)

# Receiver environment preparation

This receiver-side application layer keeps package material, local preparation,
and Registry business execution separate. See [portable environment declarations](guides/package-environments.md)
for the v2 author contract and exact package identity.

## Supported boundaries

- `check_environment` reads the selected interpreter's Python/distribution metadata,
  including its actual active transitive dependencies. No installation, lowering,
  compilation, Registry writer, plugin registration factory or provider request runs
- A selected receiver-trusted read-only adapter can inspect local capabilities,
  tools, plugin metadata, configuration and credential *references*. These are
  executable probes, unlike inert package preview
- `resolve_local_wheels` reads explicitly supplied local wheel metadata and hashes.
  It checks wheel tags and `Requires-Python`, resolves a bounded concrete dependency
  closure, and performs no download, source build or install. Unsupported/conflicting
  graphs stay unresolved rather than trying a hidden alternate environment
- `plan_environment` is pure data. Existing-environment replacements require an
  explicit resolution choice; every exact artifact and destination is in the plan
- `prepare_environment` requires a trusted in-process authorization callback over
  the exact plan digest, action inventory and target. An `approved` JSON field is
  rejected. New venv paths must be absent; partial directories/actions survive
  cancellation and failure. No original user environment is deleted
- Preparation runs the actual installed trusted HOST inside the selected Python,
  repeats the same checks, assembles actual Registration declarations and compiles
  the package's verified Module bytes. It does not perform a business operation
- A separately authorized `launch_package` revalidates material, binding, installed
  identities, configuration and HOST declarations, then uses the existing
  `start_run`, `OwnerEventLoop`, `ExecutionServices` and `Orchestrator`. Dispatch
  rechecks detect drift without silently changing locks or installing replacements

A venv can be created successfully while system/tool requirements still block
preparation. A cancelled not-yet-created venv is checked as a missing target; its
base interpreter is never represented as the prepared environment.

## Start with a complete runnable example

The [native-add v2 tutorial](guides/package-reuse-example.md) creates every input
used below: `ROOT.zip`, the exact package lock, `SELECTION.json`, and `OWNER.json`.
It covers an existing environment and a new venv, explicit preparation/launch
approval, and the genuine registered result. Use it for a first run; the generic
workflow below is a reference for materials you already possess.

## Installed command workflow

Use installed `rpnh package` commands. Every material-bearing command accepts
`--archive ROOT.zip --lock PACKAGE_LOCK.json [--local-package DEP.zip] --entry main`.

1. `check-environment --selection SELECTION.json --output CHECK.json`
2. `resolve-environment --selection SELECTION.json --check CHECK.json --wheel EXACT.whl --output RESOLUTION.json`
3. `plan-environment --selection SELECTION.json --check CHECK.json --resolved-selections RESOLUTION.json --wheel EXACT.whl --output PLAN.json`
4. `prepare-environment --selection SELECTION.json --check CHECK.json --resolved-selections RESOLUTION.json --plan PLAN.json --state-dir PRIVATE_DIRECTORY --output RECEIPT.json`
5. `run --binding RESULT_BINDING.json --receipt RECEIPT.json --resolved-selections RESOLUTION.json --owner-request OWNER.json --run-dir ABSENT_RUN_DIRECTORY`

Repeat `--wheel` for the exact selected wheels. With no wheels, `resolve-environment`
fixes actual observed installed choices. `--allow-existing-changes` explicitly
allows supplied replacement wheels to be selected, including same-version code
replacements; the later plan still needs approval. No default `--yes` bypass is
provided. Interactive preparation displays the exact actions/paths and asks for
the plan digest; run displays its separate owner request and asks for its identity.
Noninteractive callers use the same API with an actual trusted execution context,
not a serialized approval document.

Read-only commands write nothing unless `--output` is explicit. Existing output
files are never overwritten. Standard output is a public status projection;
complete private reports, paths and local references go only to explicitly chosen
files. Preparation saves immutable digest-named plan, resolution, checks, binding
and receipt files with private permissions in `--state-dir`, or by default under
`config_path().parent/environment-preparation/<binding_id>`. This is not a Registry.

To retain the terminal product body, explicitly add `--include-terminal-result`
to `run` and supply a private `--output RUN.json` file. The flag is rejected
without `--output`; standard output remains a public status projection. The
result is read through the existing authorized Registry owner. This option does
not turn a preparation receipt or a process exit into business success.

`--binding` replaces `--selection` when checking an already prepared environment;
also provide its exact `--resolved-selections`. A selection for `new_venv` names an
absolute `base_executable` and absent `prefix`. A prepared binding records actual
interpreter/prefix identity; the launcher preserves venv activation semantics even
when the interpreter executable is a symlink.

## Local-agent / setup-document route

`rpnh package setup-instructions --plan PLAN.json --format text|json --output SETUP`
produces a private receiver document naming the identical target, plan digest,
actions, artifacts, failure retention and acceptance criteria. Giving this document
to another process or agent does not authorize it. After authorized work, the
receiver must run the same checker and actual selected-HOST assembly. An agent's
text assertion or completed checkbox cannot replace observations or grant a run.
A changed plan requires fresh exact approval.

## Trusted HOST and probe extension points

The receiver explicitly selects an installed `host_profile_id`. The separate
`rpnh.environment_hosts` entry-point group maps that ID to a factory returning
`HostProfile`. Only explicit preparation/launch loads this HOST factory. Registration
callables remain trusted Python objects; package JSON cannot provide imports.
The standard driver supports existing registered executor and native plugin paths.
A custom profile may supply existing execution-service/binding factories without
creating a new writer or scheduling loop.

`rpnh.environment_probes` can map the same ID to a separate read-only
`factory(local_selection_or_binding) -> ProbePolicy`. Ordinary checks execute this
receiver-trusted probe factory and each adapter inside the selected interpreter.
They do not execute the HOST or plugin registration factory. Adapter contracts must
remain local/read-only and must not make provider availability requests. Host and
probe entry-point choices, package code and `__init__.py` bytes are fingerprinted;
separately selected probe distributions participate in the profile identity.
The builtin `rpnh-native/v1` supports explicitly selected installed native-plugin
configuration files and metadata.

For a not-yet-installed plugin, a selected exact wheel may include the bounded
inert `.dist-info/rpnh_environment_plugins.json` document:

```json
{"schema_version":"rpnh/installed_plugin_metadata/v1","plugins":[{"plugin_id":"example","version":"1.0","api_contract":"rpnh/plugin/v1","entry_point":"example"}]}
```

The resolver verifies that the named `rpnh.plugins` entry point and implementation
bytes exist in the selected distribution, checks API/version and the author's scoped
distribution link, and binds the selected local configuration file digest. It then
requires real post-install assembly/rechecking; this metadata is not evidence that
an operation succeeded. Wheels without this metadata or opaque configuration
references need an explicitly resolved supported adapter or a manual setup/recheck;
the resolver does not execute a factory merely to discover declarations.

## Evidence, privacy and failure

Reports always say `execution_permitted=false`. Metadata-only installed distributions
have `artifact_digest=null`; matching versions do not prove installed file contents.
Remote availability, live permission, capacity and author implementation identity
remain explicitly unverified. Preparation receipts contain no business-success flag.
Public evidence contains allowlisted target/check/choice summaries and declaration
digests, with no local paths, credential references, raw probe output or binding digest.
The same existing run owner publishes application evidence before admission. Registry
exact references and material digests are different types and cannot be substituted.

Missing owner control transport, including denied AF_UNIX access, is a genuine
runtime boundary. There is no direct-worker, fake runner or alternate transport
fallback. A created owner/Registry or compiled Module is not a business terminal.
Existing run directories are rejected rather than starting another writer; use the
existing explicit owner/resume protocol. Installer, assembly, drift and business
runtime failures retain their distinct stages and genuine partial evidence.

Exit codes: `0` command contract completed; `2` argument/contract error; `3` missing,
unresolved or incompatible environment; `4` authorization/incomplete preparation;
`5` drift/interruption. The Python launcher returns an `ExistingRunHandle` only after its genuine owner/control
socket exists. `handle.wait()` returns existing run/task/net and terminal/result facts;
`handle.request_stop()` signals that same owner. The CLI waits on the handle.
Once handed to the existing owner, its real stop/terminal
semantics apply. Automated tests are offline; real provider calls require separate
explicit authorization.

The existing `execution_environment_identity/v1` (`research-exp`) record remains
conditional on the corresponding optional numerical/workspace HOST binding. A
pure-native profile does not manufacture a duplicate record. Its actual HOST and
worker observations do not substitute for testing that separate optional path.
