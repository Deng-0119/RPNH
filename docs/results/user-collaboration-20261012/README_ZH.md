---
name: rpnh-user-collaboration-results-20261012
description: "一次受控原生协作实验及其实际指标取舍。"
metadata:
  document-kind: result-report
  audience: application-developer-and-researcher
  language: zh-CN
  counterpart: README.md
  revision: "2026-10-12.1"
  status: recorded-result
---

[English](README.md) | 中文 | [结果索引](../README_ZH.md)

# 受控多用户协作：2026 年 10 月 12 日

三个独立受控的**模拟 Registry 身份／会话**、两种真实模型条件以及同用户切换模型，完成了原生提案 → 审阅 → 显式协调 → 保护分支合并 → 实际采用 → 业务执行 → 独立评估的闭环。两个固定 512 条路线集合的官方序列分数下降，平均行程时间同时增加。本例展示一次可核查的端到端能力，不构成模型排名或协作收益的因果估计。

[完整流程全景与本地结构 Viewer](../../../examples/user_collaboration/README_ZH.md) · [精确汇总数值](results.json) · [源码与图片身份](SOURCES.json) · [协作 API](../../guides/user-collaboration_ZH.md)

## 条件与实际过程

A、B、C 是三个独立受控 Registry 身份，并非三位真人或外部账号。A、C 使用 `gpt-5.6-terra`（medium）；B 先使用 `deepseek-v4-pro`（effort 未声明），再在**同一 B 会话与累计预算**下切换到 Terra。Terra 经原生 Codex app-server 0.162.0，DeepSeek 经已有 external-provider input port。这些是本轮记录条件，不是读者必须采用的版本。

实际运行使用源码 `c26cf943eb9d32d14646fe411da6332ace9a3c24`、已安装 wheel SHA-256 `66e55af8ff6e7a8156441e8a6f622628002d62cba7bb28d2259015ab501fbad4`，并叠加 [SOURCES.json](SOURCES.json) 标明的八份 runtime 修改，不是未修改 wheel 的运行。单个可信 HOST coordinator 保留原身份、权限与 Registry 计数。作者使用有界输入，评分标签保留给分离的 evaluator。

实际累计 **22 次 logical call、22 个 provider-attempt spec**：A 15、B 4、C 3，没有超限调用，失败的创作尝试也在其中。底层 HTTP 尝试次数未知。配置上限为合计 72、每 actor 跨 profile 累计 24、每 node 12，同时保留原任务中更紧的额度。

| 阶段 | 实际结果 |
|---|---|
| A1–A4 | 保留工具顺序、历史大小或无效 PN 失败；A1 无模型调用 |
| A5 | 有效提案：batch 0–10 使用已有 `deadline_first` |
| B1 | 独立提案：batch 11–17 使用已有 `zone_nearest` |
| C1 | 独立提案：batch 12–23 使用 `zone_nearest` |
| B2 | 同一 B 切换模型后撤回 batch 11，未通过审阅，也未采用 |
| C2 | 在明确的 peer 协调后，保留 B 的 batch 11 与 C 的 batch 18–23 |
| M2 | 原两父合并 B1/C2；owner 选择已经组装好的 incoming C2，保护分支推进并真正采用 |

B1/C1 在六个 batch 上重叠。共享的 74 次业务 operation 预算触发 coupled-contract 合并冲突。策略联合先由 C2 形成，再由 owner 在原合并中选择。因此这里展示的是**显式协调后的原生合并采用**，不是自动无冲突融合。C2 复制 B 的修改不能再计为 C 的独立发明；A 的 11 项修改仍被保留。实际只改了已有策略配置，图拓扑与可用算法没有变化。

## 测得结果

development 与 eval 各固定 512 条路线，分别运行 baseline 与已采用 candidate。四个原生业务臂均完成，每臂 74 次 firing、98 个产品与 25 个入口资源。两个分离的原生 evaluator 使用同一固定原评分器，并非两套独立实现的算法。四臂均有 512 个有限分数、512 个排列／结构有效序列、512 条容量 PASS，记录的 scorer error 为零。

| 指标 | Development baseline → candidate | Eval baseline → candidate |
|---|---:|---:|
| 官方序列分数，越低越好 | 0.109175 → 0.100959（−7.525%） | 0.103382 → 0.094723（−8.376%） |
| 平均行程，秒 | 10,998.19 → 11,846.74（+7.715%） | 10,173.91 → 11,030.64（+8.421%） |
| 时间窗 FAIL / UNKNOWN | 182 / 330 → 75 / 437 | 161 / 351 → 79 / 433 |
| 分数降低 / 不变 / 升高的路线 | 324 / 1 / 187 | 320 / 18 / 174 |

`VALID` 仅指排列／结构有效。时间窗 UNKNOWN 不能当成 PASS。配对记录中，开发 126 条、评估 99 条从 FAIL→UNKNOWN；另有 19 条、17 条从 UNKNOWN→FAIL。每对的已检查／未知包裹数量相同。因此观测违例减少并不等于所有约束满足。开发 407/512、评估 437/512 条路线行程增加。

非随机 `deadline_first` 组的平均序列分数变差（开发 +0.027285／评估 +0.030937），`zone_nearest` 组改善（−0.034330／−0.032023）；两组平均行程都增加。这些分组描述、重叠修改以及提示、transport、尝试次数差异，不能给 actor、模型或切换分配独立因果收益。

## 范围、复用与限制

- 固定评估总体被保留。原最大同站 batch 被确定性拆成相邻两半以形成 24 个 batch，没有改变路线成员、顺序或 512 分母。
- 本地可用评估全集为 3,052 条，数据集说明为 3,072 条；20 条差异尚未解释，没有补造缺失数据。
- 创作尝试并非全部 clean 成功。B2 的冲突指导与 C2 后续协调是已观察条件，不是提示或模型的控制实验。不能据此声称普遍优势、新路线算法或任意规模协作。
- 公开材料支持离线读结果，以及交互查看**静态声明结构**。两张 PNG 是原始 runtime 截图。完整实验依赖已有本机安装、已准入数据、私有模型 profile 与保留的 Registry；此目录不是独立 fresh-trial 实验，也不是这些状态的公开交互重放。
- 本次发布没有重复模型调用、业务实验或评分。后续产品修改不会倒改本轮实际测试的源码身份。

## 参考资料与数据条款

[Amazon Last Mile Routing Research Challenge 数据集记录](https://registry.opendata.aws/amazon-last-mile-challenges/)给出数据说明及 **CC BY-NC 4.0** 条款；RPNH 的软件许可不替代数据条款。本目录不含原始路线、包裹标签、行程矩阵、凭据或私有 Registry／对话材料。数据方法见 [Merchán 等，Transportation Science，DOI 10.1287/trsc.2022.1173](https://pubsonline.informs.org/doi/10.1287/trsc.2022.1173)。

评分使用 [MIT-CAVE/rc-cli `fdafa50e8b8a18d4cbff112ec432011952b92c7d` 的 `scoring/score.py`](https://github.com/MIT-CAVE/rc-cli/blob/fdafa50e8b8a18d4cbff112ec432011952b92c7d/scoring/score.py)，SHA-256 `4ab51620391b3b5d131720518d2ce4c346f0eedb3d68afcf1ae9174b9e6b1540`。[评分说明](https://github.com/MIT-CAVE/rc-cli/blob/fdafa50e8b8a18d4cbff112ec432011952b92c7d/scoring/README.md)定义路线分数与均值；其 [MIT 许可](https://github.com/MIT-CAVE/rc-cli/blob/fdafa50e8b8a18d4cbff112ec432011952b92c7d/license)与数据集许可分别适用。
