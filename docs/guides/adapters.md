---
name: rpnh-optional-adapters
description: "Use versioned Codex presentation and the bounded managed DSH integration."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: adapters_ZH.md
  revision: "2026-09-25.1"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](adapters.md) | [中文](adapters_ZH.md)

# Codex, DSH and coexistence

## Codex is presentation, not replacement authority
The pinned stock client is `codex-cli 0.155.0`. Installing a newer client is not a compatibility test. The supported installation command for this pin is:

```bash
npm install -g @openai/codex@0.155.0
codex --version
```

The RPNH launcher uses its compatibility server and existing main-session authority. `rpnh --frontend codex` begins a session and may execute models after input; it is not an offline smoke command. Use `rpnh --frontend basic` for the documented RPNH-specific controls. A Codex model picker is an RPNH configuration projection, not permission to rewrite Codex provider settings or select a different route silently. The optional local-process subscription bridge is a separate provider path, not implied by choosing the TUI.

## Managed DSH prerequisites
This section applies only to a source tree or installed distribution containing `cpn/dsh` and `integrations/dsh`. The integration manifest pins upstream `deepseek-ai/deepseek-harness` revision `ddefc45fbc7f8e46dd73185e68295696d1297887` (`0.1.6-alpha.2`). It records Node 24 as previously tested, engine range `^22.19.0 || >=24.0.0`, and pnpm `11.7.0`. These are integration pins, not claims about the latest upstream release.

Obtain that exact upstream checkout and install its dependencies with the pinned package manager and frozen lockfile. Use a disposable dedicated checkout: preparation intentionally patches a checked upstream factory and copies the integration TypeScript files. Retain `UPSTREAM.json` and `UPSTREAM_LICENSE` when distributing the integration.

After installing the DSH-capable package, set `DSH_SOURCE` to that checkout:

```bash
: "${DSH_SOURCE:?Set the pinned upstream checkout}"
rpnh-dsh --help
rpnh-dsh "$DSH_SOURCE" --offline --help
```

The first command only shows the Python launcher's help. The second prepares the upstream checkout and shows application help without resolving a configured model or starting a task. Preparation checks the upstream revision, Node range and installed `tsx`; it **writes to the upstream checkout**. `rpnh-dsh "$DSH_SOURCE" --help` without `--offline` can resolve the saved profile before application help and is not the configuration-independent help check.

Prefer the installed launcher. For source-level preparation, the shell scripts now require an absolute Python interpreter as their first argument, followed by the checkout:

```bash
: "${DSH_SOURCE:?Set the pinned upstream checkout}"
RPNH_PYTHON=$(python -c 'import sys; print(sys.executable)')
bash integrations/dsh/prepare.sh "$RPNH_PYTHON" "$DSH_SOURCE"
bash integrations/dsh/run.sh "$RPNH_PYTHON" "$DSH_SOURCE" --offline --help
```

That interpreter must belong to the intended RPNH environment. The launcher passes its own `sys.executable` through the runner to the owner process. `--execution-path`, `--execution-profile` and `--python` are internal launcher-to-application fields, not a second user configuration interface. Invoke configured requests through `rpnh-dsh`, not by hand-building those fields.

## Explicit offline conformance run
This invokes the deterministic host and creates real private Registry data, but no external model. Use a fresh output directory:

```bash
: "${DSH_SOURCE:?Set the pinned upstream checkout}"
printf '[1,2,3]\n' > numbers.json
rpnh-dsh "$DSH_SOURCE" \
  --offline --root ./dsh-runs --data-file ./numbers.json \
  --task 'Compute the sum of the supplied numbers.' --json
```

The exact offline route is `rpnh-offline/deterministic-v1`. It exposes `read_dataset` and `sum_values` and requires finite numeric input. This demonstration is not a general analytics agent. `--json` requests an upstream headless JSON event stream, not one JSON document. `--deny` exercises the declared request-denial path on a new run. Offline mode does not fall back to a saved provider.

## Configured ordinary text
The configured path is implemented, not an outstanding numeric-only limitation. Resolve an RPNH selection in this order: explicit `--execution`, `RPNH_EXECUTION_CONFIG`, then the saved selection. There is no interactive setup in this launcher, no implicit fallback to offline, and no automatic change of the saved default. Build/select the profile through the existing RPNH configuration interface first.

```bash
: "${DSH_SOURCE:?Set the pinned upstream checkout}"
: "${EXECUTION:?Set the existing approved execution selection file}"
rpnh-dsh "$DSH_SOURCE" --execution "$EXECUTION" \
  --root ./dsh-configured-runs --task 'Reply with READY.'
```

This is an execution command and may contact the selected provider; it is not a zero-model smoke. External-provider and local-process selections use the shared Python adapter factory, exact route, credentials, recovery policy and Registry accounting. DSH converts host request/response DTOs through `registered_llm/v1`; it does not implement another provider transport.

Configured mode supports ordinary text and explicitly selected pure
native-plugin operations. Provider configuration grants no tool. Pass an
absolute `--plugin-config` together with one or more
`--managed-tool NAME=PLUGIN/OPERATION` arguments; only those exact operations
are declared and admitted. Installed tools are not discovered implicitly, and
arbitrary DSH/MCP tools remain unavailable. Configured mode rejects
`--data-file`; `--offline` and `--execution` are mutually exclusive. The
internal v2 envelope may carry an empty `data` array; users do not need to
supply a numeric snapshot for text requests.

The launcher sends a public profile summary. The configured child Registry records route/model attribution and configuration revision, not endpoint, credential binding or static header values. This is not a guarantee that task text or responses contain no sensitive information: preserve runtime data privately. The entire request/response frame is bounded at 2 MiB. The request, history and selected maximum response must pass the pre-dispatch bound; setting the response cap to 2 MiB alone does not guarantee that the enclosing frame fits.

## History and explicit recovery
Record the returned session ID. History uses `--history --root DIR --session-id ID` without model selection or credentials; it constructs no model/tool effect host. The installed runner still needs its normal host environment and may prepare the checkout. History is read-only with respect to Registry authority, not a claim of no filesystem or process effects.

Offline recovery uses `--offline --resume --root DIR --session-id ID`. Configured recovery requires `--execution ORIGINAL_SELECTION --resume --root DIR --session-id ID`; it must not silently take the current default. Neither mode accepts a new task, data-file or deny argument while recovering.

A configured clean stop before registering a model attempt is covered by local
recovery tests. The shared operation layer also records one exact completion
after output validation. If the process stops after that event but before
firing settlement, resume settles the completion without another provider or
tool call. Without the event it fails closed before advancing the writer epoch.
Only effect-free, workspace-free outcomes use this automatic settlement path.
An empty writer-epoch gap is harmless; a later writer fact invalidates the old
completion. Registration and immutable resume material are checked before a
new writer opens. Managed tool arguments are schema-checked before execution,
their declared result limit is capped at 64 KiB, and durable plugin failures
return as correlated error tool results. The inspector also requires the worst
declared result plus the next model response to fit before starting a worker.
Do not erase state, change the selected identity, or wrap the command in a
retry. Reusing a provider's configured bounded recovery policy is not the same
capability as resuming a persisted interrupted firing. Resume must repeat the
same managed plugin catalog and allowlist when those were used.

## Closed-loop integration and coexistence

Core, native plugins, Codex compatibility and DSH coexist in this source tree.
They share one profile selector, provider factory, Registry authority and
registered-host boundary. DSH observations remain candidates until registered
products, Success and terminal evidence settle through the sole owner. Do not
run multiple owners against one Registry.

Offline and fake-port tests establish adapter contracts, not live provider
availability. No `rpnh --frontend dsh` switch exists; use the `rpnh-dsh`
launcher described in the [DSH guide](dsh.md). See the
[extension reference](../reference/extensions-observation.md) for plugin and
observer contracts.
