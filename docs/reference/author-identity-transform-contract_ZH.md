---
name: rpnh-author-identity-transform-contract
description: "Describe the explicit bounded author contract and its evidence boundary."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: zh-CN
  counterpart: author-identity-transform-contract.md
  revision: "2026-10-06.1"
  status: bounded-source-contract
---

[English](author-identity-transform-contract.md) | [中文](author-identity-transform-contract_ZH.md)

# 普通作者元素的显式拆分与融合

`PlainModuleTransformAuthor` 发布调用方完整指定的闭合 Module，并保存显式作者身份历史。通过 `plain_transform_schema_data()` 或 `plain_transform_assembly_schema_data()` 显式启用。现有目录组合器、schema、普通作者命令和 Assembly/v2 resolver recipe 保持不变。

`publish` 必须提供 `parent_ref`、完整 `module`、覆盖全部当前 locator 的 `element_ids`、非空且按 canonical JSON 排序的 `transform_groups`、`copy_sources`、排序的 `created_element_ids`、排序的 `removed_element_ids` 和 `command_id`。每组包含 `kind`（`split` 或 `fusion`）、精确 `source_revision_ref`、排序的 `source_element_ids` 和 `target_element_ids`。拆分为一对多，融合为多对一；同组元素 kind 相同。来源身份退出当前定义，目标身份必须全新，各组不能重叠。任何新拆分、融合、复制或创建的身份都不得复用已验证祖先历史中的 ID。

当前身份必须完整分区为保留、转换目标、复制、创建；父身份必须完整分区为保留、转换来源、移除。保留集合由父与当前 ID 交集确定，其余处置必须显式给出。保留 ID 表示身份连续，不表示内容未变。复制边固定精确父元素，但不消耗来源；来源可同时被保留、移除或转换。现有 v1 元素映射仍负责 locator、kind 和复制，独立转换映射负责完整多方历史。遗漏身份直接拒绝，不自动解释为创建或移除。

首个持久命令锁定完整请求、精确来源及历史材料/HOST/schema、所有输出身份、内容字节和元数据。强读取器在同一读截面重新验证历史、完整映射、实际编译和五份输出材料。中断前缀只能重放原命令，过期 writer 不能发布。旧普通作者和旧 merge 明确拒绝转换父版本或历史；祖先中隐藏的其他不支持证明族也会被拒绝。

首个有限范围仅支持本源、精确同 owner、无不透明约束的普通根/单父历史及本转换族后继。暂不支持 graph、open/adapted、merge/transplant、Assembly 生成定义或不透明约束历史。结果恰有调用方指定的一个父版本，保留谱系，无 selected changes 或当前 adaptation。这些调用方声明不证明业务或行为等价，不选择 donor 变更，也不授予执行权限。

Branch/v1 可用现有三项期望值推进到精确结果。显式启用转换目录后，未修改的 Assembly/v2 完整重建成员证明并固定四份当前材料。实际 lowering 来源定位当前作者 ID，其成员证明解释父历史。生成 G 仍为严格普通闭合证明并与精确 Assembly 配对。编译器一对多 lowering 与多对一 fusion 仍独立于作者拆分/融合历史。
