---
name: rpnh-user-collaboration-results-20261012
description: "一次受控原生协作实验及其实际指标取舍。"
metadata:
  document-kind: result-report
  audience: application-developer-and-researcher
  language: zh-CN
  counterpart: README.md
  revision: "2026-10-12.2"
  status: recorded-result
---

[English](README.md) | 中文 | [结果索引](../README_ZH.md)

# 受控多用户协作：2026 年 10 月 12 日

三个独立受控的**模拟 Registry 身份／会话**、两种真实模型条件以及同用户切换模型，完成了原生提案 → 审阅 → 显式协调 → 保护分支合并 → 实际采用 → 业务执行 → 独立评估的闭环。两个固定 512 条路线集合的官方序列分数下降，平均行程时间同时增加。本例展示一次可核查的端到端能力，不构成模型排名或协作收益的因果估计。

[协作过程图、业务全景与本地结构 Viewer](../../../examples/user_collaboration/README_ZH.md) · [精确汇总数值](results.json) · [源码与图片身份](SOURCES.json) · [协作 API](../../guides/user-collaboration_ZH.md)

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

## Harness 证据具体证明了什么

业务对照是**确定性 nearest-neighbor 与模型创作的另一组已有确定性规划策略配置**，两个业务臂都不逐路线调用 LLM。模型 usage 属于提案创作、修订和失败尝试。本轮没有固定模型、任务、输入、工具能力及资源条件，再将 RPNH 与成熟替代 harness 配对的实验。

| 本例已观察的能力 | 具体证据 |
|---|---|
| 身份与预算连续 | B1/B2 使用同一 actor/session，换 profile 不清零 3 + 1 次调用，失败尝试也保留计数 |
| 区分工具完成与有效工作 | A3/A4 完成工具／TEXT 处理，仍被提案校验拒绝 |
| 用实际动作核对模型自述 | B2 文字声称保留自己的贡献，实际编辑却移除 batch 11；该提案未采用 |
| 查明采用内容与来源 | M2 连接 B1/C2、共同祖先 A5、owner 选择及实际采用回执；未采用 B2 的值相同不代表被采用 |

这些记录可以支持具体的失败定位与来源核证，但尚未测量另一个 agent 是否比使用替代 harness 查得更准、更快、token 更少或重复工作更少。可追溯性是已观察到的具体能力，比较性的工程收益仍未检验。

## 路线取舍发生在哪里

以下图形使用全部 **1,024 条原路线配对**，没有重新评分、删去异常点或新增样本。`raw_travel_seconds` 是路线行程矩阵代价累加，不是程序 walltime、服务时间或真实车队某一天预测。[机器可读细分析](ANALYSIS.json)保留分母、精确数值与缺字段覆盖。

![开发与评估两域的配对官方分数和行程变化](figures/01_paired_score_travel.png)

**配对结果。** Candidate 同时改善分数与行程的路线是开发 98/512、评估 51/512；baseline 两轴都更好的有 181、168 条。分数改善但行程增加的有 226、269 条；余下各域 6 条分数变差但行程缩短，以及 1／18 条完全不变。整体均值也是分数更好、行程更长，两配置在这两个轴上互不支配。这些路线点不是可互相替代的系统工作点，更不证明全局 Pareto 最优；时间窗 UNKNOWN 仍是另一项约束限制。

![按固定策略组划分的配对行程增量分布](figures/02_travel_delta_distribution.png)

**分布与尾部。** 配对行程增量中位数为开发 +574.45 秒、评估 +524.15 秒，P95 为 +2,994.39／+2,855.34 秒。407/512、437/512 条路线变长，涉及 23/24、24/24 个 batch；变化广泛存在，`deadline_first` 尾部更长。箱体为中间 50% 与中位数，须为 P5–P95 阈值内最远实际观测；表中精确分位数采用 `(n−1)p` 线性插值。配对增量中位数不同于两臂中位数相减。

![策略组对净行程增长的贡献与正增长集中度](figures/03_growth_contributions.png)

**按组记账。** `deadline_first` 只覆盖开发 217/512、评估 190/512 条路线，却占各域**净**行程增长的 85.29%、69.10%。增量最大的 52 条占开发／评估**只累加正增量**的 35.37%／37.62%，这个分母不扣缩短部分。左图扣除节省，右图不扣；累加小时描述固定路线集，不是计算机运行时间。策略组非随机，这些是描述性分解，不给 actor／模型分配信用，也不是策略的因果估计。

## 已登记模型 usage

22 份已登记 response envelope 都有 input/output 字段，覆盖保留的创作响应，也包括失败尝试；不代表底层 HTTP 重试或计费覆盖完整。

| 已记录 transport／model／effort | 响应数 | Input tokens | Output tokens | 其它已记录字段 |
|---|---:|---:|---:|---|
| Native app-server／gpt-5.6-terra／medium | 19 | 661,047 | 7,682 | cached 字段 19/19 均为 0；provider total 缺失 |
| ExternalProviderInputPort／deepseek-v4-pro／未声明 | 3 | 63,694 | 3,836 | provider total 为 67,530，覆盖 3/3；cached 字段缺失 |

Terra 的 input+output 算术和为 668,729，不是已保存的 provider total 字段。两种模型均缺单列 reasoning token 与费用字段；缺失为 UNKNOWN，不能填 0。不同 tokenizer、provider 计数以及 cache／reasoning 包含关系没有证明等价，不给跨模型总和或货币费用估算。每轮 input 重复计入上下文。失败 A2/A3/A4 的 13 条 Terra 响应包括 input 528,958、output 5,250；这只是处置分类，不是已测的可避免浪费。B2 也保留，未被静默剔除。

22 份响应没有可用请求耗时。现有安全证据只有部分 HOST 命令粗粒度 walltime，没有完整成功时序、CPU 或可相加的阶段 span。Registry 时间戳、逻辑顺序与 schema 声明的计时字段都不能代替实测操作耗时。模型等待、工具 IO、发布／结算、调度与评分的时间占比目前未知。不同 owner 历史与适配也不支持公平速度比较；收尾 wheel＋overlay 身份不能独立证明每个早期运行窗口全栈字节完全相同。

## 范围、复用与限制

- 固定评估总体被保留。原最大同站 batch 被确定性拆成相邻两半以形成 24 个 batch，没有改变路线成员、顺序或 512 分母。
- 本地可用评估全集为 3,052 条，数据集说明为 3,072 条；20 条差异尚未解释，没有补造缺失数据。
- 创作尝试并非全部 clean 成功。B2 的冲突指导与 C2 后续协调是已观察条件，不是提示或模型的控制实验。不能据此声称普遍优势、新路线算法或任意规模协作。
- 公开材料支持离线读结果，以及交互查看**静态声明结构**。两张 PNG 是原始 runtime 截图。完整实验依赖已有本机安装、已准入数据、私有模型 profile 与保留的 Registry；此目录不是独立 fresh-trial 实验，也不是这些状态的公开交互重放。
- 本次发布没有重复模型调用、业务实验或评分。后续产品修改不会倒改本轮实际测试的源码身份。

## 参考资料与数据条款

[Amazon Last Mile Routing Research Challenge 数据集记录](https://registry.opendata.aws/amazon-last-mile-challenges/)给出数据说明及 **CC BY-NC 4.0** 条款；RPNH 的软件许可不替代数据条款。本目录不含原始路线、包裹标签、行程矩阵、凭据或私有 Registry／对话材料。数据方法见 [Merchán 等，Transportation Science，DOI 10.1287/trsc.2022.1173](https://pubsonline.informs.org/doi/10.1287/trsc.2022.1173)。

评分使用 [MIT-CAVE/rc-cli `fdafa50e8b8a18d4cbff112ec432011952b92c7d` 的 `scoring/score.py`](https://github.com/MIT-CAVE/rc-cli/blob/fdafa50e8b8a18d4cbff112ec432011952b92c7d/scoring/score.py)，SHA-256 `4ab51620391b3b5d131720518d2ce4c346f0eedb3d68afcf1ae9174b9e6b1540`。[评分说明](https://github.com/MIT-CAVE/rc-cli/blob/fdafa50e8b8a18d4cbff112ec432011952b92c7d/scoring/README.md)定义路线分数与均值；其 [MIT 许可](https://github.com/MIT-CAVE/rc-cli/blob/fdafa50e8b8a18d4cbff112ec432011952b92c7d/license)与数据集许可分别适用。
