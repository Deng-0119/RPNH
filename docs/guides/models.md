---
name: rpnh-configure-models
description: "Configure one canonical catalog, exact identities and bounded transport recovery."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: models_ZH.md
  revision: "2026-09-30.1"
  status: source-reviewed-v0.1.0rc1
  basis: "core; adapter differences explicitly labelled"
---

[English](models.md) | [中文](models_ZH.md)

# Provider and exact-model configuration

The [configuration and limits reference](configuration.md) is the exhaustive
list of user-settable runtime fields and fixed protocol boundaries. This page
focuses on model onboarding.

## Goal and authority
Create one user-owned catalog and derive all selectable profiles from it. An exact provider/model identity is opaque user input, not a recommendation or a promise of transport compatibility. The catalog ships empty. Configuration validation, credential presence and successful physical execution are three different facts.

Default files are `~/.config/rpnh/provider_models.json`, `~/.config/rpnh/profiles/{adapters,execution}/`, `profiles/profiles.json`, and `~/.config/rpnh/config.json`. Overrides are `RPNH_PROVIDER_CATALOG`, `RPNH_PROFILE_DIR` (the **execution** directory), `RPNH_CONFIG`, and `RPNH_EXECUTION_CONFIG`. An explicit CLI `--execution` selects an execution file. Avoid changing multiple selectors while diagnosing a run.

## Safe setup sequence

```bash
rpnh config init
rpnh config build
rpnh config build --check
rpnh config list
```

The empty catalog contains no usable model; these commands do not establish connectivity. Edit the catalog, then repeat build/check/list. Select a declared profile with `rpnh config use PROFILE` or the exact pair with `rpnh config use PROVIDER MODEL`. If that model declares reasoning efforts, add `--effort EFFORT`; omitting it selects the declared default. Then inspect `rpnh config show`. `PROFILE`, `PROVIDER`, `MODEL`, and `EFFORT` are values from your catalog, not literal demo selections.

The current main package also supplies `rpnh init`, `rpnh config add` and `rpnh doctor --json`. Interactive setup requires a terminal; scripted setup uses init/build/use. The `doctor` report is offline.

Model and effort authority belongs to this basic-harness configuration layer,
not to a frontend. The Basic terminal and DSH launch the exact saved or explicit
execution profile. Codex and OpenCode adapt their model/variant controls to the
same logical selection and immutable effort profile. A frontend cannot invent
another effort, change it inside an active turn, or dispatch a provider request
outside the selected harness profile.

## Complete external-provider example
The following is **schema-oriented example data, not a usable service**. Replace the `.invalid` endpoint and `EXACT_MODEL_ID` only after choosing an authorized service. It contains a variable name, never a credential value.

```json
{
  "schema_version": "rpnh/provider_model_catalog/v3",
  "providers": [{
    "provider": "example",
    "display_name": "Example provider",
    "models": [{
      "profile": "example-model",
      "model_condition": "EXACT_MODEL_ID",
      "reasoning_efforts": {
        "supported": ["low", "medium", "high"],
        "default": "medium"
      },
      "adapter": {
        "adapter_kind": "external_provider",
        "route_id": "example-route",
        "backend": "example-backend",
        "protocol": "openai_chat_completions/v1",
        "endpoint": "https://provider.example.invalid/v1/chat/completions",
        "credential": {
          "environment": "RPNH_EXAMPLE_API_KEY",
          "header": "Authorization",
          "prefix": "Bearer "
        },
        "headers": {},
        "recovery": {
          "strategy": "bounded_same_route_health_probe/v1",
          "max_probe_attempts": 1,
          "probe_timeout_budget_seconds": 5,
          "max_probe_success_formal_failure_cycles": 1
        }
      },
      "timeout_seconds": 60,
      "max_output_tokens": 1024,
      "max_response_bytes": 1048576,
      "context_window_tokens": 131072,
      "context_compaction_retained_tokens": 16384,
      "runtime": {
        "max_turns_per_node": 12,
        "max_parallel_nodes": 4,
        "main_history_message_limit": 20,
        "context_pressure_trigger_ratio": 0.9,
        "context_tool_output_byte_limit": 10000,
        "workspace": {
          "timeout_seconds": 120,
          "memory_bytes": 4294967296,
          "process_limit": 64,
          "source_size_bytes": 16777216,
          "input_size_bytes": 16777216
        }
      }
    }]
  }]
}
```

`profile` is a lowercase file-safe slug. `model_condition` is preserved as the outbound model. Optional `reasoning_efforts` is also exact-model data supplied by the user: `supported` is the provider-documented set for that model and `default` must be one of those values. RPNH has no built-in model/effort table and does not add synthetic values such as cross-model modes or service tiers. When the field is omitted, the frontend offers no effort selector and the HTTP request omits `reasoning_effort`.

The generator accepts exactly one external route per selected profile and generic environment-to-header credentials (or `null`). The endpoint must omit userinfo/query/fragment; remote routes require HTTPS, while plain HTTP is limited to `localhost` or a loopback IP for a same-machine OpenAI-compatible server. Static authentication/connection headers and duplicate credential headers are rejected. All integer limits are positive. `context_window_tokens` is an optional exact-model capacity supplied by the user; when present it enables proactive context-pressure compaction before the configured route is called. `context_compaction_retained_tokens` optionally controls the recent complete-message tail and must be smaller than the window. When the window is absent, RPNH does not invent a capacity and can only react to an observed response-length boundary. The complete optional `runtime` object controls task, concurrency, compaction and workspace policy; omission resolves to the documented defaults and generated profiles still record those values. The recovery attempt/cycle fields are bounded from one to three by the schema.

## Transport, recovery and effects
The external transport exposed by this catalog is `openai_chat_completions/v1`, not every API marketed as compatible. The alternative `local_process` adapter requires explicit `argv`, `probe_argv`, `env` and `inherit_env`; `{model}` substitution preserves the selected model. A local model that declares efforts must also place `{reasoning_effort}` in its formal `argv`; the builder materializes one immutable execution variant for every supported value. A local process or its probe may still call a paid model. Do not treat “local” as “offline”.

For a v3 catalog, build produces `external_provider_adapter_config/v3` or `local_process_adapter_config/v2`, plus `llm_execution_selection/v2` and a `rpnh/provider_profiles/v3` manifest. Legacy v2 catalogs remain readable and generate their legacy profile shapes. Do not hand-edit generated files. Run `build --check` after catalog or generator changes. Profile/manifest disagreement and registration/route identity mismatch fail rather than selecting another provider.

Health probes and formal retries, when reached during authorized execution, are physical calls and may cost money. A profile's recovery settings are not permission to probe. Authorization must cover the exact route, model, timeout and call budget. Do not replace an unknown submission result with “safe to retry”; retain the unknown-effect record and inspect before further execution.

## Verify and recover
`ready: true` from a profile means required environment values are present; it does not validate keys, availability, model behavior or billing. Keep credentials out of the catalog, version control and shared logs. To repair drift, back up the canonical catalog and regenerate in a controlled directory; point `RPNH_PROFILE_DIR` at its `execution` subdirectory. Do not silently redirect existing runs. Changing a selected profile does not migrate prior run authority.

Sources: `cpn/rpnh/provider_setup.py`, `user_config.py`, `provider_catalog.py`,
`onboarding.py`; `cpn/schemas/runtime/provider_model_catalog.v3.schema.json`.
See [troubleshooting](troubleshooting.md) and [component reference](../reference/agents.md).
