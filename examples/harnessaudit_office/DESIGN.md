# Code and execution map

[中文](DESIGN_ZH.md) | [Example](README.md)

**Published reference:** [Published scores and interpretation](COMPARISON.md) · [Implementation supplement](IMPLEMENTATION_COMPARISON.md). This comparison uses public results and requires no local reference implementation or execution.

| Layer | Actual code | Responsibility |
|---|---|---|
| Public inputs | `src/rpnh_ha/task_view.py::public_task` | Goal, roles and public tools; excludes access rules, gold paths and completion checks from the executor view |
| Application graph | `workflow.py::build_business_workflow` | Hub fan-out/join declaration; no policy-before-write dependency is manufactured |
| Host binding | `local_driver.py::managed_bindings` | All public tools mapped to each role's trusted plugin slot; preserves published parameter descriptions |
| Native launch | `local_driver.py::LocalNativeDriver.execute` | `AgentTaskSpec` and `TaskControl`; RPNH owns the actual loop and Registry |
| Business tools | `native_plugin.py`, `backend.py`, `bank_transfer.py`, `upstream.py` | One registered call to original stateful OfficeBank; private before/after snapshots |
| Evidence | `registry_export.py`, `handoff_capture.py`, `crosswalk.py` | Separate execution receipts, communicated payloads and later model-input evidence |
| Scoring | `scoring.py`, `judge_transport.py` | Original fixed HarnessAudit functions; configured local-process judge transport replaces only transport helper calls during scoring |
| User entry | `example.py`, `cli.py` | Read results, configure, prepare, explicitly run/score; never a second agent scheduler |

The `ha_manager`, `ha_admin`, `ha_policy`, `ha_extra` entry points are host slots,
not fixed benchmark business roles. Public task roles select their slots; at most
three specialists are supported. `off-t2` remains in the low-level package for
legacy smoke tests, but is not part of the published five-task results.

The actual workflow and role instructions are retained, not optimized. In
particular it is a static hub/fan-out/join workflow, not an adaptive manager that
can redesign the graph after reading reports. All tools remain visible; hidden
benchmark labels are not runtime ACL declarations. Tool schema projection retains
the experiment's no-`required` policy rather than claiming exact equivalence to
every upstream framework's schema generation.

Returned tool evidence and subsequent model consumption are independent. A
verified returned call is eligible for the benchmark action trace without forcing
another model request. Unknown effects are not upgraded to successful returns.
Resource access permissions, data actually read, message content and grader scan
payloads must not be conflated. Full local artifacts remain private by default.

`scripted_model.py`, `scripted_profile.py`, `scripted_acceptance.py` and
`native_smoke.py` are explicitly non-benchmark probes. They are not the default
example path, do not produce the historical scores, and must never be substituted
for a model-backed run in a benchmark report.
