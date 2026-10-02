# Implementation differences: source-level supplement

[中文](IMPLEMENTATION_COMPARISON_ZH.md) | [Published-results comparison](COMPARISON.md)

This retains the reviewed implementation comparison to explain the example. It is not a local baseline experiment plan and requires no new runs. Source-level differences are not measured performance gains.

## 2. What is retained and what changes

| Layer | Original OAI path | This RPNH example |
|---|---|---|
| Tasks and business data | Office YAML and OfficeBank | Same pinned upstream tasks and Bank; no corrected answer keys or fixtures |
| Business tools | SDK FunctionTool wrappers calling the Bank | Managed native-plugin wrappers bridging to the same Bank |
| Roles | Task hub and specialists | Same public roles; the hub has separate planning and finalization nodes |
| Orchestration | Runner and delegate_to_agent | Native AgentLoop and a declared workflow graph |
| Evidence | Inline ActionSink calls and communications | Registry invocation/product/delivery evidence projected to ObservableAction |
| Grading | Fixed SAR, AVS and TCR functions | Same functions; the retained judges used the declared local route and effort |

Sources: [OAI adapter][oai_adapter], [tools][oai_tools], and this example's
[workflow](src/rpnh_ha/workflow.py), [driver](src/rpnh_ha/local_driver.py), [scoring](src/rpnh_ha/scoring.py).
An identical tool name or backend does not establish an identical provider-visible argument contract.

## 3. Source-level differences

| Dimension | Original OAI adapter | Configured RPNH example | Supported interpretation |
|---|---|---|---|
| Coordination | A hub can delegate, inspect reports and delegate follow-ups. Multiple delegate calls in one turn can execute concurrently. | hub_plan → specialists → hub_finalize; no follow-up or rework arc in this graph. | OAI has an adaptive collaboration path this example has not configured. This is not proof that RPNH cannot express other graphs. |
| Completion | Runner returns final_output; the adapter emits a communication to the user. | Declared products enable downstream nodes; final output is linked to native terminal evidence. | Completion/evidence contracts differ. Neither terminal text automatically proves business correctness. |
| Tool visibility | Hub and spokes receive all domain tools; only the hub receives delegation. | Every node receives all 15 Office tools plus read/write/complete built-ins. | Neither side here creates an answer-aware whitelist from hidden useful/forbidden labels. RPNH has not demonstrated role-specific business blocking in this configuration. |
| Argument schema | All parameters typed; unspecified types default to string; all are required. | Descriptions and explicit types retained; unspecified types unconstrained; no required list. | A real adapter/input-contract difference, not a negligible implementation detail. |
| Prompt envelope | Public role text plus hub instructions forbidding direct domain calls/PII sharing and encouraging focused follow-ups. | Public role text plus registered-product/termination instructions and retained capacity/efficiency wording. | Prompts differ. Removing numeric quotas does not remove historical wording. This is not a kernel-only swap. |
| Predispatch authority | SDK FunctionTool path; no RPNH operation/firing/lease authority in this adapter. | Managed service verifies the admitted operation, allowed tool IDs and exact registrations, then persists a started receipt. | A concrete execution mechanism, not yet a measured success-rate gain. |
| Errors | Shared dispatch/handler converts several errors to text and records them. | Business errors can still be strings; worker/transport uncertainty for external writes has outcome_unknown and reconciliation. | An error string does not prove no side effect. Reconciliation is not automatic rollback or compensation. |
| Handoffs | Hub task and specialist summary are recorded directly; spokes have no delegate tool. | Registered plans/reports are read into node context; peer-report reads were also observed. | A missing business arc is not itself an enforced communication prohibition. Actual reads and recipient scope matter. |
| State and recovery | This adapter disables SDK tracing in favor of ActionSink and does not configure a durable SDK Session or RPNH-style recovery. | Registry persists exact evidence; existing runs supported correction of missing observation projections. | Scope this statement to the adapter. Do not claim the whole SDK lacks tracing, sessions or recovery. |
| Limits | Ten turns per specialist; hub uses ctx.max_turns; default adapter wait_for is 300 seconds. | Historical B1 was capped; supplement/current example support no cumulative quotas. | Do not present default-capped OAI versus unmetered RPNH as a matched superiority test. |

Sources: [adapter][oai_adapter], [prompts][oai_prompts], [tools][oai_tools], [dispatch][dispatch],
[RPNH managed service][managed], and [example design](DESIGN.md).

### The default ClawTeam + OpenClaw path is not an unstructured baseline

The ClawTeam adapter creates a team/shared task board, starts real CLI agent processes, registers a shared
domain MCP service for all roles, and uses a sentinel, process state and session-log parsing for completion
and observation. This differs from the in-process OAI Runner. Record both framework and CLI harness.
Do not reduce ClawTeam to "logs but no state". This comparison does not audit the complete OpenClaw,
Codex or Claude CLI internals and cannot deny their native permission, recovery or safety capabilities.
Source: [ClawTeam adapter][clawteam].

[frameworks]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/frameworks/__init__.py
[launcher]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/run_ma.sh
[oai_adapter]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/frameworks/oai/adapter.py
[oai_tools]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/frameworks/oai/tools.py
[oai_prompts]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/frameworks/oai/prompts.py
[clawteam]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/frameworks/clawteam/adapter.py
[dispatch]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/frameworks/core/tool_dispatch.py
[completion]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/completion_judge.py
[checker]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/checker.py
[recognizer]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/recognizer.py
[managed]: https://github.com/Deng-0119/RPNH/blob/3492ba2fb50e40173afebae627138afea0bc2f24/cpn/plugins/managed_tools.py
[agent_task]: https://github.com/Deng-0119/RPNH/blob/3492ba2fb50e40173afebae627138afea0bc2f24/cpn/rpnh/agent_tasks.py
[graph]: https://github.com/Deng-0119/RPNH/blob/3492ba2fb50e40173afebae627138afea0bc2f24/cpn/rpnh/agent_workflows.py
