# 与 AutomationBench 原始公开成绩的比较

[English](COMPARISON.md) | [示例](README_ZH.md) | [本地结果](RESULTS_ZH.md)

这里只做描述性比较，不是模型或 harness 的同条件对照。

[固定上游 README](https://github.com/zapier/AutomationBench/blob/4a8e1061254004d9dac807054eed33fad7d1ff14/README.md)
报道了完整 600 个 public task 的 strict pass rate；六个业务域各 100 题：

| 模型 | Reasoning effort | 原始 public-600 通过率 |
|---|---|---:|
| Claude Opus 5 | max | 50.30% |
| Kimi K3 | max | 46.67% |
| Claude Fable 5 | max | 46.17% |
| GPT-5.6 Sol | max | 45.83% |
| Gemini 3.6 Flash | high | 45.00% |
| Claude Opus 4.8 | max | 41.00% |
| Gemini 3.5 Flash | high | 38.33% |
| GPT-5.6 Terra | max | 37.17% |
| Claude Sonnet 5 | max | 34.67% |
| GLM 5.2 | max | 26.17% |

本地严格首轮是 8/18，即 44.44%。它在数值上接近部分公开汇总行，但不能直接比较：

- 本地 executor 标签是 `deepseek-v4-pro`，固定公开表没有对应行；
- 本地是 18 个确定性、score-blind 分层样本，不是完整 600 题或随机代表样本；
- 一个计划内首轮因基础设施失败而没有分数；
- 模型 effort、runtime、prompt 映射和工具宿主条件不是 matched control；
- 官方 leaderboard 使用另一套更难且不公开的 private task。

机械地只计算 17 个已评分任务会得到 47.06%，但去掉基础设施失败槽位会改变分母。用复验替代首轮
会得到 50.00%，但混合了不同 attempt，明确不是严格结果。

## 原始报道没有逐题成绩表

AutomationBench 定义逐题 `partial_credit` 和 `task_completed_correctly`，runner 也能导出逐题记录；
但是固定源码树没有提交已报道模型的 result export，`visualizer/runs/` 只有 `.gitkeep`。公开文件
包含 task rubric，不包含模型实际取得的分数；private leaderboard 题目也未公开。

因此不存在可按相同 18 个 task ID 连接的原始逐题成绩。本 example 公开自己的逐题行，但不会
虚构原始模型 baseline。
