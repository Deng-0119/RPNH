# 证据与保留规则

[English](EVIDENCE.md) | [设计](../DESIGN_ZH.md)

`tool_events.jsonl` 记录模型原始参数、准入分派和精确上游返回；`normalization_events.jsonl` 记录
兼容转换，但不替换原参数。RPNH Registry 投影记录 managed action 与真实模型调用次数；冻结的
final world 和上游 score 文件确定业务结果。

这些层回答不同问题。工具成功返回不自动证明模型已消费，终态文字报告也不证明业务 world 正确。
只有 host 和 world owner 均静止后，分数才有效。

attempt、中断、错误和评分修订都追加保留。修复复验不覆盖首轮。reprojection 与 rescoring 可以读取
冻结证据，但不得重放业务动作。

公开 example 只包含脱敏计划和汇总。原始 Registry 数据库、请求／响应 transcript、私有 execution
profile 和本地路径留在仓库外。仓库内汇总可复核报告算术，但不能完整重放每次 provider 交互。
