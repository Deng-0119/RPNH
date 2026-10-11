# RPNH user examples

English | [中文](README_ZH.md)

To take an example into your own project, start with
[dependency-complete export and customization](../docs/guides/examples.md#export-an-example-and-make-it-yours).
Named exports are `native_plugin`, `hybrid_summary`, `compose_serial`,
`package_reuse` and the default `adapter_task`; the rest remain source examples.
The [v2 native-add tutorial](../docs/guides/package-reuse-example.md) constructs a
shareable package and walks through environment preparation and genuine execution.
Use this source candidate's wheel, not the historical rc1 wheel, for these new
exports. AutomationBench has its own Python 3.13+ and pinned-upstream setup.

Choose an example by the behavior you want to see:

| Goal | Example | Figures or guide | Default model boundary |
|---|---|---|---|
| Local calculation and registered resources | [Native plugin](native_plugin/README.md) | [PetriNet declarations](native_plugin/figures/README.md) | No model |
| Model–program–model calculation | [Hybrid summary](hybrid_summary/README.md) | [Detailed flow](hybrid_summary/README.md) | Scripted; optional exact live profile |
| Serial, parallel, document and long workflow shapes | [Workflow patterns](workflow_patterns/README.md) | [PN and simplified-view gallery](workflow_patterns/README.md) | Scripted; optional exact live profile |
| Two independently managed child tasks | [Task workspace](task_workspace/README.md) | [Child-task PetriNet](task_workspace/README.md) | Scripted |
| Definition operations and live replacement | [Native net operations](net_operations/README.md) | [Adopted replacement PetriNet](net_operations/README.md) | Definition-only plus one live task |
| Reuse one native-add v2 package across environments | [Package reuse](package_reuse/README.md) | [Package PetriNet declaration](package_reuse/figures/README.md) | No model |
| Ten registered tools in one typed Petri net | [Atomic tool pipeline](tool_pipeline/README.md) | [PN variants and node details](tool_pipeline/figures/README.md) | Deterministic HOST tools; no model |
| Bounded synthetic iteration in one Registry | [Synthetic iteration](rsi_workflows/README.md) | [One to four rounds and terminal binding](rsi_workflows/figures/README.md) | Deterministic HOSTs; no model-improvement claim |
| Real clinical document/data packet | [JB steering packet](jb_steering_packet/README.md) | [Accepted workflow Overview](jb_steering_packet/README.md) | User-owned exact live profile |
| Real numerical optimal-control task | [3-DOF powered descent](three_dof_powered_descent/README.md) | [Accepted workflow Overview](three_dof_powered_descent/README.md) | User-owned exact live profile |
| Office benchmark integration and scored results | [HarnessAudit Office](harnessaudit_office/README.md) | [Run and viewer guide](harnessaudit_office/README.md) | Read results offline; run/score require explicit local profiles |
| Two-round Policy evolution with independent child runs | [RRSI v0.6](rrsi_v06/README.md) | [Policy and role PetriNet declarations](rrsi_v06/README.md#petrinet-declarations) | User-owned exact live profile |
| Business-workflow benchmark and host extensions | [AutomationBench](automationbench/README.md) | [Results, task boundaries and viewer guide](automationbench/README.md) | Read retained results offline; new live runs require an authorized profile |
| Managed ERP tasks with the original verifier | [ERP-Bench](erp_bench/README.md) | [Task and execution guide](erp_bench/README.md) | User-owned exact live profile and original Docker environment |
| Native Session commands for a coding-task prefix | [SlopCodeBench pilot](slopcodebench/README.md) | [Pilot scope and retained results](slopcodebench/README.md) | Scripted fixtures or an explicitly authorized live profile |
| Validate evidence files and source identities | [Example evidence contract](example_validation/README.md) | [Read-only validation utility](example_validation/README.md) | No standalone PN, model call or business execution |

Additional installed entry point: the [adapter task](../docs/guides/examples.md) runs one semantic task through every supported host; its retained illustration is the DSH execution net.

The galleries distinguish initial declarations from recorded runs. **PetriNet** shows all places, transitions and arcs. **Overview** is the Agent-only simplification; pure-tool nets use **Detailed flow**. Node-detail screenshots remain details of the PN and are not simplified views.

Each runnable workflow creates a real Registry that can be opened with
`rpnh net --run RUN_DIR --view --no-open`. Scripted fixtures exercise the same
protocol and settlement boundaries but are not claims about model reasoning.
Run screenshots retain their original scenario scope; declaration galleries state their source and offline-compilation boundary. Public images omit exact checkpoint identities and local observation timestamps.

For interruption/recovery behavior, use the independent-task example and the
dedicated [checkpoint recovery guide](../docs/guides/checkpoint-recovery.md).
Reopen appends a new execution generation to the same Registry; it does not
rewrite the example's final-success image or reference result.

Start with the [complete examples guide](../docs/guides/examples.md) and the
[dashboard guide](../docs/guides/viewer.md). Generated
profiles, Registry directories and provider transcripts stay outside this tree.

Public record dated 2026-10-06: freeze04 first18 has 5 PASS / 9 FAIL / 4 BLOCKED; separate repair4 has 1 PASS / 3 FAIL. The old 14 scored tasks were not rerun. [Results and limits](automationbench/PUBLIC_RESULTS_20261006.md).
