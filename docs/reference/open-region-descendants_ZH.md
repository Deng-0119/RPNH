---
name: rpnh-open-region-descendants
description: "显式普通作者后继、独立复制及强历史消费。"
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: zh-CN
  counterpart: open-region-descendants.md
  revision: "2026-10-05.1"
  status: implemented-bounded-author-contract
---

[English](open-region-descendants.md) | [中文](open-region-descendants_ZH.md)

# 开放区域适配结果的普通后继

已适配闭合 D 可以作为重新建立普通定义 E 的精确父版本。该操作使用显式新作者命令，
不会隐式沿用旧作者编辑，也不刷新 D 的预期目标证明。E 具有完整当前 Module、稳定元素、
边界及 HOST 材料，并实际闭合编译；其派生声明为 `historical_only`，
`current_adaptation` 为 null。

## 显式编辑与独立复制

使用 `open_region_derived_schema_data()` 或 `open_region_derived_assembly_schema_data()`
显式组合目录。以现有所有者 gateway、可信 Registration 及精确 producer ref 配置
`OpenRegionDerivedAuthor`。`publish(request=..., command_id=...)` 必须给出全部请求字段：

- `authority_mode`：必须为 `ordinary_new_definition`，不存在省略默认值
- `operation`：`edit` 或 `copy_root`
- `parent_revision_ref` 与 `copy_source_ref`：精确带来源引用或显式 null
- `module`：完整当前 Module 文档
- `element_ids`：完整定位符到稳定 ID 映射
- `copy_sources`：显式结果 ID 到来源 ID 映射；无复制时也必须给出空映射

编辑选择一个真实 D 或 E 父版本，复制来源为 null，保留父版本逻辑谱系。
同类保留 ID 和显式新父元素复制遵循普通元素映射规则。E 可以继续编辑为 E2；
每次完整读取都会重建全部历史证明。

独立复制选择一个真实 D 或 E 来源，父版本为 null。结果具有新逻辑谱系、空历史父列表
及全部全新元素 ID。首次复制的 Module 必须与精确来源相同，完整复制映射必须在每个
对应定位符选择同类来源元素。需要改变内容时，随后编辑复制结果。跨谱系复制来源保存在
新历史资源中，旧元素映射仅表示父关系的 `copied_from` 保持 null。
复制不建立共同合并祖先，也不继承目标资格。

## 强当前与历史权威

`validate_closed_revision` 返回独立类型 `ValidatedOpenDerivedRevision`。
其精确 `command_ref`、`historical_origins_ref` 指向不可变完整命令及重建摘要。
历史行固定真实 D/E 版本、命令、适配或历史摘要引用。历史父关系标记为 `ancestor`，
复制来源标记为 `copied_from`。复制继承历史时，将关系转换为复制来源，避免虚构祖先。

发布在提交版本前固定完整请求、精确来源证明及材料、全部当前材料字节、schema 权威
和历史摘要。完整消费者在同一读取快照内重建 D/O/S 与复制来源证明、当前编译、映射、
精确所有者/来源及活动依赖路径。缺失历史、祖先变化、材料或 schema 字节错误、命令变化
均失败。相同重放不写入；中断前缀只能继续原请求。Branch/v1 保留精确版本/头/流 CAS。

## 显式普通 Assembly 使用

`AssemblyAuthorV5` 使用独立版本协议
`rpnh/collaboration/direct_ordinary_derived_member_resolver/v1`。
`AssemblyMemberV5` 要求 claim 和显式 `derived_command_ref`、`historical_origins_ref`。
`ordinary_new_definition` 的两个引用必须精确指向所选 E/复制结果的证明；
`plain_closed_v1` 的两个引用必须均为 null。调用者仍须提供稳定成员 ID、连接、完成条件、
`shared_exact` 和 `same_run_candidate`。

完整 Assembly 入口重建实际组合、最终上下文载体、完整成员/片段来源及精确生成的
普通 v1 配对。单独生成结果 G 证明自身独立普通定义；组合和成员历史证明需要显式
Assembly 引用，不增加强制 G 到 Assembly 反向链接。V5 历史只接受精确 v5 父版本。

旧普通作者/读取器、merge-v1 和 Assembly v1/v2/v3 拒绝新证明族。冻结的适配 Assembly/v4
不接受新增普通 claim，也不为 E 复用 D 的 intent；其既有 schema 与 resolver recipe 不变。

## 边界

本有限生产者覆盖 D/E 后继及其复制。Merge-v2、其结果复制、graph-open 适配器和更丰富
边界适配仍为后续工作。普通作者结果不会替换应用所需的来源绑定贡献或完成契约，该要求
仍由调用者决定。任何作者发布均不授予采用、运行、输入可用性、资源租约、效果或远程权限。
