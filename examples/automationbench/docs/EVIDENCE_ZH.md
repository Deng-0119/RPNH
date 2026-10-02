# 证据与保留规则

[English](EVIDENCE.md) | [设计](../DESIGN_ZH.md)

`tool_events.jsonl` 记录模型原始参数、准入分派和精确上游返回；`normalization_events.jsonl` 记录
兼容转换，但不替换原参数。RPNH Registry 投影记录 managed action 与真实模型调用次数；冻结的
final world 和上游 score 文件确定业务结果。

这些层回答不同问题。工具成功返回不自动证明模型已消费，终态文字报告也不证明业务 world 正确。
只有 host 和 world owner 均静止后，分数才有效。

attempt、中断、错误和评分修订都追加保留。score 只有同时满足以下条件才合格：task ID 与
task-contract digest 匹配冻结 plan，私有 task-contract 文件仍匹配 attempt 创建时固定的 digest，
scoring-input 与 final-world hash 仍一致，并且 lifecycle 证明 host/world owner 均已静止。评分修订
还须先用同一组输入核验上一版 score。修复复验不覆盖首轮。reprojection 与 rescoring 可以读取
冻结证据，但不得重放业务动作。

return ZIP 会在存在时包含 `normalization_events.jsonl`，并记录每份实际导出、脱敏后字节的
SHA-256 与大小。该清单验证实际返回副本，不是未脱敏私有原件的 hash。

历史公开结果只包含脱敏计划和汇总。新的扩展运行把 plan、绑定 condition 的七项 host
acceptance、attempt 证据与 export 保存在调用者 work 目录。原始 Registry 数据库、请求／响应
transcript、私有 execution profile 和本地路径留在仓库外。仓库内汇总可复核报告算术，但不能
完整重放每次 provider 交互。
