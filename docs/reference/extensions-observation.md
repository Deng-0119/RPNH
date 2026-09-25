---
name: rpnh-extension-observer-reference
description: "Reference native plugins, adapter lifecycle and read-only net projections."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: en
  counterpart: extensions-observation_ZH.md
  revision: "2026-09-25.2"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](extensions-observation.md) | [中文](extensions-observation_ZH.md)

# Plugin SDK, adapters and read-only observation

## Native plugin author contracts
These APIs exist on the plugin-capable overlays. `PluginDefinition` binds a name/version and a tuple of operations, a config schema and optional resources. `PluginOperation(name, description, input_schema, output_schema, handler, resources=(), effect="pure", timeout_seconds=60, max_result_bytes=1048576)` pins a top-level importable function and its source-module hash. Valid effect declarations are `pure`, `external_read`, `external_write`; limits are 1–7200 seconds and 1–16777216 bytes. Wrong names, unsupported schemas, unimportable handlers, invalid limits and nonfinite/non-JSON values raise `PluginError`.

`PluginResource(name, payload, media_type="text/plain")` requires nonempty immutable bytes and provides hash/size/media type. Handler resources are selected by distinct declared names. Draft-07 schemas permit local `#/` references, not remote schema loading. This avoids turning task-authored data into arbitrary schema fetch/import instructions; it does not sandbox trusted Python code.

`PluginCatalog.resolve("plugin/operation")` returns the configured plugin and exact operation, or fails when not selected. `load_catalog(document=None, *, factories=None)` defaults to empty; Python `factories` is a trusted host/testing injection, not a JSON locator. All configuration rows are validated before imports; selected installed entry points must resolve uniquely and match name/version/API. Exact versions and binding digests govern reproducibility, while transitive dependency integrity still depends on packaging.

## Plugin CLI lifecycle
`list` describes the selected catalog; `check` compiles all operations; `build SELECTOR` returns a compiled-checked declaration. These import trusted selected factories but do not invoke plugin operations. `run SELECTOR --input FILE --run-dir DIR` invokes the handler through the registered execution path and writes real run data. Exit success depends on terminal evidence. Timeout/cancellation or external-write uncertainty must not be papered over by an implicit handler retry.

Use the [customization example](../guides/customization.md), not direct calls to private `cpn.rpnh.registry` helpers. Installing a Python package does not itself select it for every task. Documentation metadata never grants runtime execution authority.

## Managed tool catalog on the DSH line
The DSH line adds `cpn/plugins/managed_tools.py`; do not infer that an older core-only or Codex installation contains it. `ManagedToolSelector(name, selector)` maps one provider-visible name to one exact `plugin/operation`. `ManagedPluginToolCatalog(plugin_catalog, allowlist)` produces an immutable projection of an explicit `PluginCatalog`, not a discovery service. Its `provider_declarations` property returns function declarations, `declaration(name)` and `binding(name)` reject unselected names, and `document()` includes the selected catalog and registration identities.

Only operations declared `effect="pure"` with an object input schema are eligible. Provider-visible names and plugin selectors must be unique. The DSH CLI uses `--plugin-config ABSOLUTE_PATH` together with one or more `--managed-tool NAME=PLUGIN/OPERATION` arguments. Each name component must match `[a-z][a-z0-9_]{0,47}`. Installing a plugin, choosing a provider, or adding a model-visible description does not grant execution. This is not arbitrary DSH plugin or MCP mounting.

The DSH backend imposes a **65536-byte (64 KiB) declared result limit** in addition to the general SDK limits. The SDK default is 1048576 bytes, so an otherwise valid plugin can be rejected before any worker starts. The unchanged `examples/native_plugin/rpnh_demo.py` uses that default; it is a native-plugin example, not a ready-to-run DSH managed-tool fixture. A DSH-compatible selected operation must explicitly declare an appropriate `max_result_bytes` no greater than 65536 in its plugin implementation; changing a launch flag does not change that declaration. Keep the original plugin/version for existing runs and apply the normal exact-identity rules to a revised plugin.

The owner-bound managed invocation service validates the exact caller execution, tool identity and arguments, and records invocation/receipt material. The inspector checks arguments before worker execution; the DSH path also checks declarations, worst-case tool-result size and the next model response against its 2 MiB frame. A durably recorded plugin failure is returned as one correlated `isError: true` tool result, not a successful value. A dispatch without a durable terminal observation requires reconciliation and must not be retried automatically. Only one correlated tool call from a model response is supported before the next model step; parallel tool batches are not implied.

The declaration `pure` is a contract for trusted installed code, not an OS security boundary. See the [DSH guide](../guides/dsh.md) for launch/resume arguments and [runtime recovery](runtime-registry.md) for durable completion settlement.

## Adapter interfaces
The Codex compatibility layer maps client interactions onto the existing main-session/control/model-selection boundaries. Its fixed client version and unsupported slash-command distinctions are part of support, not cosmetic details.

For managed DSH, `createApplication(config)` constructs the genuine upper-layer host, Registry bridge, capability host and projection. `runHeadless(config, task, json = false)` executes that host; `readHistory(config, id)` uses the owner client without constructing effect services; `resumeSession(config, id)` resumes the stored active request. Python `DshBackend` is the session owner. Offline numeric execution uses the exact deterministic route; configured text and explicit managed tools use an exact shared external-provider or local-process selection through `registered_llm/v1`. Request/response frames are bounded at 2 MiB. Observations remain candidates until existing products/Success settlement.

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
