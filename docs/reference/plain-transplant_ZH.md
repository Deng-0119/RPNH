---
name: rpnh-plain-transplant
description: "具有完整供体证明的显式普通作者选择性移植。"
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: zh-CN
  counterpart: plain-transplant.md
  revision: "2026-10-05.1"
  status: bounded-author-contract
---

[English](plain-transplant.md) | [中文](plain-transplant_ZH.md)

# 普通作者修订的选择性移植

显式组合 `plain_transplant_schema_data()`，或使用
`plain_transplant_assembly_schema_data()` 接入既有完整 Assembly/v2 消费者。
这些可信作者接口不启动、执行或采用运行。旧 schema 清单和 Assembly recipe 保持不变。

调用 `PlainModuleTransplantAnalyzer.analyze` 时提供确切 `local_ref`、
`incoming_ref`、非空且排序去重的 `selected_subjects` 和 `command_id`。
每个 subject 必须是真实 B→R 差异中的稳定元素原子，采用
[普通作者归一化](plain-merge-analysis_ZH.md)。不存在默认全选。
可选 `base_ref` 仅断言唯一最近共同祖先，不能替换谱系。
所有参与祖先必须是同 owner、同 source 的完整普通作者证明，包括已接受的 merge-v1。
graph、适配／开放、带不透明约束、Assembly 生成和 transplant 谱系不属于输入域。

保存的分析仅把所选供体状态投影到 B。依赖来自各原子对应的真实 B/R 来源；
保守资源载体证据来自这两个确实编译过的输入。投影本身不是已编译作者证明。
分析列出确切结构缺项、所选／未选供体变化之间的耦合，以及 B/L/投影 R 冲突。
不完整选择绝不隐式引入依赖变化。

## 显式结果与来源

调用 `PlainModuleTransplantAuthor.publish` 时提供保存的 `analysis_ref`、
显式 `choices` 和独立结果 `command_id`。复用既有冲突选择字段
`conflict_id`、`choice`、`reason`、`delete_element_ids`；每个确切冲突需要一个
调用方选择和非空理由。无冲突时仍显式传 `choices=[]`。
重叠选择必须一致，未解决依赖和无法表达的顺序仍报错。
可以拒绝耦合缺项涉及的导入；若仍要引入其所选事实，必须重新明确完整选择。

T 中每个未选原子必须与 L 完全一致，包括不存在状态、结构顺序和 HOST。
宽范围冲突选择、合成删除清理及顺序裁剪不能静默丢弃本地状态。
结果实际重建并完整编译，作者原子、元素 ID、边界和确切 HOST 声明必须往返一致。
可信 Registration 必须能够重建全部输入已有的确切 HOST 契约。

T 仅有一个 parent，即 L。供体 R 和共同 B 是来源证据，不增加谱系父节点。
`selected_change_refs` 仅包含有效导入事实：结果等于真实供体状态并且不同于 L。
明确选择的供体删除可以算导入；调用方合成删除单独记录处置。
already-local、kept-local、selected-base 和合成删除均与完整原始选择一起保存在
command/resolution 中，不能虚称已导入。

每个导入变化资源固定 B/L/R、analysis、result、确切 subject、B/R/result 状态
和实际结果稳定 ID 映射。首个持久化完整 command 固定全部未来资源、schema
authority、真实 authority 字节摘要、规范文档、metadata、结果 ref、parent、
选择和处置。最终 revision 提交才是发布。相同中断重试可完成原命令；
选择、理由、authority 或字节改变即冲突。重放 T 不推进 Branch。

## 完整读取、Branch 与 Assembly

`validate_closed_revision` 仅按显式 `plain_transplant_author_command_v1`
marker 分派，返回可区分的 `ValidatedPlainTransplantRevision`。
单个读取事务内重建全部真实输入证明、最近共同祖先、投影、选择、有效导入、
实际完整编译以及每个材料的规范字节和 metadata。
通过 schema 的陈述或保存摘要不构成独立证明。未知、双重或替换成旧协议的 marker 均拒绝。

使用既有 Branch gateway 时同时提供预期 Branch version、head revision 和
stream sequence。保存的 Branch version 定位历史 head；显式完整读取所选确切 head。
结果重放不会推进或回退 Branch。

在显式 transplant+Assembly 清单下，把 T 作为 `AssemblyMemberV2`，并提供
completion、成员身份、连接、`shared_exact` 预算策略和 `same_run_candidate`
意图。既有 `plain_closed_v1` 成员解析固定四份确切材料并执行完整移植证明。
实际 Assembly reader 重建完整成员来源、编译清单和配对生成修订。
生成 G 仍必须是确切普通 `ValidatedClosedRevision` 类型，且保持原 command、
材料与编译配对。这个 opt-in 不放宽检查。

旧 merge 和普通作者后继不把 T 自动纳入新的 parent 域。
本有限契约仅建立选择性作者发布和组合，不授予运行 readiness、部署、效果或
工作流要求替换权限。

若需显式重建普通后继或独立复制，并保留 T 的完整证明，请使用独立的
[移植后继合同](plain-transplant-descendants_ZH.md)。
