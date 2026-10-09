---
name: rpnh-extension-observer-reference
description: "Reference native plugins, adapter lifecycle and read-only net projections."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: extensions-observation_ZH.md
  revision: "2026-10-01.1"
  status: source-reviewed-v0.1.0rc1
  basis: "core; adapter differences explicitly labelled"
---

[English](extensions-observation.md) | [中文](extensions-observation_ZH.md)

# Plugin SDK, adapters and read-only observation

For the trusted-host, refs-only Registry history boundary, see
[Fixed-cut main-thread history](main-thread-history.md).

## Native plugin author contracts
These APIs ship in the unified package. An external plugin is still an
independently installed, explicitly selected trusted package. `PluginDefinition`
binds a name/version and a tuple of operations, a config schema and optional
resources. `PluginOperation(name, description, input_schema, output_schema,
handler, resources=(), effect="pure", timeout_seconds=60,
max_result_bytes=1048576)` pins a top-level importable function and its
source-module hash. Valid effect declarations are `pure`, `external_read`,
`external_write`; limits are 1–7200 seconds and 1–16777216 bytes. Wrong names,
unsupported schemas, unimportable handlers, invalid limits and nonfinite/non-JSON
values raise `PluginError`.

`PluginResource(name, payload, media_type="text/plain")` requires nonempty immutable bytes and provides hash/size/media type. Handler resources are selected by distinct declared names. Draft-07 schemas permit local `#/` references, not remote schema loading. This avoids turning task-authored data into arbitrary schema fetch/import instructions; it does not sandbox trusted Python code.

`PluginCatalog.resolve("plugin/operation")` returns the configured plugin and exact operation, or fails when not selected. `load_catalog(document=None, *, factories=None)` defaults to empty; Python `factories` is a trusted host/testing injection, not a JSON locator. All configuration rows are validated before imports; selected installed entry points must resolve uniquely and match name/version/API. Exact versions and binding digests govern reproducibility, while transitive dependency integrity still depends on packaging.

## Plugin CLI lifecycle
`list` describes the selected catalog; `check` compiles all operations; `build SELECTOR` returns a compiled-checked declaration. These import trusted selected factories but do not invoke plugin operations. `run SELECTOR --input FILE --run-dir DIR` invokes the handler through the registered execution path and writes real run data. Exit success depends on terminal evidence. Timeout/cancellation or external-write uncertainty must not be papered over by an implicit handler retry.

Use the [customization example](../guides/customization.md), not direct calls to private `cpn.rpnh.registry` helpers. Installing a Python package does not itself select it for every task. Documentation metadata never grants runtime execution authority.

## Managed tool catalog for trusted hosts
The unified package includes `cpn/plugins/managed_tools.py`. The catalog is
activated only by an explicit trusted-host configuration, including managed DSH
or per-node `AgentTaskSpec.managed_bindings`; it is not a global discovery
surface for Basic, Codex or OpenCode. `ManagedToolSelector(name, selector)` maps
one provider-visible name to one exact `plugin/operation`.
`ManagedPluginToolCatalog(plugin_catalog, allowlist)` produces an immutable
projection of an explicit `PluginCatalog`, not a discovery service. Its
`provider_declarations` property returns function declarations,
`declaration(name)` and `binding(name)` reject unselected names, and `document()`
includes the selected catalog and registration identities.

The generic catalog accepts `pure`, `external_read`, or `external_write` only
when the host explicitly lists that effect; the default remains `pure`.
Operations require an object input schema. Provider-visible names and selectors
are unique within a node catalog, and built-in AgentLoop dispatch names are
reserved across all nodes. A host may narrow the visible description and input
schema, but invocation validates both the visible schema and the original
plugin operation schema. Installing a plugin, selecting a provider, or changing
the visible description does not grant execution.

The DSH CLI remains a narrower host: it uses `--plugin-config ABSOLUTE_PATH`
together with one or more `--managed-tool NAME=PLUGIN/OPERATION` arguments and
admits pure operations only. Each name component must match
`[a-z][a-z0-9_]{0,47}`. This is not arbitrary DSH plugin or MCP mounting.

The DSH backend uses the shared **16777216-byte (16 MiB) declared result ceiling**. The SDK default remains 1048576 bytes. Neither value is a recommended business limit: derive `max_result_bytes` from the operation's bounded legal output. The current demo operations constrain their input domains and declare 1024 bytes, which is above their maximum serialized output and below the shared ceiling. Changing a launch flag does not change a plugin declaration; keep normal version and exact-identity rules when revising one.

The owner-bound managed invocation service validates the exact caller execution,
tool identity and arguments, and records started plus returned, failed, or
outcome-unknown receipts. A concurrent observer of an actively owned call does
not convert it into an unknown outcome. Once ownership is lost, a dispatch
without durable terminal evidence requires reconciliation and must not be
retried automatically. The Registry result and the bounded model-visible
projection are separate evidence; storing a full result is not proof that a
provider received it. The inspector checks arguments before worker execution;
the DSH path also checks declarations, worst-case tool-result size and the next
model response against its 68 MiB frame. A durably recorded plugin failure is one
correlated `isError: true` tool result, not a successful value. DSH supports one
correlated tool call per model response before the next model step; parallel DSH
tool batches are not implied.

The declaration `pure` is a contract for trusted installed code, not an OS security boundary. See the [DSH guide](../guides/dsh.md) for launch/resume arguments and [runtime recovery](runtime-registry.md) for durable completion settlement.

## Adapter interfaces
The Codex compatibility layer maps client interactions onto the existing main-session/control/model-selection boundaries. Its fixed client version and unsupported slash-command distinctions are part of support, not cosmetic details.

For managed DSH, `createApplication(config)` constructs the genuine upper-layer host, Registry bridge, capability host and projection. `runHeadless(config, task, json = false)` executes that host; `readHistory(config, id)` uses the owner client without constructing effect services; `resumeSession(config, id)` resumes the stored active request. Python `DshBackend` is the session owner. Offline numeric execution uses the exact deterministic route; configured text and explicit managed tools use an exact shared external-provider or local-process selection through `registered_llm/v1`. Request/response frames are bounded at 68 MiB. A new session defaults to 48 module attempts and may explicitly select a positive `--attempt-budget` or `unmetered`; resume retains the persisted declaration. Observations remain candidates until existing products/Success settlement.

The offline and configured envelopes are `application/rpnh_dsh_envelope/v1` and `/v2`; the capability protocol remains `rpnh/dsh/v1`. These are not declarations of arbitrary upstream/API compatibility. A completion recorded after validated outputs can support bounded settlement-only recovery; that does not make an incomplete or unknown provider response replayable. See [adapters](../guides/adapters.md) for actual commands and support boundaries.

## Observation API and state provenance
`project_compiled_net(compiled, *, source, marking=None) -> dict` builds a transient JSON-ready projection. `compiled` must be `CompiledPetriNet` (`TypeError` otherwise); `source.mode` must be `initial_configured` or `registry_current` (`ValueError` otherwise). An optional marking contributes current-epoch unconsumed counts and checkpoint identity. Configured topology without marking is not live runtime state.

The view schema is `rpnh/net_view/v1`, with observation schema `rpnh/net_observation/v1`. Resource places derive from actual `agent_resource`/`resource_lease` kinds and declared lease pools; node IDs, operation IDs, executor keys, schemas, guards and budget bindings remain machine identities. Renderers may translate explanatory labels, never identity fields or original run content.

The ordinary user entry is `rpnh net`; it must remain read-only even if the implementation loads Registry-backed projections. `--view` creates a listener and `--output` writes a projection file, so “read-only Registry” is not “no process/filesystem effects”. A viewer cannot grant permission, infer a final result, open a second writer, or simplify away core execution semantics.

Sources: `cpn/plugins/{api,catalog,cli,managed_tools}.py`,
`cpn/frontend/codex_app_server.py`, `cpn/dsh/backend.py`,
`integrations/dsh/src/app.ts`, `cpn/rpnh/inspection.py:project_compiled_net`,
and `cpn/rpnh_cli.py:_net_command`. These shared contracts are present in the
unified tree; offline acceptance does not establish live-provider validation.
