# Bound-child lowering 状态

状态更新：2026-10-09 UTC。

**独立终审结论：ACCEPT_LIMITED_OFFLINE_CANDIDATE。仅接受机械 origin lowering 与可枚举声明冻结切片；不能据此宣布 H7 或两类生产执行完成。**

## 已通过有限独审的冻结身份

- 输入：H7 acceptance-history 的 994 文件，aggregate `a3af1d85ffbc8acac72e719fed316ed3c848229fce92026c62e571cb77a913a2`。
- 候选：999 文件，aggregate `7867bec81c0830f19cfd6f8a58b0d1598d5efc8220c69e6cfdd02604f98f1a0e`。
- 增量：`bound-child-declarations.patch`，SHA-256 `2bc8dd99115f2d139f6b17dd8cd1342087a02110233b8d43c5c37c5199a9cc98`，8 个 touched paths。
- 独审报告：[LOWERING_REVIEW.md](evidence/LOWERING_REVIEW.md)；精确测试集合：[LOWERING_TEST_COUNTS.json](evidence/LOWERING_TEST_COUNTS.json)；身份：[LOWERING_REVIEW_IDENTITY.json](evidence/LOWERING_REVIEW_IDENTITY.json)。独审已独立从精确输入重建patch，输出与999-file最终身份一致。

## 最终离线证据与交付状态

- 768 distinct PASS，0 FAIL、0 SKIP：46新独审 + 28作者新增测试复跑 + 516 compiler/ControlIR/serializer + 177 core/history/static + 1 ordinary-revision。作者与独审重复执行不相加，也不把先前139/298等不同阶段总数再加进768。
- 1项native subprocess明确deselected；额外6项net-operations因冻结输入缺 `examples/net_operations/live_agent_replacement.py` 而无法collect，NOT_RUN，不补文件凑通过。
- 本次是受sentinel约束的D0，零forbidden socket/URL/subprocess/PTY attempts。完整仓库、native D1、真实peer/receipt/reservation、恢复与H8未验。
- 自包含交付材料已冻结：`rpnh-bound-child-material-lowering.zip`，10,776,504 bytes、2,155 members；SHA-256 `4d2fc67ec4bad0c0b8c557be516bcfdbe4a0b89c58c8d1d348a5c172b3eb61e7`，根目录 `rpnh-bound-child-material-lowering/`。本计划只读复核了ZIP摘要、CRC、根级patch以及999-file源aggregate，全部吻合。
- 接包入口为根目录 `REPRODUCE.md`、`IMPLEMENTATION_SCOPE.md`、`SOURCE_IDENTITY.json` 和独审材料；包内精确994-file输入已含S1/core/history。只按最终patch接续一次，不从旧core/history重来。材料冻结和用户实际接收分别记录。

## 实现范围，不随审阅状态扩张

当前候选将 protected-origin 结构要求机械接入原 compiler/composition，冻结 Module 与受支持单阶段 AgentTask 的声明材料，返回 `FrozenBoundChildDeclarations`。此对象不等于完整 `PreparedChildMaterials`，intent producer 拒绝它；`require_execution_materials()` 保持 `ParentChildUnsupported`。

这不是完整 AgentTask/Module runtime inventory，不是 selected-installed HOST collector，不是两类实际 wrapper 接线。ControlIR proof-bearing Module、带 plugin/managed/workflow/额外 execution profiles 的 AgentTask 等边界仍按候选范围明确拒绝。普通业务 fanout/VariableResourceArc 不是新增 native child slot。

生产 native issuer、receipt、物理 target reservation、worker composition、child binding、parent terminal completion 仍 fail closed；D1、H7b、H8 均不由此完成。

## 接续规则

只在精确994-file acceptance-history输入之上应用本增量一次；输入已含S1/core/history，不重套早先patch。源码改变时必须同步重做涉及该候选的静态交集/身份核查，不能保留旧版本数字作为新版本证明。其他文档引用本页，不扩张其有限接受范围。
