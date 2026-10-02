# RPNH user examples

English | [中文](README_ZH.md)

Choose an example by the behavior you want to see:

| Goal | Example | Actual dashboard | Default model boundary |
|---|---|---|---|
| Local calculation and registered resource | [Native plugin](native_plugin/README.md) | ![PetriNet](native_plugin/assets/native-plugin-petrinet.png) | No model |
| Serial model–program–model calculation | [Hybrid summary](hybrid_summary/README.md) | ![Detailed flow](hybrid_summary/assets/hybrid-summary-flow.png) | Scripted; optional exact live profile |
| Serial, parallel, document and long workflow shapes | [Workflow pattern gallery](workflow_patterns/README.md) | ![Parallel overview](workflow_patterns/assets/parallel-overview.png) | Scripted; optional exact live profile |
| Two independently managed child tasks | [Task workspace](task_workspace/README.md) | ![Child PetriNet](task_workspace/assets/independent-task-petrinet.png) | Scripted |
| Definition operations and live replacement | [Native net operations](net_operations/README.md) | ![Adopted net](net_operations/assets/live-replacement-petrinet.png) | Definition-only plus one live task |
| One semantic task through every supported host | [Installed adapter task](../docs/guides/examples.md) | ![DSH execution net](../cpn/examples/adapter_task/assets/dsh-petrinet.png) | User-owned exact live profile |
| Real clinical document/data packet | [JB steering packet](jb_steering_packet/README.md) | ![JB workflow](jb_steering_packet/assets/jb-steering-petrinet.png) | User-owned exact live profile |
| Real numerical optimal-control task | [3-DOF powered descent](three_dof_powered_descent/README.md) | ![3-DOF workflow](three_dof_powered_descent/assets/three-dof-petrinet.png) | User-owned exact live profile |
| Office benchmark integration and scored results | [HarnessAudit Office](harnessaudit_office/README.md) | Viewer command for user-generated runs; no archived screenshot | Read results offline; run/score require explicit local profiles |
| Recursive role evolution with independent child runs | [RRSI v0.6 application](rrsi_v06/README.md) | Viewer command for user-generated child Registries; no archived screenshot | User-owned exact live profile |
| Public business-workflow benchmark and task-level scores | [AutomationBench](automationbench/README.md) | Viewer command for user-generated runs; no archived screenshot | Read retained results offline; live run requires an authorized profile |

Each runnable workflow creates a real Registry that can be opened with
`rpnh net --run RUN_DIR --view --no-open`. Scripted fixtures exercise the same
protocol and settlement boundaries but are not claims about model reasoning.
Every linked image comes from the named run type; public copies omit exact
checkpoint identities and local observation timestamps.

For interruption/recovery behavior, use the independent-task example and the
dedicated [checkpoint recovery guide](../docs/guides/checkpoint-recovery.md).
Reopen appends a new execution generation to the same Registry; it does not
rewrite the example's final-success image or reference result.

Start with the [complete examples guide](../docs/guides/examples.md) and the
[dashboard guide](../docs/guides/viewer.md). Generated
profiles, Registry directories and provider transcripts stay outside this tree.
