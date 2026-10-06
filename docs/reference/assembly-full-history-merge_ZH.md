---
name: rpnh-assembly-full-history-merge
description: "Describe the explicit bounded author contract and its evidence boundary."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: zh-CN
  counterpart: assembly-full-history-merge.md
  revision: "2026-10-06.1"
  status: bounded-source-contract
---

[English](assembly-full-history-merge.md) | [中文](assembly-full-history-merge_ZH.md)

# 扁平 Assembly 完整历史合并

`assembly_merge_schema_data()` 显式启用 Assembly v6。已有 v2/v3/v4
catalog 保持原契约；v5 属于另一个 resolver。此 API 只发布作者事实，不启动
run，也不授予采用权限。

`AssemblyMergeAnalyzer(gateway, registration, producer).analyze(...)` 接收
确切 `local_ref`、`incoming_ref` 和 `command_id`。输入必须是同一本地 source、
同一 owner 的真实扁平 Assembly v2 历史，或 v6 结果／后继。分析器重建双方完整
实际父历史；没有由调用者覆盖 base 的参数。必须存在唯一最近共同祖先。无共同
祖先或存在多个最近共同祖先时，保存不可变的 `unresolved_history` 分析，禁止发布结果。

稳定 member ID 区分实例，包括显示名称相同的实例。分析保存 base/left/right 的
确切成员版本和完整计划原子。竞争的版本升级或内部修订产生
`member_version_competition`。双方结构修改将保守地关联变动成员、连接、完成、
预算和部署；调用者必须审阅冲突，不会自动递归合并成员。

调用 `AssemblyMergeAuthor.publish` 时，传入保存的 `analysis_ref`、显式
`choices`、`generated_continuity="left"` 和新 `command_id`。每项冲突必须且只能
有一项选择，包含 `subject`、`choice` 和非空 `reason`。选择支持 `left`、`right`、
`base`，成员的 `delete`，以及带完整 `value` 的 `exact`。确切成员值必须保留
`member_id`，并给出 `display_name` 和真实闭合叶修订的确切 `revision_ref`。
删除或替换成员不会隐式修复连接或完成选择；必要时必须显式选择相应完整计划值。
额外、缺失或重复选择均被拒绝。

未修改／单边修改的成员仍被保留。成员和连接数组沿用规范且无语义的排序。
最终候选仍须通过真实完整装配、可信静态 lowering、消费者／基数和完成检查、
确切共享预算桶及绑定检查，以及完整成员／编译来源覆盖。
`shared_exact` 与 `same_run_candidate` 是必填显式值。不支持或不兼容的结果
不能到达最终发布。

合并 Assembly M 的实际有序父项为 `[L,R]`，即使调用者对全部冲突都保留本地值，
也不丢弃 R。其独立普通 closed-v1 G 具有 `[L.G]` 父项。这项显式普通连续性不
宣称 G 自身证明双方 Assembly 合并。`validate_assembly_revision` 在同一读取切面
重建 M 的完整历史、已保存分析、决定、确切成员材料、真实装配和严格普通 G 配对。
`read_assembly_revision` 只读取描述符。独立的 `validate_closed_revision` 仍可
读取 G，不要求 G 反向链接 Assembly。

`AssemblyAuthorV6.publish` 提供下一步作者编辑。传入真实 v6 `parent_ref=M`、
完整类型化计划（`AssemblyMemberV2`、`AssemblyConnection`、`AssemblyCompletion`）、
名称、预算／部署值和 command ID。E 的实际 Assembly 父项为 `[M]`，E.G 父项为
`[M.G]`；完整读取 E 会重建 M 及原始双方历史。

分析的首 command 和结果／编辑的首 plan 固定确切 schema authority 引用及规范
authority 字节摘要、所有预期输出 metadata、材料字节、选择／理由和两个输出描述符，
包括 G 的四份材料。中断后仅能使用相同 command 和完整原输入恢复；同 command
下改变 authority、metadata、理由或内容都会冲突。过期 owner writer 被拒绝。

首 command／plan 同时固定完整输入描述符、材料、HOST 和 schema authority 证明
闭包。即使独立旧 reader 接受等价 JSON 编码，本协议也要求描述符及来源材料的
实际字节为规范编码。发布 analysis resource 前会重新检查已固定的输入切面。

首个有限叶域为已接受的普通 closed-v1、plain merge-v1 和普通 graph-v2 证明族，
并递归检查叶历史。增加 catalog schema 不会自动扩大资格。嵌套、adapted、开放区域、
跨 owner/source、递归成员合并及自定义语义排序需要独立协议。带 Assembly 不透明
约束的生成 Module 不能冒充普通成员。
