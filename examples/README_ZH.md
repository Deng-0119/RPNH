# RPNH 用户案例

[English](README.md) | 中文

可以按希望观察的行为选择案例：

| 目标 | 案例 | 实际 dashboard | 默认模型边界 |
|---|---|---|---|
| 本地计算与登记资源 | [原生插件](native_plugin/README_ZH.md) | ![PetriNet](native_plugin/assets/native-plugin-petrinet.png) | 不使用模型 |
| 串行模型—程序—模型计算 | [混合汇总](hybrid_summary/README_ZH.md) | ![Detailed flow](hybrid_summary/assets/hybrid-summary-flow.png) | 脚本替身；可传入精确真实 profile |
| 串行、并行、文档和长流程结构 | [工作流模式案例库](workflow_patterns/README_ZH.md) | ![并行概览](workflow_patterns/assets/parallel-overview.png) | 脚本替身；可传入精确真实 profile |
| 两个独立管理的子任务 | [任务工作区](task_workspace/README_ZH.md) | ![子任务 PetriNet](task_workspace/assets/independent-task-petrinet.png) | 脚本替身 |
| 定义操作与真实 replacement | [原生网操作](net_operations/README_ZH.md) | ![已采用的网](net_operations/assets/live-replacement-petrinet.png) | 纯定义操作加一个真实任务 |
| 通过全部支持宿主运行同一语义任务 | [安装版适配任务](../docs/guides/examples_ZH.md) | ![DSH 执行网](../cpn/examples/adapter_task/assets/dsh-petrinet.png) | 用户自有精确真实 profile |
| 真实临床文档／数据 packet | [JB steering packet](jb_steering_packet/README_ZH.md) | ![JB workflow](jb_steering_packet/assets/jb-steering-petrinet.png) | 用户自有精确真实 profile |
| 真实数值最优控制任务 | [3-DOF 动力下降](three_dof_powered_descent/README_ZH.md) | ![3-DOF workflow](three_dof_powered_descent/assets/three-dof-petrinet.png) | 用户自有精确真实 profile |

每个可运行 workflow 都会创建真实 Registry，可用
`rpnh net --run RUN_DIR --view --no-open` 打开。脚本替身经过相同协议与结算边界，但不代表
模型推理能力。每张链接图片都来自对应类型的 run；公开副本移除了精确 checkpoint 身份和
本地观察时间。

中断／恢复行为请使用独立任务案例和专门的
[checkpoint 恢复指南](../docs/guides/checkpoint-recovery_ZH.md)。Reopen 会在同一 Registry
中追加新的执行代次，不会改写案例的最终成功图片或参考结果。

请从[完整案例指南](../docs/guides/examples_ZH.md)和
[看板指南](../docs/guides/viewer_ZH.md)开始。生成的 profile、Registry 目录和 provider
transcript 均保存在仓库之外。
