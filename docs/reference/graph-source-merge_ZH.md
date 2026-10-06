---
name: rpnh-graph-source-merge
description: "Describe the explicit bounded author contract and its evidence boundary."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: zh-CN
  counterpart: graph-source-merge.md
  revision: "2026-10-06.1"
  status: bounded-source-contract
---

[English](graph-source-merge.md) | [中文](graph-source-merge_ZH.md)

# 普通 graph source 与 recipe 三方合并

[English](graph-source-merge.md)

`GraphMergeAnalyzer` 和 `GraphMergeAuthor` 是显式 opt-in 的 I01 作者能力切片。
合并对象是完整普通 graph-v3 source 与冻结的 builder recipe；全部派生 operation
仍由原 builder 重建。结果使用新的 `collaboration_net_revision/v3`：M 有两个
有序精确父修订，后继编辑 E 只有一个精确 v3 父修订。graph-v3 语言和旧 graph-v2
根／单父协议的含义保持不变。

调用方必须已有受信本地 Registry owner、精确 producer principal 和明确受信的
离线 `Registration`。发布不会采用或执行网。此切片不接纳 native/managed plugin、
外部 schema 解析或跨 source 作者历史。

## 真实历史与显式选择

创建 Registry 前组合 `graph_merge_schema_data()`；需要 Assembly/v8 时组合
`graph_merge_assembly_schema_data()`。它们增加新合同，旧 inventory 不变。

```python
analyzer = GraphMergeAnalyzer(gateway, registration, exact_producer)
analysis = analyzer.analyze(
    local_ref=left_exact_revision,
    incoming_ref=right_exact_revision,
    base_ref=optional_asserted_base,
    command_id="graph:analysis:1",
)
```

分析器读取完整本地祖先链，逐一独立验证每个参与 graph 的材料，再计算最近共同祖先。
caller 声明的 B 必须等于唯一最近共同祖先。无共同祖先和多个最近 base 保存为未解决
分析，不代选赢家；缺失、损坏、循环或不支持的历史不能形成成功分析。

saved analysis 包含精确 source/recipe/material pins、完整祖先、稳定身份正规化模型、
B/L/R 差异和精确 conflict ID。graph、node、port、arc、boundary 的身份不等于名字。
引入修订及原 copy 证据区分连续保留与删除后复用同一 ID。完整 source 字段和 wire 数组
次序都会保留；完整 recipe（包括精确 HOST selections）是不可分的选择值。
bool、integer、null、缺失和数组次序不会混同。

相同状态和严格单侧变化按精确三方规则合并。双侧分歧、起源碰撞、delete/modify、
delete/dependency、同名冲突，以及相互影响的 graph/recipe 合同，需要 caller 选择。
每个 conflict 可包含多个 subject，必须逐项显式选择并给出理由：

```python
choices = [{
    "conflict_id": exact_saved_conflict_id,
    "reason": "保留本地重命名，以及为此 graph 明确选择的 incoming recipe。",
    "selections": [
        {"subject": exact_name_subject, "side": "local"},
        {"subject": "recipe/complete", "side": "incoming"},
        # 必须覆盖此 conflict 列出的全部其他精确 subject。
    ],
}]
author = GraphMergeAuthor(gateway, registration, exact_producer)
merged = author.publish(
    analysis_ref=analysis.analysis_ref, choices=choices, command_id="graph:merge:1",
)
```

示例只说明选择格式，实际 subject 必须来自 saved analysis。每个 conflict 和 subject
恰好覆盖一次；重叠 subject 的选择必须一致。选取 B/L/R 中确实缺失的状态表示明确删除；
结果仍须完整满足归属、次序和 endpoint 引用。系统不补次序、业务引用、默认值或 HOST
选择；无法组成合法 graph 的组合保持未解决。

最终 source 和 recipe 必须无损 round-trip 全部所选 atoms。原 `rebuild_graph_module`
重建整个 Module，包括 budget、request-port、feedback 和 terminal。兼容默认的显式
cap 12、显式 null cap 含义不变。source/Module 身份、boundary 与实际编译消费的精确
HOST declarations 全部重算。持久化派生 operations 不构成第二份可编辑真源。

## 持久结果与后继编辑

完整 immutable command 先于八份材料：resolution、source、recipe、source map、
Module definition、Module element map、boundary map、HOST。它冻结请求选择与理由、
owner/producer、两个父 refs、全部后续文档、精确资源 refs、schema authorities、bytes、
SHA256 与 metadata。最终 v3 descriptor 提交才表示成功。

每个 durable cut 后都可 reopen 并按同一 command 精确重放。从首 command 起，改变
source、recipe、parents、理由、精确 HOST 或任一后续材料都会冲突。共享 schema/HOST
注册是 setup，与该 command lock 分开。

```python
edited = author.publish_edit(
    parent_ref=merged.revision.revision_ref,
    source=complete_edited_source, recipe=complete_requested_recipe,
    source_ids=stable_source_ids, copy_sources=explicit_new_to_parent_ids,
    command_id="graph:edit:1",
)
proof = validate_closed_revision(read_only_core, edited.revision.revision_ref, registration)
```

E 保留完整 merge 祖先和 origin 决策；新 copy 必须使用新 ID，并钉住父中同 kind 的精确
ID。不提供新 v3 root，也不隐式把 v2 升级成 v3；旧 `GraphModuleAuthor` 拒绝 v3 父。

公共 fullconsumer 按精确版本分派，在同一 local read cut 重读全部证据，独立重建
source、recipe、选择、身份、全部材料 bytes 及其 authority。最终成功发布前重新验证
完整闭包；实际 Registry 提交边界拒绝 stale owner。这不宣称跨 SQLite 与文件系统 payload
的新原子快照。依赖变化只会失败，不修改已冻结 command。

## Branch 与 Assembly 消费

`gateway.create_graph_merge_branch` 和 `advance_graph_merge_branch` 发布 Branch/v3，
复用原 Branch command domain 与精确 version/head/stream-sequence 三元 CAS。
expected head 须是下一修订的实际父：M 任一精确父，或 E 的唯一父。L/R 有序来源不会
新增 first-parent 业务限制。完整 predecessor 历史拒绝 ABA／重复 head。旧 Branch/v2
不迁移。Branch 仍只有 descriptor authority；选择 head 后须显式 fullconsumer 验证材料。

`AssemblyAuthorV8`、`AssemblyMemberV8` 在 flat closed Assembly 中消费精确 plain-v1、
普通 graph-v2 和完整 graph-v3 proof。使用原 typed connections/completion，以及显式
`shared_exact`／`same_run_candidate`。重复成员实例各有独立稳定 member ID。source/recipe
在每个实际 final context 重新构建及 lowering；map 钉住完整 graph origins、v3 command／
resolution refs 和每个 element 的引入证据。真实 Assembly A 与 generated G 强配对验证。

首份 v8 plan 冻结完整 member/HOST/schema 选择及后续材料签名。最终发布前重验完整依赖
闭包。`validate_assembly_revision` 独立检查 source、最终 context primitives、精确 A/G
配对及每份 durable 资源。单独读取 G 只具有原 closed-Module 合同，不证明 Assembly 闭包。

本片是 graph 作者合并，不是 Assembly 合并。递归 Assembly merge、其他 graph 语言版本、
开放 graph merge、自动编造次序和 runtime adoption 不在该有限切片内，仍是独立设计工作。
