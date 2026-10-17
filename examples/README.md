# RPNH user examples

English | [中文](README_ZH.md)

[Repository home](../README.md) · [First-run tutorial](../docs/guides/getting-started.md) · [Documentation hub](../docs/index.md) · [Viewer guide](../docs/guides/viewer.md)

## Choose your first example

New to RPNH? Complete [installation and a local run](../README.md#quick-start), then choose one example and follow its own README. Run source examples from the checkout root; installed wheels export only the named examples listed below.

| What to check first | Start with | Models and dependencies |
|---|---|---|
| Installation, tool output and the Viewer | [Native plugin](native_plugin/README.md) | No model; install the example plugin |
| Two-agent parallelism and an all-input join | [Workflow patterns](workflow_patterns/README.md) | Scripted by default; optional live profile |
| Carry a project to another environment | [Package reuse](package_reuse/README.md) | No model; prepare receiver dependencies |
| Co-create and independently run the original task | [Project collaboration](project_collaboration/README.md) | Authorized live profile; controlled contributors |
| Inspect applied tasks and recorded scores | [Agent/role workflows](#agent-and-role-workflows) | Reading is offline; run requirements are per example |

[Tools and SDK](#tool-sdk-and-validation-fixtures) · [Native input and authority](#native-input-and-authority-fixtures) · [Historical mechanisms](#historical-contribution-and-business-evidence) · [Obtain and run](#obtain-and-run-an-example) · [Read the figures](#read-the-figures-and-evidence)

Choose by modeled object: agent workflows describe task responsibilities and handoffs; domain algorithms and business systems are tools or external environments. ERP, 3-DOF, JB, HarnessAudit, AutomationBench, SlopCodeBench and RRSI remain genuine agent/role workflows. RRSI custom registered-host role PNs can have genuine agent bindings without using stock AgentLoop. Pure-tool pipelines, native-input gates, author principals and annotations do not become agents merely through Registry lineage.

## Project preview: copy, co-create and reuse

Start with [copy, co-create and reuse one agent project](project_collaboration/README.md)
for the complete project journey: resolve two contributors' conflicts into original
two-parent M, perform three pre-business content adoptions, then download exact M
for an independent receiver. Both ends run stock implementer/reviewer agents.
The dated result records 43 registered requests/42 responses and both original
path-adapted assertion sets passing 3/3; the grader was visible and original
`test.sh` was not run. See the [full business PN](project_collaboration/figures/source-petri.png),
real attached execution nets and [capture provenance](project_collaboration/figures/provenance.json).

[![Completed source business PN: implementer/reviewer, five nodes and six arcs.](project_collaboration/figures/source-petri.png)](project_collaboration/figures/source-petri.png)

[Full-size PN](project_collaboration/figures/source-petri.png) · [Exact observation and provenance](project_collaboration/figures/source-petri.json) · [Capture record](project_collaboration/figures/provenance.json)

## Agent and role workflows

| Goal | Example | Figures or guide | Default model boundary |
|---|---|---|---|
| Copy a complete project, resolve conflicts and reuse exact M independently | [Project collaboration](project_collaboration/README.md) | [Actual business and execution PN views](project_collaboration/README.md#actual-viewer-project-and-two-pn-layers) | Existing authorized live profile; controlled contributors |
| Two original-principal contributions to curator → auditor contracts | [Agent-network co-building](user_collaboration/agent_cobuild/README.md) | Actual execution and evidence on example page | Native AgentLoop; local_process scripted protocol demonstration |
| Model–program–model calculation | [Hybrid summary](hybrid_summary/README.md) | [Detailed flow](hybrid_summary/README.md) | Scripted; optional exact live profile |
| Serial, parallel, document and long workflow shapes | [Workflow patterns](workflow_patterns/README.md) | [PN and simplified-view gallery](workflow_patterns/README.md) | Scripted; optional exact live profile |
| Two independently managed child tasks | [Task workspace](task_workspace/README.md) | [Child-task PetriNet](task_workspace/README.md) | Scripted |
| Agent replacement; separate definition-only composition | [Native net operations](net_operations/README.md) | [Adopted replacement PetriNet](net_operations/README.md) | AgentStage replacement; compose_serial is definition-only |
| Real clinical document/data packet | [JB steering packet](jb_steering_packet/README.md) | [Actual workflow PetriNet](jb_steering_packet/README.md) | User-owned exact live profile |
| Real numerical optimal-control task | [3-DOF powered descent](three_dof_powered_descent/README.md) | [Actual workflow PetriNet](three_dof_powered_descent/README.md) | User-owned exact live profile |
| Office benchmark integration and scored results | [HarnessAudit Office](harnessaudit_office/README.md) | [Run and viewer guide](harnessaudit_office/README.md) | Read results offline; run/score require explicit local profiles |
| Two-round Policy evolution with independent child runs | [RRSI v0.6](rrsi_v06/README.md) | [Policy and role PetriNet declarations](rrsi_v06/README.md#petrinet-declarations) | User-owned exact live profile |
| Business-workflow benchmark and host extensions | [AutomationBench](automationbench/README.md) | [Results, task boundaries and viewer guide](automationbench/README.md) | Read retained results offline; new live runs require an authorized profile |
| Managed ERP tasks with the original verifier | [ERP-Bench](erp_bench/README.md) | [Task and execution guide](erp_bench/README.md) | User-owned exact live profile and original Docker environment |
| Native Session commands for a coding-task prefix | [SlopCodeBench pilot](slopcodebench/README.md) | [Pilot scope and retained results](slopcodebench/README.md) | Scripted fixtures or an explicitly authorized live profile |

## Tool, SDK and validation fixtures

| Goal | Example | Figures or guide | Default model boundary |
|---|---|---|---|
| Local calculation and registered resources | [Native plugin](native_plugin/README.md) | [PetriNet declarations](native_plugin/figures/README.md) | No model |
| Reuse one native-add v2 package across environments | [Package reuse](package_reuse/README.md) | [Package PetriNet declaration](package_reuse/figures/README.md) | No model |
| Ten registered tools in one typed Petri net | [Atomic tool pipeline](tool_pipeline/README.md) | [PN variants and node details](tool_pipeline/figures/README.md) | Deterministic HOST tools; no model |
| Read-only SQL execution and exact ordered query comparison | [SQL query validation](query_validation/README.md) | [Native result, verifier boundaries and actual Viewer](query_validation/README.md) | Fixed candidate; no model/provider calls |
| Original JSON, CSV and Parquet inputs joined into two artifacts | [Multi-source data merge](tool_pipeline/multi_source/README.md) | [Conflicts, adapted verifier and actual Viewer](tool_pipeline/multi_source/README.md) | Deterministic HOST tools; no model |
| Bounded synthetic iteration in one Registry | [Synthetic iteration](rsi_workflows/README.md) | [One to four rounds and terminal binding](rsi_workflows/figures/README.md) | Deterministic HOSTs; no model-improvement claim |
| Validate evidence files and source identities | [Example evidence contract](example_validation/README.md) | [Read-only validation utility](example_validation/README.md) | No standalone PN, model call or business execution |

## Native input and authority fixtures

| Goal | Example | Figures or guide | Default model boundary |
|---|---|---|---|
| Separate native requester and approver | [Mock refund approval](approval_workflow/README.md) | [Actual PN and same-run Viewer](approval_workflow/README.md#recorded-petri-net) | Controlled local clients; no money or model |
| Exact external-result intake | [Controlled quote](a2a_quote/README.md) | [Actual PN and same-run Viewer](a2a_quote/README.md#recorded-petri-net) | Custom HTTP status fixtures; no external service or model |

## Historical contribution and business evidence

| Goal | Example | Figures or guide | Default model boundary |
|---|---|---|---|
| Historical route configuration contributions and business evaluation | [Route mechanisms and results](user_collaboration/README.md#historical-route-planning-mechanism-evidence) | Original 74-operation tool PN, versions and failures | Models proposed configuration outside the net; tools execute business |
| Historical sequential revisions of ten-operation billing net | [Native billing mechanism fixture](user_collaboration/native_billing/README.md) | Original business PN, proposer and adoption evidence | Deterministic source programs and tools; no agent net |

## Obtain and run an example

To take an example into your own project, start with
[dependency-complete export and customization](../docs/guides/examples.md#export-an-example-and-make-it-yours).
Named exports are `native_plugin`, `hybrid_summary`, `compose_serial`,
`package_reuse` and the default `adapter_task`; the rest remain source examples.
The [v2 native-add tutorial](../docs/guides/package-reuse-example.md) constructs a
shareable package and walks through environment preparation and genuine execution.
Use this source candidate's wheel, not the historical rc1 wheel, for these new
exports. AutomationBench has its own Python 3.13+ and pinned-upstream setup.

Additional installed entry point: the [adapter task](../docs/guides/examples.md) runs one semantic task through every supported host; its retained illustration is the recorded task PN under the DSH host. See the [business and execution PN definitions](../docs/ARCHITECTURE.md#workflow-graph-and-petrinet-lowering).

## Read the figures and evidence

Galleries distinguish initial declarations from runtime records. **PetriNet** shows the full original net; pure-tool agent overviews may be empty, and their tools use **Detailed flow**. Node details are not simplified views. Overview is a read-only agent projection, not a reduced executable PN. `registered_agent_binding` traces to the exact executable binding and agent reference; `declared_llm_executor` establishes a declaration only, not execution; `explicit_host_annotation` is an unconfirmed display annotation and cannot establish agent identity. Roles are grouped through explicit scoped identity, not equal names, model names or user accounts. An edge retains original path provenance and denotes a structural path to the next agent, not observed firing order or proof that a guard passes or an all-input join is satisfied. The full PetriNet and owning Registry retain authority, guards, joins and actual loop/turn/action, tool and product references. Managed children/actions are not additional PN transitions; independent child Registries remain independent.

ERP, AutomationBench, HarnessAudit Office and SlopCodeBench now export **1 / 1 / 4 / 1 declared roles** in their static frames. These are declaration-only identities, not new benchmark runs or runtime bindings; their original frames and images remain preserved.

Each runnable workflow creates a real Registry that can be opened with
`rpnh net --run RUN_DIR --view --no-open`. Scripted fixtures exercise the same
protocol and settlement boundaries but are not claims about model reasoning.
Run screenshots retain their original scenario scope; declaration galleries state their source and offline-compilation boundary. Routine example captures omit exact checkpoint identities and local observation timestamps. Dated result galleries may preserve original technical run identifiers when explicitly documented; these are not identity or access credentials.

For interruption/recovery behavior, use the independent-task example and the
dedicated [checkpoint recovery guide](../docs/guides/checkpoint-recovery.md).
Reopen appends a new execution generation to the same Registry; it does not
rewrite the example's final-success image or reference result.

Start with the [complete examples guide](../docs/guides/examples.md) and the
[dashboard guide](../docs/guides/viewer.md). Generated
profiles, Registry directories and provider transcripts stay outside this tree.

Public record dated 2026-10-06: freeze04 first18 has 5 PASS / 9 FAIL / 4 BLOCKED; separate repair4 has 1 PASS / 3 FAIL. The old 14 scored tasks were not rerun. [Results and limits](automationbench/PUBLIC_RESULTS_20261006.md).

The Viewer supports zoom, pan and node/arc inspection; its brief Overview shows task agents and handoffs. See the [Viewer guide](../docs/guides/viewer.md).

## Multi-user release handoff: independent agents and shared resources

The [complete example](multiuser_release_handoff/README.md) follows four controlled participants with independent Tasks, Registries and private agent PNs, plus a separate fifth Source Registry for resource control. Native checkpoint restoration completed v2, SQL, the QA checklist, incident and final handoffs; the final read-only audit passed 98/98 required checks. Source is not an agent. Original failures and two optional UNKNOWN history-coverage checks remain explicit.

[Actual role PNs and Source details](multiuser_release_handoff/figures/README.md) · [Original task and run prerequisites](multiuser_release_handoff/RUNNING.md) · [Exact historical results and limits](multiuser_release_handoff/RESULTS.md). New model runs require authorization; this is not an official benchmark score, deployment or executed rollback validation.
