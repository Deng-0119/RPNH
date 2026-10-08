# H1 独立审阅摘要

结论：限定离线 owner-entry / Registry / PN 语义范围通过，无尚未解决的阻断问题。
本地原生 AF_UNIX gate 仍待补验。补丁可交接，不代表已经合并或完成原生发布验收。

## 重点结论

- 原 Orchestrator 只增加 stop 转发，不新增 owner、queue、runner、scheduler 或持久状态。
- AgentTask 两处 stop 改走公共方法，其 provider、workspace、recovery 和清理控制流未改写。
- tool_pipeline 正常运行使用同一 factory 与 Orchestrator.run；旧 make_harness 兼容入口已保留。
- 原 Registry/PN 仍决定准入与事实。关键未改文件的 SHA256 见 `BOUNDARY_PROVENANCE.json`。
- host 保持 loop、pool、port 等资源所有权。没有新增双重关闭路径或 RunOwner close API。
- 1/2 worker 等价性按资源值、因果角色、每 firing 事件顺序与 terminal outcome 比较，
  没有要求并发分支的全局交错顺序固定。
- 独立验证了 peer progress、精确 join/lineage、stop 后排空且无后继准入、提前 SIGINT、
  兼容 factory、只读重建与原 SIGINT handler 恢复。

## 数量与版本

34 项 focused 产品测试 + 5 项既有资源/中断回归 = 39 个不重复测试均通过。
独立复核其中 8 项，不增加 unique 数；所有成功窗口累计 50 次执行。
主批 collection 较早为 32 项，最终两项由后续批次覆盖；最终 focused 34 项无遗漏。
详见 `repo-relative-test-inventory.json` 与 `verified-test-summary.json`。

独立核验基线 948 个 runtime/test/example blob，零不匹配；独立 apply 后七文件与最终 manifest
逐字一致。实时主线 compare 仅发现 ec9077e 的五个 evidence 差异。精确 Git SHA、patch hash、
原始 JUnit hash 和证据来源都保留在相邻 JSON 中。

## 边界

云端 AF_UNIX 失败停在 socket 创建，原始 `native-attempt.log` 保留。pipe integration 不能证明
原生 socket/client、权限或连接生命周期。未运行真实模型、Actions、部署、业务 benchmark 或全仓。
最初系统 Python 缺 pytest、plugin/collection 路径错误及 unhashable fake-port fixture 均另存原始记录；
这些不是最终产品失败，也没有改写为通过。最终 fixture 修正后的相应用例已重跑通过。
