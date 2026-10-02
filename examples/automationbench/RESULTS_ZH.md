# 18 题分层 pilot 保留结果

[English](RESULTS.md) | [示例](README_ZH.md) | [精确条件](EXPERIMENT_ZH.md) | [公开比较](COMPARISON_ZH.md)

日期：2026-10-02。executor 标签：`deepseek-v4-pro`。该标签只记录本次本地配置路线，不构成
项目支持模型承诺。

## 严格首轮

- 18/18 个选中任务均已尝试。
- 17/18 达到 native `host_terminal`、host/world-owner 双重静止并完成上游评分。
- 8/18 的 `task_completed_correctly=1.0`。
- 17 个可评分任务的平均 `partial_credit` 为 0.8877005348。
- 共 437 次真实模型调用和 1,081 次成功业务工具分派。

保留基础设施失败槽位时，严格 cohort 结果是 `8/18 = 44.44%`。`8/17 = 47.06%` 只是在已评分
子集上的诊断值，不是完整 benchmark pass rate。

| # | 任务 | 域 | 宽度 | 基础设施 | 满分 | Partial | 模型调用 | 工具分派 |
|---:|---|---|---|---:|---:|---:|---:|---:|
| 1 | `finance-0019` | finance | focused | 1 | 0 | 0.7500 | 29 | 58 |
| 2 | `finance-0015` | finance | standard | 1 | 0 | 0.9091 | 30 | 59 |
| 3 | `finance-0076` | finance | broad | 1 | 1 | 1.0000 | 42 | 113 |
| 4 | `hr-0009` | hr | focused | 1 | 0 | 0.8333 | 19 | 30 |
| 5 | `hr-0035` | hr | standard | 1 | 1 | 1.0000 | 21 | 56 |
| 6 | `hr-0072` | hr | broad | 1 | 1 | 1.0000 | 34 | 86 |
| 7 | `marketing-0083` | marketing | focused | 1 | 0 | 0.6667 | 25 | 106 |
| 8 | `marketing-0043` | marketing | standard | 1 | 1 | 1.0000 | 33 | 101 |
| 9 | `marketing-0086` | marketing | broad | 1 | 1 | 1.0000 | 31 | 67 |
| 10 | `operations-0009` | operations | focused | 0 | — | — | 9 | 21 |
| 11 | `operations-0047` | operations | standard | 1 | 0 | 0.9167 | 18 | 38 |
| 12 | `operations-0028` | operations | broad | 1 | 0 | 0.4000 | 25 | 49 |
| 13 | `sales-0002` | sales | focused | 1 | 1 | 1.0000 | 28 | 76 |
| 14 | `sales-0034` | sales | standard | 1 | 1 | 1.0000 | 28 | 55 |
| 15 | `sales-0065` | sales | broad | 1 | 1 | 1.0000 | 26 | 66 |
| 16 | `support-0058` | support | focused | 1 | 0 | 0.7273 | 13 | 23 |
| 17 | `support-0041` | support | standard | 1 | 0 | 0.9545 | 13 | 42 |
| 18 | `support-0032` | support | broad | 1 | 0 | 0.9333 | 13 | 35 |

未舍入数值和逐题元数据位于 `results/` 下的机器可读 JSON。

## 抽样

源集合包含 sales、marketing、operations、support、finance、HR 各 100 个 public task。按允许
服务数分为 focused（2–3）、standard（4）和 broad（5–8），每个“域 × 宽度”单元使用
`SHA-256(seed:task_id)` 字典序最小值选一题。规则与 seed 在读取成绩前冻结。排除 15 个使用
ChatGPT 业务 helper 的任务，以及 5 个此前已尝试的 task ID。

该 hash 只用于确定性抽样，不是证据 fingerprint。每个单元只有一题，不能估计域或宽度效应。

## 首轮基础设施失败与独立复验

`operations-0009` 首轮向固定 Trello add-label 路由传入 JSON 标量 body；上游把解析后的字符串
当 mapping 展开并抛出 `TypeError`，native host 因此未到终态。该问题属于适配器／上游兼容，
不是 Registry 或 Unix socket 故障。

加入精确 endpoint 转换和回归覆盖后，独立复验达到 `host_terminal` 与 strict 1.0，使用 11 次
模型调用和 26 次工具分派。复验中的模型自行发出合法对象参数，因此它验证了修复后的端到端路径，
但没有再次触发标量转换本身。

若以复验替代原失败项，只能得到工程覆盖视图：18/18 基础设施闭环、9/18 满分。它不是首轮
重评分，不进入主结果。

## 范围与来源

“真实 API”指真实外部模型 provider HTTPS 路线。Gmail、Salesforce、Trello 等业务服务均为
AutomationBench 本地模拟世界，不是生产账号。评分使用原始逐题断言检查冻结 world，没有增加
LLM judge。

前 8 题记录 RPNH `3492ba2`，后 10 题及复验记录 `1da3648`；两提交间执行核心 `cpn/` 完全
一致。AutomationBench 固定为 `4a8e1061254004d9dac807054eed33fad7d1ff14`。

本结果不是随机代表性样本，不是完整 public-600 运行，也不是 AutomationBench 官方成绩。
