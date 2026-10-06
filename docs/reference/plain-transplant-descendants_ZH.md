---
name: rpnh-plain-transplant-descendants
description: "保留完整选择性移植历史的显式普通作者定义。"
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: zh-CN
  counterpart: plain-transplant-descendants.md
  revision: "2026-10-05.1"
  status: bounded-author-contract
---

[English](plain-transplant-descendants.md) | [中文](plain-transplant-descendants_ZH.md)

# 选择性移植的普通后继

具有完整精确证明的[选择性移植 T](plain-transplant_ZH.md) 可以作为新普通定义 E 的父版本或独立复制来源。
显式组合 `plain_transplant_derived_schema_data()` 或
`plain_transplant_derived_assembly_schema_data()`，用既有 owner gateway、可信 Registration
和精确 producer ref 配置 `PlainTransplantDerivedAuthor`。

`publish(request=..., command_id=...)` 必须提供全部字段，不设省略默认值：

- `authority_mode`：必须为 `ordinary_new_definition`
- `operation`：`edit` 或 `copy_root`
- `parent_revision_ref` 与 `copy_source_ref`：精确带来源 ref 或 null
- `module`：完整的新当前 Module 文档
- `element_ids`：完整当前定位符到稳定 ID 映射
- `copy_sources`：显式结果 ID 到来源 ID 映射，无复制时也提供 `{}`

编辑仅选择一个真实 T 或 E 父版本，复制来源为 null，保留该逻辑谱系。
保留 ID 必须保持元素类别；显式新增元素复制遵循既有父元素规则。E 可继续编辑为 E2。
每次发布实际重建当前 Module、元素、边界、精确 HOST 要求和闭合编译。

独立复制选择真实 T 或 E 来源，父版本为 null，具有新逻辑谱系、`parents=[]`、全部全新元素 ID、
精确来源 Module，以及完整同定位符同类别复制映射。需要改变定义时，再显式编辑复制结果。
跨谱系来源单独保存；普通元素映射仅表示父关系的 `copied_from` 字段保持 null。

## 历史选择仍须完整证明

完整读取器返回独立 `ValidatedPlainTransplantDerivedRevision` 类型。
E 当前的 `selected_change_refs` 为空；历史摘要声明 `derivation=historical_only`、
`authority_mode=ordinary_new_definition`、`current_transplant=null`。
E 改变当前内容后，不能把 T 的已导入变化重新宣称为 E 的新导入。

历史行固定精确 T/E 版本和命令、T 的 analysis/resolution/全部 selected-change refs，或 E 的历史摘要 ref。
编辑增加 `ancestor` 关系；复制使用 `copied_from`，并转换继承来源的关系。
单独表格保存独立复制的精确元素映射。T 的 B/R 仍是供体和共同基线来源，不增加 E 或复制结果的历史父节点。

每次完整读取在同一读取事务内重建原始 T 全证明、全部供体/基线/本地输入、原始选择和处置、
有效导入变化及递归后继/复制历史，然后重建当前定义和编译。
检查真实规范材料字节、metadata、schema authority、精确来源/owner 和活动依赖路径。
历史缺失、选择变化、环、替换 marker 或权威变化均拒绝。

首个持久化完整 command 固定全部请求、来源证明、预备材料、身份、schema 和 metadata 选择。
中断前缀仅能继续同一命令；最终 revision commit 前再次检查完整依赖，然后才发布。
重放只读，绝不推进 Branch。既有 Branch 版本/head/stream 三重 CAS 保持；历史 Branch 版本
定位精确旧 head，调用者可显式完整读取。

## 显式 Assembly/v7 消费

`AssemblyAuthorV7` 使用 `rpnh/collaboration/direct_transplant_derived_member_resolver/v1`。
`AssemblyMemberV7` 要求显式 claim 和证明 refs。
`ordinary_new_definition` 必须选择本精确 E/复制证明族，并提供对应
`derived_command_ref`、`historical_origins_ref`。
`plain_closed_v1` 的两个证明 ref 必须为 null，且只接纳既有普通/merge 证明族。
成员 ID、连接、完成条件、`shared_exact` 与 `same_run_candidate` 仍由调用方提供。
V7 只允许 V7 父版本。

完整 Assembly 入口重建真实组合、最终上下文资源载体、全部成员/片段来源，以及精确生成的普通 v1 G
命令/材料/编译配对。G 必须是精确 `ValidatedClosedRevision` 类型。
G 的独立普通定义仍可读取；完整组合和成员历史证明要求显式 Assembly 入口，不增加强制反向链接。

既有普通作者、merge、P2 派生和旧 Assembly 合同保持不变；T 既有的直接 Assembly/v2 路径仍可使用。
本有限作者合同不授予运行 readiness、采用、执行、资源租约、外部效果或远程许可。
应用所要求的来源绑定贡献或完成条件仍是调用方的显式要求。
