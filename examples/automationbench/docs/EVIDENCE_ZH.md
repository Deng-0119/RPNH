# 证据与保留规则

[English](EVIDENCE.md) | [设计](../DESIGN_ZH.md)

`tool_events.jsonl` 记录模型原始参数、准入分派和精确上游返回；`normalization_events.jsonl` 记录
兼容转换，但不替换原参数。RPNH Registry 投影记录 managed action 与真实模型调用次数；冻结的
final world 和上游 score 文件确定业务结果。

新 attempt 在上游 setup 完成后、host 启动和第一次工具分派之前，立即以独占创建方式保留
`world.initial.materialized.json`。该文件是实际 materialized world 的快照，包含默认值与实际
`meta.current_time`；后续 broker checkpoint 只替换 `world.latest.json`。
`initial_world_provenance.json` 将快照的精确 UTF-8 文件字节（含末尾换行）、SHA-256 与大小，
绑定到 attempt 记录、task-contract hashes、固定与实际观察到的上游 commit，以及 adapter
Python 源码摘要；同时记录初始 clock 与 service scope。即使 setup 失败，attempt 记录仍保留
源码和任务身份；后续准备或 host 步骤失败时，已写入的快照仍会保留。缺失快照绝不从 final world
反向推断。

这些是被动采集的私有证据：不设置或冻结上游 clock，不修改 world、prompt 或工具，不向 actor
暴露隐藏任务数据，也不替换原有构造前的 `scoring_input.initial_state`。scorer 与评分准入规则
保持不变。这无法恢复历史缺失的初始 world 或 clock，也不是合并或改写历史 cohort 的依据。

汇总行增加可选的 `initial_world` provenance 诊断，只含 hash、源码身份和初始时间戳，不含
world 内容、service 列表、prompt 或隐藏 rubric。captured 记录会与保留的快照、attempt 和
task-contract 文件核对；不一致单独报告，不改变分数。缺少新证据的旧 attempt 仍可读取和评分，
其 initial-world 状态为 `unknown`。

这些层回答不同问题。工具成功返回不自动证明模型已消费，终态文字报告也不证明业务 world 正确。
只有 host 和 world owner 均静止后，分数才有效。

attempt、中断、错误和评分修订都追加保留。score 只有同时满足以下条件才合格：task ID 与
task-contract digest 匹配冻结 plan，私有 task-contract 文件仍匹配 attempt 创建时固定的 digest，
scoring-input 与 final-world hash 仍一致，并且 lifecycle 证明 host/world owner 均已静止。评分修订
还须先用同一组输入核验上一版 score。修复复验不覆盖首轮。reprojection 与 rescoring 可以读取
冻结证据，但不得重放业务动作。

return ZIP 会在存在时包含 `normalization_events.jsonl`，并记录每份实际导出、脱敏后字节的
SHA-256 与大小。该清单验证实际返回副本，不是未脱敏私有原件的 hash。

白名单式私有 return ZIP 也会在存在时包含初始快照与 provenance，使用与其他私有证据相同的
已知凭据精确值脱敏。汇总／provenance hash 指向保留的私有源文件（汇总标记
`hash_scope: retained_private_evidence`）；若脱敏改变导出副本，应使用 `RETURN_MANIFEST.json`
核对返回副本的字节。return ZIP 含私有任务／world 数据，不是公开汇总。

历史公开结果只包含脱敏计划和汇总。新的扩展运行把 plan、绑定 condition 的七项 host
acceptance、attempt 证据与 export 保存在调用者 work 目录。原始 Registry 数据库、请求／响应
transcript、私有 execution profile 和本地路径留在仓库外。仓库内汇总可复核报告算术，但不能
完整重放每次 provider 交互。
