# 多个用户共同构建同一任务网络

[English](README.md) | 中文 | [详细结果与分析](../../docs/results/user-collaboration-20261012/README_ZH.md)

需要可独立运行、无需模型的示例，可从[原生电费协作](native_billing/README_ZH.md)开始：三个受控本地主体、连续两项贡献进入同一采用网，并提供真实 Viewer 截图。下方路线规划研究继续保留原结果与证据。

三个独立受控的模拟 Registry 身份 A/B/C，共同修改同一个 24 批次路线规划任务网的配置。各自有独立 actor 和 session，由同一个可信 HOST 协调；它们不是三位真人或三个外部账号。模型提出修改，owner 接受一个版本，再由确定性 HOST 工具执行采用后的业务 Petri 网。

## 先看官方 Viewer 中已采用的任务网

[![原 adopted-M2 runtime 的 Detailed flow 全景：全部 24 个批次分支与最终汇合](figures/adopted-M2.png)](figures/adopted-M2.png)

这张未修改的 4485 × 5000 截图来自原 **development adopted-M2 runtime**：已准入路线输入、全部 24 个 ingest → plan → validate 分支、聚合、发布和结果。截图有 74 次已结算 firing、零 unsettled。Detailed flow 折叠 97 个交接 place，从完整 197 节点／220 弧的 Petri 网展示 100 节点／123 边。打开原图可查看全网。

卡片对应业务操作。A/B/C 是下文贡献记录中的配置作者，并不是这张纯工具 PN 上的三个运行时 Agent 节点。截图展示执行状态，没有按作者着色或作者／版本徽标。

## 各方具体修改了哪些操作

保留的配置声明将规划操作命名为 `batch_0.plan` 到 `batch_23.plan`。以下修改针对各操作的 `config.strategy`；place、transition、arc 的拓扑和可用算法保持不变。

| 参与者与原提案 | 涉及的规划操作 | 已采用 M2 中保留的内容 |
| --- | --- | --- |
| A，共同版本 A5 | `batch_0.plan`–`batch_10.plan` | 11 项从 `nearest_neighbor` 改为 `deadline_first` |
| B，基于 A5 的独立提案 B1 | `batch_11.plan`–`batch_17.plan` | `zone_nearest`；batch 11 为 B 独有，12–17 与 C1 重叠 |
| C，基于 A5 的独立提案 C1 | `batch_12.plan`–`batch_23.plan` | `zone_nearest`；batch 18–23 为 C 独有，12–17 与 B1 重叠 |
| C，协调版本 C2 | 在 C1 中补入 B 的 batch 11 修改 | 形成 batch 11–23 的完整 `zone_nearest` 配置，同时保留 A 的 0–10 配置 |
| A，owner 决策 | 在原 B1/C2 合并中选择 incoming C2，推进保护分支并采用 M2 | 接受后的配置用于上图所示业务运行 |

此表将已公开 module 声明与[回执派生的贡献记录](visuals/collaboration-evidence.json)对应，是文档中的核对说明，不是 Viewer 导出的节点归因叠层。B/C 重叠的六项修改在最终内容中只计一次；C2 吸收 B 的修改是复用，不能再计为 C 的独立贡献。B2 未被采用。[详细结果记录](../../docs/results/user-collaboration-20261012/README_ZH.md)保留未通过的提案、记录中的协调与合并决策、模型 usage 及证据范围。

## 用原始 baseline 对照

[![原 baseline runtime 的 Detailed flow 全景](figures/baseline.png)](figures/baseline.png)

这张同样未修改的 4485 × 5000 截图来自原 **development baseline**，拓扑相同，全部 24 个规划操作均使用 `nearest_neighbor`。它也有 74 次已结算 firing、零 unsettled。

两图保留原技术 checkpoint 标识与观察时间，指向被记录的执行状态，不是个人或账号身份，也不是访问凭据。PNG 不含文本／EXIF 元数据。两图没有重画、改标或冒充新运行；精确字节身份见 [SOURCES.json](../../docs/results/user-collaboration-20261012/SOURCES.json)。执行完成不等于路线业务约束全部通过。

M2 与 baseline 在固定 development／eval 各 512 条路线上评估：官方序列分数降低约 **7.525%／8.376%**，平均行程成本增加约 **7.715%／8.421%**。时间窗仍有失败和未知。这些结果不衡量协作带来的因果收益，详见[完整结果与取舍](../../docs/results/user-collaboration-20261012/README_ZH.md)。

## 用官方 Viewer 打开静态结构

在源码 checkout 中，使用已有且兼容的 RPNH 环境：

```bash
python examples/view_declaration.py collaboration-baseline
python examples/view_declaration.py collaboration-adopted
```

加 `--no-open` 可只打印实际回环 URL，不自动打开浏览器。Viewer 中选择 **PetriNet** 查看全部 place／transition／arc；纯工具流程可选择 **Detailed flow**。平移、缩放和节点查看均使用已有官方 UI。这两份内置帧不需要模型、数据集、provider profile 或保留的 runtime Registry。

命令显示从已记录 baseline／adopted module 编译的 **initial_configured 静态声明**，不含数据载荷或 runtime 身份。空／未标记状态属于声明状态，不是 PNG 中的已完成执行；没有执行历史、checkpoint 重放或 live run。两声明拓扑相同，candidate 在 batch 0–10 使用 `deadline_first`、11–23 使用 `zone_nearest`，替换 baseline 全部采用的 `nearest_neighbor`。完整配置声明分别见 [baseline](declarations/BASELINE_MODULE.json) 与 [adopted M2](declarations/ADOPTED_M2_MODULE.json)。

## 本目录可复现的范围

可以阅读公开汇总结果、检查两张保留的 runtime 全景、打开两份静态结构。完整真实模型实验并非下载即可独立运行：它使用已有安装、已准入路线数据、用户自有 profile 与私有 Registry 状态。数据条款及评分器／源码身份在报告中。静态 Viewer 不代表私有 runtime 的公开重放。

构建协作应用可看[原生协作指南](../../docs/guides/user-collaboration_ZH.md)；需要小型独立可执行 workflow 时，从[工作流模式](../workflow_patterns/README_ZH.md)开始。
