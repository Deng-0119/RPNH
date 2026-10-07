# Configuration contract

[中文](CONFIGURATION_ZH.md) | [Example](README.md)

Use your existing RPNH local-process route. This example contains no provider
transport or credential loader of its own. The execution profile references an
adapter; the judge references an adapter directly. They may select the same
model, but their output-token settings and roles differ.

| Field | Meaning |
|---|---|
| `execution.profile_path` | User-owned `llm_execution_selection/v1`; `adapter_kind=local_process` |
| profile `adapter_config_path` | Relative to profile or absolute local path; never an in-repo credential |
| `execution.exact_model`, `reasoning_effort` | Must match profile/model and adapter `--model`/`--reasoning-effort` argv |
| `scoring.adapter_path` | User-owned local-process adapter; `env` object and `inherit_env` array required |
| `scoring.max_output_tokens` | 512, matching the fixed scorer's local-transport request |
| `scoring.transport_status` | `ready` is a configuration declaration, not a network probe |
| `limits_per_run` | All three cumulative limits default to null; finite values are optional and constitute a different experimental condition |
| `authorized` | False unless configure was given `--authorize`; explicit run/score still required |

The example's `check-config` reports execution and judge readiness separately.
A missing judge file does not make the execution route invalid; the configuration
must still contain the judge fields. The retained checker expects the explicit
argv flags described here: an arbitrary RPNH adapter is not automatically
compatible. No monetary cap, price table, account ID or embedded credential is
required. Historical model labels live in the result metadata only.

Changing model, route, effort, task graph, instruction envelope, tool surface,
source revision or scoring projection creates a newly recorded condition.
Reproduction means running the documented protocol with your selected settings;
it is not a promise of identical stochastic outputs or identical scores.

An explicit `--configuration-condition office-public-discovery-workflow-v1` selects the separately versioned [public discovery/workflow comparison](CONFIGURATION_COMPARISON.md). Omitting it preserves the baseline. New-condition runtime acceptance and model performance remain unverified.
