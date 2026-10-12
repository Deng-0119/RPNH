# 受控用户协作：结果与图结构查看

[English](README.md) | 中文 | [实测报告](../../docs/results/user-collaboration-20261012/README_ZH.md)

本例记录一次三个受控模拟 Registry 身份、两种真实模型条件下的原生端到端协作。报告给出提案／采用链、固定 512 条路线对照、失败与业务指标取舍。

## 原始 runtime 全景

以下两张未修改的 4485 × 5000 PNG 来自原 **development** baseline 与 adopted-M2 runtime Registry 的官方 RPNH Viewer。每张都包含全部 24 个 batch 分支、聚合、发布和结果；截图有 74 次已结算 firing、零 unsettled。Detailed flow 折叠 97 个交接 place，从完整 197 节点／220 弧的 Petri 网展示 100 节点／123 边。打开原图可查看全流程。

### Baseline

[![原 baseline runtime 的 Detailed flow 全景](figures/baseline.png)](figures/baseline.png)

### Adopted M2

[![原 adopted-M2 runtime 的 Detailed flow 全景](figures/adopted-M2.png)](figures/adopted-M2.png)

原 footer 保留技术 checkpoint 标识与观察时间，指向被记录的执行状态，不是个人或账号身份，也不是访问凭据。PNG 不含文本／EXIF 元数据。两图没有重画、改标或冒充云端新运行；精确字节身份见 [SOURCES.json](../../docs/results/user-collaboration-20261012/SOURCES.json)。执行完成不等于路线业务约束全部通过。

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
