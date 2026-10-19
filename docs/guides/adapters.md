---
name: rpnh-optional-adapters
description: "Use Codex and unified DSH presentation, or the advanced legacy DSH host."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: adapters_ZH.md
  revision: "2026-10-19.2"
  status: source-reviewed
  basis: "core; adapter differences explicitly labelled"
---

[English](adapters.md) | [中文](adapters_ZH.md)

# Codex, DSH and coexistence

## Codex is presentation, not replacement authority

The pinned stock client is `codex-cli 0.163.0`. Installing a newer client is not a compatibility test. The supported installation command for this pin is:

```bash
npm install -g @openai/codex@0.163.0
codex --version
```

Set `RPNH_CODEX_BIN` to the stock executable if `codex` on `PATH` is a
local wrapper that adds backend options. In particular, Codex 0.163.0 rejects
`--add-dir` with `--remote`; additional workspace roots belong to the RPNH
server. The binary override selects the client only and does not change the
provider. See the [dated native checks](../validation/frontends-20261019.md).

The RPNH launcher uses its compatibility server and existing main-session authority. `rpnh --frontend codex` begins a session and may execute models after input; it is not an offline smoke command. Use `rpnh --frontend basic` for the documented RPNH-specific controls. A Codex model picker is an RPNH configuration projection, not permission to rewrite Codex provider settings or select a different route silently. The optional local-process subscription bridge is a separate provider path, not implied by choosing the TUI.

At `thread/start`, `config.model_reasoning_effort` selects only a registered
RPNH model/effort profile. When effort is omitted or null, the active model
retains the active selection and a different model uses its catalog default.
An explicit registered effort overrides either choice.
Top-level `effort` remains a legacy input. Conflicting non-null values and
invalid types are rejected before session admission. Other `config` keys remain
ignored and cannot change provider or permission settings. This request-decoding
behavior is covered by offline handler tests; it does not expand the client pin.

The subscription bridge gives each firing a temporary private Codex home and
runtime directory.  Authentication remains a reference to the configured
source rather than a credential copy; private directories use `0700` and are
removed with the firing.  This makes local state writable under a read-only
parent policy but does not bypass the Codex sandbox.  The Linux owner and
user-namespace environment must still support the selected Codex sandbox;
otherwise the adapter fails before a terminal model response and retains its
private failure audit.

Codex receives the same direct MainSession root as Basic, OpenCode and DSH Web. Use
`--session-dir ROOT` once, then `--resume ROOT` from another frontend only after
the current owner exits. Codex protocol thread IDs and `.frontends/codex.json`
are rebuildable presentation metadata; the Registry transcript is authoritative.

## DSH Web frontend

Use the shared main session in a browser:

```bash
rpnh config frontend dsh --dsh-source /absolute/path/to/deepseek-harness
rpnh --session-dir ./main-session
# After exiting this frontend, reopen the same root in Basic:
rpnh --frontend basic --resume ./main-session
```

An explicit `--frontend` overrides the saved preference; unset preferences use
`auto`, which still selects only compatible Codex or Basic. `DSH_SOURCE`
overrides the saved checkout. Preferences are independent of model configuration
and can be saved before model setup. `rpnh --frontend dsh --prompt TEXT` is
rejected before model resolution.

The launcher prints a loopback URL and attempts to open a browser. This RPNH Web
frontend uses native DSH chat/composer/theme widgets and AgentLoop/Session; it
is not the full stock DSH Web shell or a TUI. The pinned upstream bundles no
terminal TUI. Native DSH providers, tools and workspace filesystem are not
loaded. The RPNH catalog, controls and Registry history are shared, with one
writable owner. Viewing reopened history never continues work automatically;
use `/rpnh-resume` or `/rpnh-rollback` explicitly. `Ctrl+C` exits the host and
checkpoint-stops its owned main turn only.

The launcher applies the factory seam and builds Web assets on every launch
before acquiring MainSession. See the [DSH guide](dsh.md) for pinned source,
Node/pnpm requirements and the separate
[unified frontend validation](../validation/dsh-unified-20261019.md).

## Legacy registered headless DSH host prerequisites

`rpnh-dsh` retains separate `--root`/`--session-id` semantics. The following
launcher, 68 MiB frame, 48-attempt budget and recovery details describe that
advanced legacy host, not the unified MainSession frontend.

This section applies only to a source tree or installed distribution containing `cpn/dsh` and `integrations/dsh`. The integration manifest pins upstream `deepseek-ai/deepseek-harness` revision `687f282e8625c6b8d11ff58211a90524be2e4317` (`0.2.1-rc.2`), checked on 2026-10-19. This source host was tested with Node 22.22.1 and pnpm 11.28.5; its engine range is `^22.19.0 || >=24.0.0`. The [DSH guide](dsh.md) distinguishes npm `latest` from `next` and this exact source integration.

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

## Legacy host: explicit offline conformance run

This invokes the deterministic host and creates real private Registry data, but no external model. Use a fresh output directory:

```bash
: "${DSH_SOURCE:?Set the pinned upstream checkout}"
printf '[1,2,3]\n' > numbers.json
rpnh-dsh "$DSH_SOURCE" \
  --offline --root ./dsh-runs --data-file ./numbers.json \
  --task 'Compute the sum of the supplied numbers.' --json
```

The exact offline route is `rpnh-offline/deterministic-v1`. It exposes `read_dataset` and `sum_values` and requires finite numeric input. This demonstration is not a general analytics agent. `--json` requests an upstream headless JSON event stream, not one JSON document. `--deny` exercises the declared request-denial path on a new run. Offline mode does not fall back to a saved provider.

## Legacy host: configured ordinary text

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

The launcher sends a public profile summary. The configured child Registry records route/model attribution and configuration revision, not endpoint, credential binding or static header values. This is not a guarantee that task text or responses contain no sensitive information: preserve runtime data privately. The entire request/response frame is bounded at 68 MiB. The request, history and selected maximum response must pass the pre-dispatch bound; setting the response cap to 68 MiB alone does not guarantee that the enclosing frame fits. A general profile with a larger `max_response_bytes` remains valid for core RPNH but is not DSH-compatible. Build an explicit user-owned DSH profile for the same provider, credential route and exact model with enough frame headroom; do not silently rewrite the selected profile.

New legacy host DSH sessions default to a 48-attempt module budget. The launcher accepts a
positive `--attempt-budget` or the explicit value `unmetered`; the latter is
intended for a known protocol such as the AutomationBench adapter that can
legitimately exceed 48 model/tool turns. Resume keeps the persisted declaration
and does not accept a new budget.

A rejection before Registry turn admission is now emitted as an explicit
headless error and nonzero exit, rather than an empty final message. It still
creates no provider call and must not be reported as a model failure.

## Legacy host: history and explicit recovery

Record the returned session ID. History uses `--history --root DIR --session-id ID` without model selection or credentials; it constructs no model/tool effect host. The installed runner still needs its normal host environment and may prepare the checkout. History is read-only with respect to Registry authority, not a claim of no filesystem or process effects.

Offline recovery uses `--offline --resume --root DIR --session-id ID`. Configured recovery requires `--execution ORIGINAL_SELECTION --resume --root DIR --session-id ID`; it must not silently take the current default. Neither mode accepts a new task, data-file or deny argument while recovering.

A configured clean stop before registering a model attempt is covered by local
recovery tests. The shared operation layer also records one exact completion
after output validation. If the process stops after that event but before
firing settlement, resume settles the completion without another provider or
tool call. Without the event it fails closed before advancing the writer epoch.
Outcomes with declared HOST effects remain excluded. A workspace-bound outcome
is eligible only when the completion names its exact immutable workspace
candidate and that candidate is held by a `map_ready` subordinate execution
checkpoint; recovery validates the archive instead of rescanning the live tree.
An empty writer-epoch gap is harmless; a later writer fact invalidates the old
completion. Registration and immutable resume material are checked before a
new writer opens. Managed tool arguments are schema-checked before execution,
their declared result limit uses the shared 16 MiB `PluginOperation` ceiling,
and durable plugin failures
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
availability. Use `rpnh --frontend dsh` for the shared-session Web frontend;
`rpnh-dsh` remains the separate advanced headless host described in the
[DSH guide](dsh.md). See the
[extension reference](../reference/extensions-observation.md) for plugin and
observer contracts.
