---
name: rpnh-provider-model-configuration
description: "Configure arbitrary user-owned provider routes and exact models."
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: en
  counterpart: PROVIDER_MODEL_CONFIGURATION_ZH.md
  revision: "2026-09-30.1"
  status: source-reviewed-pre-release
---

# Provider and model configuration

[中文](PROVIDER_MODEL_CONFIGURATION_ZH.md)

This page covers provider transport in detail. The exhaustive list of Agent,
workspace, viewer, plugin and fixed compatibility limits is the
[configuration reference](guides/configuration.md).

## Guided setup

Run `rpnh init` (or `rpnh config setup`) in a terminal. The wizard can create
an HTTPS API or local-command profile, generate its files, and select it in one
step. `rpnh config add` always adds a new named profile and never replaces an
existing name. Use `rpnh doctor` for offline configuration checks, or
`rpnh doctor --json` in scripts. These commands do not send model requests.

The wizard provides editable limits of 900 seconds, 8192 output tokens and
16 MiB per response. Provider and model identifiers are entered by the user.
API keys are supplied through environment variables, not entered into the catalog.
For existing configurations the wizard offers selection or addition; it does
not reset your catalog. Ctrl-C or declining the final save cancels addition.

## Manual or scripted setup

The editable catalog is `~/.config/rpnh/provider_models.json`. Generated adapter
and execution profiles are under `~/.config/rpnh/profiles/`; the selected profile
is in `~/.config/rpnh/config.json`.

```bash
rpnh config init
${EDITOR:-vi} ~/.config/rpnh/provider_models.json
rpnh config build
rpnh config build --check
rpnh config list
rpnh config use PROFILE
rpnh config show
```

`provider` and `model_condition` are user-controlled opaque identifiers. RPNH
does not maintain a provider/model allowlist and does not require a validation
flag before showing a configured profile. `profile` is only a safe local slug
used for filenames and frontend selection. A profile can also be selected by
its exact pair when that pair identifies only one profile:

```bash
rpnh config use 'provider identifier' 'exact model identifier'
```

If that exact model declares reasoning efforts, select one with either form:

```bash
rpnh config use PROFILE --effort EFFORT
rpnh config use 'provider identifier' 'exact model identifier' --effort EFFORT
```

The valid values and default come only from that model's catalog entry. RPNH
does not maintain a vendor model/effort table.

RPNH preserves exact-model authority: the generated outbound model is exactly
`model_condition`, and a failed call never causes a silent provider, route, or
model fallback.

## OpenAI-compatible HTTP transport

The built-in HTTP adapter accepts any provider and exact model exposed through
an OpenAI-compatible chat-completions endpoint. Remote endpoints must use
HTTPS; plain HTTP is accepted only for a loopback service on the same machine:

The numeric values below illustrate a high-capacity profile; they are not
additional defaults. Interactive defaults are the values stated above.

```json
{
  "schema_version": "rpnh/provider_model_catalog/v3",
  "providers": [
    {
      "provider": "provider identifier",
      "display_name": "My provider",
      "models": [
        {
          "profile": "my-model",
          "model_condition": "exact/model-identifier@version",
          "reasoning_efforts": {
            "supported": ["low", "medium", "high"],
            "default": "medium"
          },
          "adapter": {
            "adapter_kind": "external_provider",
            "route_id": "primary",
            "backend": "user-defined backend",
            "protocol": "openai_chat_completions/v1",
            "endpoint": "https://api.example.invalid/v1/chat/completions",
            "credential": {
              "environment": "MY_PROVIDER_API_KEY",
              "header": "Authorization",
              "prefix": "Bearer "
            },
            "recovery": {
              "strategy": "bounded_same_route_health_probe/v1",
              "max_probe_attempts": 3,
              "probe_timeout_budget_seconds": 300,
              "max_probe_success_formal_failure_cycles": 3
            },
            "headers": {}
          },
          "timeout_seconds": 900,
          "max_output_tokens": 32768,
          "max_response_bytes": 16777216,
          "context_window_tokens": 262144,
          "context_compaction_retained_tokens": 32768
        }
      ]
    }
  ]
}
```

The endpoint cannot contain credentials, a query, or a fragment. HTTPS is
required unless the hostname is `localhost` or a loopback IP address; remote
plain-HTTP endpoints fail validation. Credential handling is generic: map one
environment variable to the required HTTP header and optional prefix. Use
`"credential": null` when the endpoint does not require one. Secret values are
never stored in the catalog, generated profiles, user selection, logs, or
public Registry policy.

`context_window_tokens` is optional but must be the capacity of the exact
configured model when supplied. It is rendered into the execution target and
shown by `rpnh config show`; this enables proactive compaction at the current
90% pressure boundary or earlier when output-token reservation requires it.
`context_compaction_retained_tokens` optionally bounds the recent complete-message
tail and must be smaller than the declared window. Omitting the window disables
proactive pressure decisions rather than making RPNH guess a provider-specific
value. Full historical turns remain in Registry after compaction.

`reasoning_efforts` is optional exact-model metadata. Populate `supported`
only with values documented by the provider for that exact model; `default`
must be a member of the same nonempty set. These values are opaque identifiers,
so model modes, subscription plans, service tiers, or project-wide labels do
not belong in this field. If omitted, no effort selector is advertised and the
external request omits `reasoning_effort`. If present, each frontend adapts the
same basic-harness selection to its own protocol; the selected value remains
pinned in saved configuration and session authority.

The optional complete `runtime` object on the same model entry configures
per-node turn budget, workflow concurrency, main-history prompt tail, context
pressure/reduction and workspace resource bounds. When omitted, build resolves
documented defaults and writes them into the generated execution profile. See
the configuration reference for every field and default; do not hand-edit the
generated profile.

Static non-secret headers may be placed in `headers`. They cannot override the
credential header or transport-owned headers such as `Content-Type`, `Host`, or
`Content-Length`.

## Bounded same-route recovery

Every external-provider adapter declares its current `recovery` policy. The
values shown above are the recommended baseline: after a formal provider or
transport interruption, RPNH submits at most three minimal health probes within
one shared 300-second probe budget. A probe uses the same provider, backend,
endpoint, credential route, and exact model, with the fixed prompt `Reply with
READY.` and an eight-token output limit. The first valid 2xx response stops the
probe sequence immediately and permits one new physical attempt of the original
formal prompt.

If that formal retry is also interrupted, the adapter may begin another bounded
probe sequence. `max_probe_success_formal_failure_cycles` limits those cycles
to at most three.
Exhausting the probe attempts, shared probe budget, or cycle limit returns one
classified failure to the AgentLoop. There is no sleep loop and no provider,
route, or model fallback.

Health probes are provider-private physical calls. Each probe and formal retry
has a distinct request/attempt identity in the append-only private adapter
audit. Probes do not create AgentLoop turns, Registry model successes, workflow
operations, or experiment results. Existing external-provider catalogs must add
the required `recovery` object and run `rpnh config build`; the current schema
does not silently read an older shape.

## Local-process transport

A local executable must read the canonical RPNH request JSON from stdin and
write one canonical `llm_response_envelope/v1` JSON response to stdout. An ordinary
interactive chat CLI is not directly interchangeable with that protocol. See
`cpn/llm_adapters/local_process.py` and the bundled
`cpn/llm_adapters/codex_subscription_bridge.py` for the transport and a wrapper.
Setup and doctor never execute the adapter or its stored probe command.

A local model catalog entry looks like:

```json
{
  "profile": "my-local-model",
  "model_condition": "exact local model",
  "reasoning_efforts": {
    "supported": ["low", "medium", "high"],
    "default": "medium"
  },
  "adapter": {
    "adapter_kind": "local_process",
    "argv": [
      "my-llm-command", "--model", "{model}",
      "--reasoning-effort", "{reasoning_effort}"
    ],
    "probe_argv": ["my-llm-command", "--version"],
    "env": {},
    "inherit_env": []
  },
  "timeout_seconds": 900,
  "max_output_tokens": 32768,
  "max_response_bytes": 16777216,
  "context_window_tokens": 262144,
  "context_compaction_retained_tokens": 32768
}
```

The builder replaces `{model}` with the exact model identifier. If
`reasoning_efforts` is declared, the formal `argv` must also contain
`{reasoning_effort}`; the builder creates one immutable adapter/execution
variant per supported value. Runtime-owned placeholders supported by the
local-process adapter remain available. Every
local-process request runs with the destination Registry run root as its current
working directory. Bridge scratch data therefore stays inside the user-owned
run instead of requiring the installed source tree or `/tmp` to be writable.

## Alternate locations

`RPNH_PROVIDER_CATALOG`, `RPNH_PROFILE_DIR`, and `RPNH_CONFIG` can relocate the
catalog, generated execution directory, and selected-profile file. Commands can
also receive explicit paths:

```bash
rpnh config init --catalog /absolute/path/provider_models.json
rpnh config build \
  --catalog /absolute/path/provider_models.json \
  --output-root /absolute/path/generated-profiles
export RPNH_PROFILE_DIR=/absolute/path/generated-profiles/execution
```

The generated index allows later builds to remove only obsolete files that the
same builder previously generated. It never deletes unrelated files.

## Checking your configuration

`rpnh doctor` validates local profile files, required API-key variable presence,
local executable availability, and generated-file consistency. It does not run
adapter commands, import a local wrapper module, check API-key validity, or
contact a model. A clean report is local configuration readiness only. Use an
ordinary conversation to test the connection when you are ready to make model
calls. Keep raw keys out of model names, URLs, arguments, fixed environment
values, and extra headers; use the credential environment mapping instead.
