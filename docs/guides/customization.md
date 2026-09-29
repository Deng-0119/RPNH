---
name: rpnh-customize
description: "Extend declarations and explicitly selected plugins without bypassing the core."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: customization_ZH.md
  revision: "2026-09-29.2"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](customization.md) | [中文](customization_ZH.md)

# Customization: workflows, tools, skills and MCP

## Choose the correct boundary
A business workflow declares operations and their visible Petri structure. A trusted host binds implementation identities. The harness admits and settles execution; Registry records exact identities, resources and versions. A frontend only translates interaction. Do not embed a new private agent loop in a plugin and add its logs after it finishes.

Use `ModuleDeclaration` and `Registration` for a new typed workflow, registered components for lowering, and an explicitly installed native plugin for an external tool/instruction resource. The native SDK and host bridge are part of the current main package; each external plugin is still installed and selected separately. Ordinary documentation pages are never automatically scanned or executed as skills.

A model-capable host integration is also an adapter/plugin, but it must use the harness-owned `registered_llm/v1` capability. The adapter translates host messages into the registered request and maps the canonical response back to the host. Provider profile resolution, credentials, transport, recovery, physical-attempt accounting and Registry settlement remain in the shared harness. Do not copy them into Codex-, DSH- or OpenCode-specific code.

## Build a workflow or Inspector boundary
Declare component config schemas, typed input/output ports, operation executors, outcomes/products, links, entry/exit and terminal binding. Register trusted lowering callbacks, executors, tools and schemas in the host. Compile using `compile_module`; compilation returns a candidate net, not a registered run. Start through the sole owner and dispatch only after admission.

Represent a business decision with an explicit Inspector place/token, guard or registered operation at the appropriate boundary. The business defines the policy; the core enforces the declared structure and records the decision. Declare relevant resource access and settlement requirements rather than checking permission only in an invisible UI branch. Resource capacity/leases and exact version references are execution inputs, not diagram decorations.

Links fuse compatible places; they do not manufacture broadcast copies. Branching, joins and bounded feedback must have the corresponding declared ports, arcs and budgets. Do not edit Registry records or marking directly to “enable” a step. See [declarations](../reference/declarations.md).

## A real native-plugin example
The unified source tree includes the native plugin SDK and one example plugin,
but the example remains an independently installed and explicitly selected
package. After reviewing its source:

```bash
python -m pip install ./examples/native_plugin
rpnh plugins --config examples/native_plugin/plugins.json list
rpnh plugins --config examples/native_plugin/plugins.json check
rpnh plugins --config examples/native_plugin/plugins.json build demo/add
```

`--config` precedes the subcommand. The example exposes `demo/add`, `demo/instruction` and `demo/summarize`; its handlers check cancellation where work is performed, and the instruction result includes exact registered resource identity. `check` compiles declarations without invoking an operation, but selected factories are trusted Python imports and must be declaration-only.

For an explicitly authorized local tool execution, create an input JSON with `left` and `right`, then run `rpnh plugins --config CONFIG run demo/add --input INPUT --run-dir NEW_RUN_DIR`. That creates real Registry/run state and invokes a handler, even though the pure addition needs no model. Do not include it in a blanket “nothing executes” setup check.

## Author and bind a plugin
Expose a declaration-only factory through the `rpnh.plugins` entry-point group. Select it by name, entry-point name, version, config and environment-variable **names** in a `rpnh/plugins/v1` configuration. `RPNH_PLUGIN_CONFIG` is an explicit alternative to `--config`; absent selection means an empty catalog, not directory discovery.

Use top-level importable handlers with input/output Draft-07 schemas, declared resources, an effect of `pure`, `external_read` or `external_write`, and explicit limits. Handler source bytes are pinned; transitive dependencies still belong to the installed environment. Version declarations are not an OS security sandbox. No hidden retries, dynamic imports supplied by task JSON, or direct internal Registry handles belong in a handler.

A skill can be a registered instruction resource consumed by an admitted operation; Markdown front matter alone does not create one. An MCP-backed tool must be wrapped/bound through an explicitly installed host capability with visible operation/resource/effect semantics. This is not a claim of universal MCP server discovery or support for every transport. Test the selected host and protocol before advertising compatibility.

## Change providers or runtime behavior
A new provider using an existing transport usually belongs in the canonical catalog. A new transport requires an explicit adapter contract, exact identity handling, physical-call/unknown-effect accounting and deterministic tests; editing only a provider name is insufficient. Never move credentials or business policies into generic core defaults.

Validate malformed input, missing registration, schema mismatch, cancellation, identity drift, timeout, resource access and settlement failure. Keep compatibility or migration notes with changed schemas/commands. Sources: `cpn/rpnh/module.py`, `compiler.py`, `harness.py`; `cpn/plugins/{api,catalog,cli,runtime}.py` and `examples/native_plugin/rpnh_demo.py`.
