# 保留 pilot 的实验协议

[English](PROTOCOL.md) | [结果](../RESULTS_ZH.md)

本次保留结果使用 commit `4a8e1061254004d9dac807054eed33fad7d1ff14` 的 AutomationBench
public split、API toolset，并对每个选中任务执行一次新首轮。抽样在评分前冻结。

六个业务域与 focused（2–3 个服务）、standard（4 个）和 broad（5–8 个）三种集成宽度交叉。
每个单元选择 `SHA-256(seed:task_id)` 最小的合格任务；排除使用 ChatGPT 业务 helper 的任务和
此前已尝试的 ID。仓库内 plan 保存精确 seed、排除项、单元和选中 task ID。

可评分的普通任务失败不会停止 pilot；基础设施或配置失败会暂停后续分派，待兼容问题定位后恢复。
恢复保留冻结前缀，不重跑或覆盖既有 attempt。

每个分数都对冻结 final world 调用上游 `partial_credit` 和 `task_completed_correctly`。缺失或报错
分数保持 null。主汇总是严格首轮 cohort；修复复验单列。

适配器不增加 LLM judge，也不增加累计模型调用、工具调用、费用或整题时长上限。新运行仍须记录
所选 RPNH profile 的单次请求和 managed operation 限制。
