---
name: rpnh-configure-models
description: "Configure one canonical catalog, exact identities and bounded transport recovery."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: en
  counterpart: models_ZH.md
  revision: "2026-09-26.2"
  status: source-reviewed-not-final-candidate-acceptance
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

The empty catalog contains no usable model; these commands do not establish connectivity. Edit the catalog, then repeat build/check/list. Select a declared profile with `rpnh config use PROFILE` or the exact pair with `rpnh config use PROVIDER MODEL`, then inspect `rpnh config show`. `PROFILE`, `PROVIDER` and `MODEL` are values from your catalog, not literal demo selections.

The current main package also supplies `rpnh init`, `rpnh config add` and `rpnh doctor --json`. Interactive setup requires a terminal; scripted setup uses init/build/use. The `doctor` report is offline.

## Complete external-provider example
The following is **schema-oriented example data, not a usable service**. Replace the `.invalid` endpoint and `EXACT_MODEL_ID` only after choosing an authorized service. It contains a variable name, never a credential value.

```json
{
  "schema_version": "rpnh/provider_model_catalog/v2",
  "providers": [{
    "provider": "example",
    "display_name": "Example provider",
    "models": [{
      "profile": "example-model",
      "model_condition": "EXACT_MODEL_ID",
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

`profile` is a lowercase file-safe slug. `model_condition` is preserved as the outbound model. The generator accepts exactly one external route per selected profile and generic environment-to-header credentials (or `null`). The endpoint must omit userinfo/query/fragment; remote routes require HTTPS, while plain HTTP is limited to `localhost` or a loopback IP for a same-machine OpenAI-compatible server. Static authentication/connection headers and duplicate credential headers are rejected. All integer limits are positive. `context_window_tokens` is an optional exact-model capacity supplied by the user; when present it enables proactive context-pressure compaction before the configured route is called. `context_compaction_retained_tokens` optionally controls the recent complete-message tail and must be smaller than the window. When the window is absent, RPNH does not invent a capacity and can only react to an observed response-length boundary. The complete optional `runtime` object controls task, concurrency, compaction and workspace policy; omission resolves to the documented defaults and generated profiles still record those values. The recovery attempt/cycle fields are bounded from one to three by the schema.

## Transport, recovery and effects
The external transport exposed by this catalog is `openai_chat_completions/v1`, not every API marketed as compatible. The alternative `local_process` adapter requires explicit `argv`, `probe_argv`, `env` and `inherit_env`; `{model}` substitution preserves the selected model. A local process or its probe may still call a paid model. Do not treat “local” as “offline”.

Build produces `external_provider_adapter_config/v2` or `local_process_adapter_config/v1`, plus `llm_execution_selection/v1` and a `rpnh/provider_profiles/v2` manifest. Do not hand-edit generated files. Run `build --check` after catalog or generator changes. Profile/manifest disagreement and registration/route identity mismatch fail rather than selecting another provider.

Health probes and formal retries, when reached during authorized execution, are physical calls and may cost money. A profile's recovery settings are not permission to probe. Authorization must cover the exact route, model, timeout and call budget. Do not replace an unknown submission result with “safe to retry”; retain the unknown-effect record and inspect before further execution.

## Verify and recover
`ready: true` from a profile means required environment values are present; it does not validate keys, availability, model behavior or billing. Keep credentials out of the catalog, version control and shared logs. To repair drift, back up the canonical catalog and regenerate in a controlled directory; point `RPNH_PROFILE_DIR` at its `execution` subdirectory. Do not silently redirect existing runs. Changing a selected profile does not migrate prior run authority.

Sources: `cpn/rpnh/provider_setup.py`, `user_config.py`, `provider_catalog.py`; `cpn/schemas/runtime/provider_model_catalog.v2.schema.json`; overlay `cpn/rpnh/onboarding.py`. See [troubleshooting](troubleshooting.md) and [component reference](../reference/agents.md).
