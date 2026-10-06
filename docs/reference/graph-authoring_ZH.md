---
name: rpnh-graph-authoring
description: "Describe the explicit bounded author contract and its evidence boundary."
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: zh-CN
  counterpart: graph-authoring.md
  revision: "2026-10-06.1"
  status: bounded-source-contract
---

[English](graph-authoring.md) | [中文](graph-authoring_ZH.md)

# 普通 graph 的持久单源作者修订

[English](graph-authoring.md)

显式双父普通 graph source merge、后继编辑、Branch/v3 和 Assembly/v8 消费见
[graph source merge](graph-source-merge_ZH.md)。该独立 opt-in 协议不放宽本文的 v2 历史合同。

## 支持合同

显式启用的 `GraphModuleAuthor` 通过既有 Registry owner、registration gateway、
私有资源发布和事务机制，发布独立、闭合的普通 v3 工作流 graph。
其 `collaboration_net_revision/v2` descriptor 与 legacy v1 修订明确分开。
这是 I01 的有界作者切片，不代表 graph 作者能力或 I00–I10 全部完成。
发布不会启动 run、采用网结构或分派任何 operation。

当前支持本地同源 root 和精确 v2 单父后继。dependency 和 bounded-feedback
直接复用未修改的 `build_agent_workflow_module`。原 request-port 选择、
与原默认行为兼容的显式上限 12、显式 null 不设累计调用上限均保持原义。
不从已存派生 operations 倒推作者算法。

最终成功修订之前，依次持久化七份不可变材料：

1. `graph_author_source/v1`：完整显式 v3 graph wire、component key 和
   config-schema 身份；在兼容 loader 补旧字段缺省前拒绝缺 execution、arc kind
   或 rework 字段的输入
2. `graph_build_recipe/v1`：builder 合同、executor/terminal keys、tools、
   required schemas、显式每节点预算、空 managed maps、null plugin catalog，
   以及所有选中 HOST 声明的 exact 资源引用
3. `graph_source_map/v1`：稳定来源元素身份与 exact copy provenance
4. 完整生成的 Module 声明
5. 派生 Module 元素身份图
6. 边界图
7. 实际 compile 消费的 HOST requirements 和 exact declaration resources

未知或不完整版本明确拒绝。新版本没有 plain-Module 分支。
native/managed plugin 仍不支持，包括把含 `native_plugin` 或 `managed_plugin`
合同的 HOST 声明换成普通外观别名。选中 schemas 必须自包含，`$ref` 只能使用
本地 fragment。调用方显式信任 HOST 实现；来源验证不等于证明任意 Python 行为。

## Owner API

调用方提供已授权的既有本地 owner gateway、可信 `Registration` 和 exact 本地主体。
创建 Registry 之前，将 `graph_author_schema_data()` 合入显式 catalog；若要使用
下文 v2 Assembly API，则使用 `graph_assembly_schema_data()`。
作者初始化可以预先登记共享 schema/HOST；这些准备事实不代表作者 command 已冻结。
用 `graph_source_elements(source)` 取得当前 locator 集，为每个来源元素分配不同的
规范 `element:` UUID。

```python
from cpn.rpnh.collaboration import (
    GraphModuleAuthor, make_graph_source, make_graph_recipe,
    graph_source_elements, validate_closed_revision,
)

source = make_graph_source(explicit_v3_graph_document)
recipe = make_graph_recipe(
    executor_key=selected_executor_key,
    terminal_key=selected_terminal_key,
    tools=selected_tools,
    required_schemas=selected_schema_keys,
    max_attempts_per_node=12,  # None 是显式不设累计调用上限的选择。
)
author = GraphModuleAuthor(gateway, trusted_registration, exact_principal_ref)
result = author.publish(
    source=source, recipe=recipe, source_ids=stable_source_ids,
    command_id="author:graph:r0",
)
validated = validate_closed_revision(
    read_only_core, result.revision.revision_ref, independently_selected_registration,
)
```

recipe 的空 declaration selection 表示由发布步骤在第一份作者材料前固定实际
compile 消费的 exact resources；非空 selection 必须完全一致。持久 recipe
始终完整包含这些 refs。重试可使用原请求或持久 recipe，不得同名换用另一声明版本。

source map 覆盖 graph、nodes、每节点输入/输出 ports、arcs、ingress 和 egress。
rename 可以改变 locator 名称并保留 ID。Module ID 从来源身份加语义角色派生，
不使用显示名、数组位置或 revision hash。copy 使用新 ID，并通过 `copy_sources`
把每个被复制的新 ID 指向 exact 同类型 parent ID；重开时独立核对 parent 和
source/derived copy provenance。多个输入 port 的改名可能改变原 builder 的
首输入 request 选择；稳定身份不等于执行语义一定不变。

## 显式启用 graph-v2 Branch 发布

`graph_branch_schema_data()` 在 graph 作者清单上显式增加
`collaboration_branch/v2`。旧 `branch_schema_data()` 和仅 graph 作者的
`graph_author_schema_data()` 不自动加入新 Branch writer 协议。
`GraphBranchVersion` 只接受同源 graph-v2 revision refs，以及 v2 predecessor／
upstream Branch refs。旧 `BranchVersion.from_dict` 和 producer 入口保持严格 v1，
包括 legacy open／多父 revision 的 descriptor 级支持。不迁移已有 Branch。

继续使用原可信 owner gateway，不传 Registration 或 runtime 参数：

```python
from cpn.rpnh.collaboration import current_branch, read_branch_version

first = gateway.create_graph_author_branch(
    head_revision_ref=published_root.revision.revision_ref,
    command_id="branch:graph:create",
)
second = gateway.advance_graph_author_branch(
    expected_branch_version_ref=first.branch_ref,
    expected_head_revision_ref=first.head_revision_ref,
    expected_stream_head=first.sequence,
    next_revision_ref=published_successor.revision.revision_ref,
    command_id="branch:graph:advance",
)
observed = current_branch(read_only_core, first.branch_ref.ref.entity_id)
proof = validate_closed_revision(
    read_only_core, observed.head_revision_ref, independently_selected_registration,
)
historical = read_branch_version(read_only_core, first.branch_ref)
```

可以在已经发布的 graph-v2 descendant 上新建 Branch，并固定该 exact fork base。
可选 `upstream_branch_ref` 固定 head 等于 fork base 的同源 exact v2 Branch；
upstream 后续推进不会改变此 pin。不默认查询 latest，不继承 upstream 全部历史，
也不冒充 merge 或 v1 转换。后续推进必须以当前 head 为新 revision 的唯一 parent。
完整本 Branch predecessor 链拒绝重用旧 head，阻断 A → B → A 以及更长 descriptor 环。

既有 writer transaction 同时比较调用者的 exact Branch version、head、stream
sequence。gateway、Core staged transaction 和 direct EventStore batch 都执行
namespace 和 canonical descriptor 检查。v1／v2 共享 Branch family command key
与确定性 ID 域；同命令改用另一个协议必须冲突。原命令在后续推进、writer 重开或
丢失返回后仍取回原结果。

此边界仍为 **descriptor 权威**。Branch 发布不 compile、不调用 HOST、不证明材料有效，
不启动 run 或采用网结构。canonical descriptor 即使缺材料或 source／recipe／派生
材料被伪改，仍可能成为 Branch head；显式调用的既有 `validate_closed_revision`
必须拒绝无效材料。公开 current／history reader 仅按已知 exact Branch 版本分派，
返回对应 record 类型。未知版本、未配置 catalog、错误 source／envelope 或 current
stream 错路由均 fail closed，不能回退返回旧 head。

选择 Branch 和验证其 immutable exact head 使用不同读事务；证明的是选定 head 及
材料，不是 Branch／材料联合 cut，也不保证验证结束时仍为 latest。full validation
仍需要显式可信 offline HOST lowering，与只检查 descriptor 的 Branch 提交门分开。

## 消费、恢复与兼容

既有 `validate_closed_revision` 按 exact revision type 分派。
v2 在同一读事务中重读 source、recipe、source map 和所有材料 authority，
调用原 builder 重建整个 Module，在 compile 前按 canonical JSON bytes 核对，
随后检查全部派生身份、边界、exact HOST selection、payload bytes、schema authority、
producer relation、commit evidence 和完整 command。布尔/数字、缺失/null、
不透明数组顺序不会混同。仅编译成功不构成来源证明。

第一份 source 材料即封存完整作者 command，包括 owner、producer、command ID、
parent 及全部七份材料内容。输入数组重排即使生成同一个 Module，也改变该完整命令。
每个后续 durable material cut 均可重开并复用原 exact resources 重试；首 source
持久化后，异内容同 command 必须冲突。只有最终 v2 revision commit 才代表成功。
source 之前的共享 registration 准备是独立事实。

legacy v1 plain 和 graph 保留原材料合同与 bytes，不被返回成
`ValidatedGraphRevision`，也不新增来源证明。不能将 v1 parent 悄然升级为本 v2
谱系，或将 v2 parent 悄然降级为旧作者发布。Branch v1、`AssemblyAuthor` 保持既有
v1 exact descriptor 边界，不通过 downcast 获得假成功；legacy Assembly 对 graph
v1–v4 的现有拒绝保留。graph-aware 组合使用下文独立 v2 API。

## 显式启用来源权威的 Assembly v2

创建 Registry 时使用显式 `graph_assembly_schema_data()` 清单。
`AssemblyAuthorV2` 接受 `AssemblyMemberV2` exact revisions：完整普通 v3 graph-v2
证明，或 plain closed-v1 证明，且必须同本地 source／owner。公开的
`AssemblyConnection` 和 `AssemblyCompletion` 选择稳定成员边界身份；每个 member
使用稳定的 `member:<32 lowercase hex>` ID，并显式选择 `shared_exact` 与
`same_run_candidate`。publish 参数名沿用 legacy Assembly，但 command／descriptor
版本独立。完整合同见[声明](declarations_ZH.md#来源权威的闭合-assemblyv2)。

最终组合从每个 graph 的 source／recipe 独立重建，按 member 投影 constraints，
在真实最终 context 编译。map 除 Module 声明外，还覆盖 source fields／roles 和实际
fragment primitives，不为内部 source port／arc 虚构 Module element。原内部 fusion、
final-context aliases、terminal 选择及保守 carrier 限制均保持显式。budget 身份只在
完全相等的合同下共用，rework permit 保持局部。

最终实际使用的每份 HOST resource 必须已登记；缺证据在 plan 发布前拒绝，不自动
修复。首 plan 锁完整请求、member／HOST selection、resolver，以及随后六份材料的
exact ref／size／SHA256；metadata 再锁完整 document envelope。七资源和 generated-v1、
Assembly-v2 两 descriptor 的每个成功 cut 都可恢复，最终 descriptor commit 才是成功点。

`validate_assembly_revision` 按 exact v2 分派，独立重建 source／composition，核对完整
材料／authority 及 exact generated-v1 pairing，返回 `ValidatedAssemblyRevisionV2`。
generated v1 仍可独立按 closed Module 消费；只读它不证明 Assembly source closure。
生成的组合 constraints 也阻止把它当作 recursive plain member。作者／验证不会分派
run、采用、provider、executor 或 tool；只执行显式可信的离线 lowerer。

后续仍需多版本 graph、open／任意深度 recursive Assembly、跨版本 author／Branch 转换、
merge／selected changes、open region、native/managed selection，以及独立授权的
runtime adoption／provider／effects 验证。这些能力未从总方案永久删除。

## 显式启用两层闭合 Assembly v3

使用 `nested_assembly_schema_data()` 创建 Registry，通过 `AssemblyAuthorV3`
发布 `AssemblyMemberV3`。成员允许 plain closed-v1、ordinary graph-v2、Assembly-v2
或 flat Assembly-v3。首片仅支持 root → child Assembly → leaf；不接受任意深度或
open Assembly。所有 exact member 必须同本地 source、同 exact owner。
旧 v2 catalog、recipe、command 身份及验证语义保持不变。

containment 使用 member-instance path；history 仍是同 v3 logical revision 历史。
同一 child exact revision 可出现多次。在一个 Registry snapshot 内，用同一 active
exact-reference path 检查 history 与 containment，合法 shared-child DAG 不算循环。
被选中的 child 必须 flat；它自身的历史独立验证。

消费者完整核对 child Assembly descriptor、immutable plan、所有材料及其 exact
generated-v1 pair。只传 child generated v1 不构成这份证明，会作为 opaque plain
member 拒绝。`shared_exact`、`same_run_candidate` 及 selected-primary completion
和其 alternatives 继续显式指定。

根网在最终 context 重新 lower。使用根的实际 fragments 重新检查 child connection
cut 和 exact contracted public exits。v3 map 保存完整 member/reference path、leaf
声明来源、所有孙层 graph source-role／primitive，以及 child 引入的 link、boundary
和 terminal 来源。compiled pointer 从根 inventory 重算，不给 child standalone
编译结果简单加前缀。

v3 plan 锁 resolver、exact selections 及随后材料的全部 signatures。发布仍按七资源、
两 descriptor 的 durable protocol 进行，并使用独立 v3 plan/map/command 身份。
只读 `validate_assembly_revision` 完整重建闭包后返回 `ValidatedAssemblyRevisionV3`。
作者操作不会采用或运行该网。

## 定点离线验证

- `tests/test_collaboration_graph_source.py`：原 builder 字节 oracle、显式 v3/
  recipe 版本、rename/copy 稳定身份和顺序语义
- `tests/test_collaboration_graph_materials.py`：真实 author-only Registry 发布、
  read-only 重开、source/derived 伪改、exact authority 故障、逐 durable cut、
  完整 command 冲突和并发
- `tests/test_collaboration_graph_branches.py`：真实 root/r1/r2 作者→Branch
  current／history→full consumer 链、descriptor／材料分层反例、三元 CAS、命令
  replay／故障恢复、完整 noreset 历史及 direct-batch authority
- Assembly-v2 publication、source/fragment、carrier、recovery、request-lock、
  concurrency 和 authority/pairing 测试覆盖显式组合 API
- 既有 closed-author 材料测试、指定 legacy Assembly graph 拒绝测试及普通 builder
  测试提供有界兼容回归

作者 fixture 的 executor、tool、terminal 回调一旦调用即抛错，只执行显式登记的可信离线 HOST
lowerer。修改 bytes/删除 SQL 证据的反例明确标为存储故障注入，
不用于伪造成功。这些测试不证明 runtime 执行或 provider readiness。
