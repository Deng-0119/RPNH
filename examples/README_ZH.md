# RPNH 用户案例

[English](README.md) | 中文

可以按希望观察的行为选择案例：

| 目标 | 案例 | 默认模型边界 |
|---|---|---|
| 本地计算与登记资源 | [原生插件](native_plugin/README_ZH.md) | 不使用模型 |
| 串行模型—程序—模型计算 | [混合汇总](hybrid_summary/README_ZH.md) | 脚本替身；可传入精确真实 profile |
| 串行、并行、文档和长流程结构 | [工作流模式案例库](workflow_patterns/README_ZH.md) | 脚本替身；可传入精确真实 profile |
| 两个独立管理的子任务 | [任务工作区](task_workspace/README_ZH.md) | 脚本替身 |
| 定义操作与真实 replacement | [原生网操作](net_operations/README_ZH.md) | 纯定义操作加一个真实任务 |
| 通过全部支持宿主运行同一语义任务 | `rpnh examples export --output DIR` | 用户自有精确真实 profile |

每个可运行 workflow 都会创建真实 Registry，可用
`rpnh net --run RUN_DIR --view --no-open` 打开。脚本替身经过相同协议与结算边界，但不代表
模型推理能力。

请从[完整案例指南](../docs/guides/examples_ZH.md)和
[看板指南](../docs/guides/viewer_ZH.md)开始。生成的 profile、Registry 目录和 provider
transcript 均保存在仓库之外。
