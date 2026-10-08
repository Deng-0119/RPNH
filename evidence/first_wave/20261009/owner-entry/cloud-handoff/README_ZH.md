# H1 既有 owner 执行入口收敛：本地补验包

状态：**READY_FOR_LOCAL_NATIVE_VALIDATION**。代码候选与独立审阅已冻结，未推送、未合并。

## 本次交付

复用已有 `cpn.orchestrator.runner.Orchestrator`，没有新增 runner、state 或 scheduler。
AgentTask 本来就用这一入口，本轮仅将两处 stop 改成公共转发；tool_pipeline 正常 run 改走
同一 Orchestrator。旧 `make_harness` 保持 Harness 返回形状，并通过同一 factory 取得 executor。

七个变更文件中，生产 Python 文件三个，测试文件两个，英中 example README 两个。
业务 PN、工具实现/ABI、Registry、Harness、OwnerEventLoop、provider/workspace/recovery 与
host 资源所有权保持原边界。此包不包含 H2a 或前端尚未合入的代码。

## 已核验与尚待补验

- 34 项 focused 产品测试和 5 项既有资源/中断回归均通过，共 39 个不重复 testcase。
- 独立复核重跑其中 8 项。六批累计 50 次执行，重复执行不另算新增测试。
- 覆盖同一 Module 的旧/新入口、1/2 worker、lineage/join、已准入 sibling 排空、host-owned close、
  提前 SIGINT/零 provider dispatch、兼容 factory 和只读结果重建。
- 云端原生 AF_UNIX 已实测，但在 socket 创建时遇到 `PermissionError: [Errno 1] Operation not permitted`。
  通过的 integration 明确使用既有 test-only pipe；生产没有 transport fallback。
- 原生 default focused、AgentTask 三项 classic 中断/恢复、native CLI 和资源关闭仍由本地补验闭合。
  不能把这里的离线通过改写为 native 或全仓通过。

基线是 `674252feb836f631c162979f177d1fe91f22559f`。已核主线后继
`ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4` 只修改五个 evidence 文件，产品代码身份不变。
补丁 SHA256：`71261d059d205b375334450aef281702a1c573562c6e905a08d411b907749a6c`。

## 从这里开始

1. 读 `LOCAL_VALIDATION_ZH.md`，先核当前 checkout、main/HEAD、dirty 状态和旧文件 hash。
2. 运行 `verify_patch.py`，它只在临时目录检验补丁，不修改目标 checkout。
3. 在授权 checkout 应用补丁，执行任务书里的原生补验。保留命令、退出码、原始日志、JUnit 与源码 hash。
4. 补验通过后把代码与对应真实证据一起提交供合并；不得只合并测试报告而漏掉七文件代码。
   本包没有自动 commit、push、merge 或 Actions 步骤，具体远端动作遵循既有授权。

`source/` 与 `baseline/` 仅是七个变更文件身份的最终/原始副本，不是完整仓库。
`file-manifest.json` 记录 old/new Git blob 与 SHA256；`SHA256SUMS` 覆盖整个交接内容。
`review/REVIEW_SUMMARY_ZH.md` 为用户向独立审阅摘要；`review/` 的 JSON、XML 与日志为可核对证据。
原生失败与最初装配/fixture 错误原样保留，不计作最终产品通过。
`COMMANDS.md` 与 `IMPLEMENTATION.md` 为原始执行/实现记录；其中冻结前的待审措辞不替代本包最新状态。

`plan/` 的 v2 持续优化计划是 2026-10-08 16:10 UTC 独立快照，其中 H1 的“最终测试/独审中”状态
早于本包冻结；H1 最新结果以本 README 和 `HANDOFF.json` 为准。计划不属于产品代码补丁。
本地交接不阻止 H2a、RSI 和其他无依赖工作继续推进；历史 A 的 PARTIAL_ENV 也不等于 H1 失败。

本包不调用模型、外部 provider、Docker、Actions 或业务 benchmark；不要求强制全仓测试。
