---
name: rpnh-normal-child-root-contract
description: "在原 Workset Success 中显式闭合本地正常 execution child。"
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: zh-CN
  counterpart: normal-child-root-contract.md
  revision: "2026-10-06.1"
  status: accepted-finite-browser-blocked
---

[English](normal-child-root-contract.md) | [中文](normal-child-root-contract_ZH.md)

# 正常 execution child 与 Workset 根完成

这个可选本地适配器为 [Workset](worksets_ZH.md) 增加显式的
`execution-v1-normal-only` 闭合 profile，将已经登记的正常
`ExecutionRuntime` child 接入父 firing 原有的普通 Success，并生成可完整读取的
`collaboration_root_terminal/v2`。child definition、内容 schema、预期业务 slots、
output port、精确 Workset expectation、已登记 products、command 和预算均由 caller
选择。适配器不推断应用的完成政策，也不调度 child 的实际计算。

## 显式选择合同

使用 `normal_child_root_schema_data()` 取得旧 Workset inventory 以及新增的
`execution_child_seal/v1`、`collaboration_root_terminal/v2`，再用返回的 documents、
definitions 和 paths 显式构造 Registry catalog。仅 import 公开符号不会注册新类型。
`workset_schema_data()` 及此前 catalog 入口保持原 inventory；已有 transplant 与
Assembly 的显式选择入口也保持不变。

父 firing 具备真实已登记 products 且所有 child 已 settle 后，调用
`WorksetOwner.complete_normal_children(expected=..., outputs=..., output_port=..., command_id=...)`。
`expected` 是精确的 `WorksetExpectation`，`outputs` 是原父 products 返回的真实
`RegisteredOperationOutputsAuthority`。对应显式 typed action 是
`CompleteWorksetNormalChildren(expected, output_port)`。这个 facade 将该 action
交给原 `RunOwner.succeed`，并提供严格完成重放。两个入口都不接收 caller 自造 seal，
也不允许 caller 选择要闭合的 child 子集。

Workset/v1 wire 格式保持不变。其 `required_child_seal_ref` 创建时为 `None`，
每个后继都保持不变。新 seal 只由 RootTerminal/v2 引用；Workset 的通用
`terminal_ref` 指向该 root。原 `CompleteWorkset`/RootTerminal/v1 路径保留双拒绝：
root 的 child-seal 字段必须为 null，且父 firing 不能有 execution child。
业务 `expected_slots` 仍必须非空；无 child 路径不等于空业务集合。

## 原 Success 的同一事务与完整 child 集合

seal 是具有精确 VersionRef 的真实不可变对象，不能用 seal event ID 冒充对象引用。
原 Success 事务一并发布 child seal、terminal mappings、RootTerminal/v2、
Workset/v1 后继、operation result、firing completion 和后继业务 checkpoint。
父 products 与 child 状态此前已经在该父 firing 的 provisional publication 下提交，
由此次 Success 提升为 canonical。`RunOwner.terminal()` 仍是之后独立登记
run-terminal evidence 的事务；根完成不调用它，也不表示该证据已经存在。

required 集合是精确本地 source/task/run 及父 invocation/business firing 所属的
全部 execution instance。definition、attach 事实、admission checkpoint、完整
checkpoint 历史及最新 child checkpoint 必须一致。每个 child 必须正常
`map_ready`、没有 active firing、具有有效 terminal token，并提供非空且可读的
evidence。seal 绑定精确父 refs、child stream 与 pre-seal head、
child/evidence/mapping refs、result、后继 checkpoint、Success command 与事务。

提交 validator 在原 writer snapshot 内重新枚举全集，并以 CAS 核对 child-stream
head。缺项、重复、额外项或归属错误都会拒绝。新 attach 先提交会使旧闭合提案 stale；
父 firing 先关闭则拒绝之后归属于该 producer 的 child attach 或状态推进。
最终 Success batch 不能新带 execution instance、definition、checkpoint、token、
transition-firing 对象或 attach event。原有 lease、writer、adoption、product、
terminal、全部 contribution 实际消费及其它 active firing 检查继续有效。

## Normal-root token 分配与持久能力

新的显式 normal-root 完成在 settlement delta 上记录单一标量
`ordinary_token_ref_scheme: "normal_root_firing_scoped/v1"`。仅精确类型
`CompleteWorksetNormalChildren` action 选择该方案。已有 token ref 保持不变；
新 token 使用原 net、原 business firing 的完整精确 refs 与 token ordinal，
在分离且有版本的 logical/version UUID namespace 中生成身份。同一 firing 和
ordinal 的重复提案保持确定性；改变不可变材料仍由原 no-clobber 检查拒绝。

拒绝的 root 提案可能留下不可变 prewrite。新的 root token namespace 与之后普通
acceptance 或 contribution 在同一 ordinal 使用的 legacy namespace 分离；旧
prewrite 保留。这个有限修补不解决所有普通 legacy Success 的分配风险。

writer 在 token 发布之前，用同一数据库 snapshot 闭合 source、native genesis、
native run、bootstrap command 和初始 catalog。持久 catalog 必须显式支持该可选
标量及 settlement/单 firing 规则；真实 delta 用其中保存的 schema source 验证。
更新 HOST schema 不能向旧 catalog 授予新能力；本 profile 不包含 catalog 升级或
metadata 替换。

commit 在 ordinary/revision 分派之前，独立绑定同一 Success 中真实的 root/v2、
seal、completion、result、delta 和 checkpoint。未知、null、错置 selector，或
新 normal 写入缺少 selector，均拒绝。effect/revision outcome 不属于本 normal
分配 profile；普通 legacy Success 与空 output 保持原默认行为。

没有该标量的历史 normal root 只用原 legacy 公式；有标量的历史 root 只用新公式，
并按原 pre-Success cut 核 catalog，reader 不尝试两套公式。精确公开重放先读取原
闭合，再决定是否进入新写能力门，因此保留旧 root refs 与原 catalog authority。

## 有限 evidence codec 与历史 schema authority

新 profile 的 evidence codec 合同仅包含：

- 恰好五类 JSON descriptor：`execution_instance/v1`、
  `execution_net_definition/v1`、`execution_checkpoint/v1`、
  `execution_token/v1`、`execution_transition_firing/v1`；media type 必须为
  `application/json`，实际 bytes 必须等于已验证 metadata
- `resource_version/v1` 的 schema-backed JSON 或 UTF-8 `text/*` 内容，
  必须用其精确且受支持的 self-contained Draft7 schema authority 验证

校验覆盖真实 bytes、self identity、producer、task、round、net、size、media type
和精确 refs。schema authority 必须在 evidence 登记之前已经 canonical，包含它的
publication、事务及每个必需 promotion terminal。之后才成为 canonical 的 schema
不能追认更早 evidence 的 authority。evidence 本身也必须先于 child settlement 登记。

新 profile 拒绝无对应 validator 的 opaque evidence、未知 descriptor 类型，以及
缺少所需 codec/schema validator 的 resource。仅声称 JSON media type 不足以通过。
旧 `ExecutionRuntime.settle` 对同父 refs 的合同不变；settle 被接纳本身不证明
新 root-closure profile 能验证这些 evidence。

## 完整读取、严格重放与版本化视图

`read_root_terminal(read_only_core, exact_root_ref)` 要求只读 Registry Core 与
source-qualified RootTerminal/v2 ref，返回验证后的 root、Workset、seal、
后继 checkpoint ref 和 `verified_at_cut`。它在同一个本地数据库 snapshot 中重核
不可变 bytes、publication 与 promotion、Workset 历史、contribution 实际消费、
compiled Module terminal binding 和同 Success 闭合。child 历史按原 Success 的
cut 选择，并在当前 snapshot 核对关闭的父 firing 没有后续 child 状态。
通用 `read_record` 不等于这个完整 reader。

再次调用 `complete_normal_children` 时，仅在原 command、精确父 refs、Workset
expectation、profile、selected outcome、output port 和整个已登记 output bundle
一致时，才是只读重放。改变材料会拒绝；匹配重放返回原闭合的完整验证结果，不再次
Success、不重新做 I/O validation、workspace planning 或 edit advance。
默认 `RunOwner.succeed` 的重试行为保持不变。

仅含 v1 数据时，Workset 投影仍为 `rpnh/workset_view/v1`；包含受支持 root/v2
时显式返回 `rpnh/workset_view/v2`，独立的 `root_child_closure` 包含 profile、
root ref、seal ref 与已验证的本地 cut。旧 Workset child-seal 投影字段仍为 null。
server 与 JavaScript consumer 识别这两个 view 版本，拒绝不受支持或不一致的
closure 数据。本地证明不表示跨 source Registry 的全局 cut 或完整物理交付覆盖。

## 验证边界

freeze04已有独立有限接受：normal-root跨事务retained refs/bytes、独立T01 run terminal、
ordinary revision、旧catalog拒绝、twochild/精确replay/cold v2读取、closed-parent拒绝、
7种损坏副本只读拒绝，以及线程attach-wins与独立child-stream CAS。R02单独验证一个明确的
Success-wins次序。旧token分配修复“未运行”的状态已由这些限定窗口取代。

writer动态64 case与R02另1 case分开；独立集成共8窗口、31执行、30不同节点，不把旧源上的
执行重标为freeze04。HTTP/Node和mock DOM已通过，实际browser启动被root/sandbox阻断，
未获页面/真实DOM/截图接受。详见[有限验证](../guides/release-validation_ZH.md)。

不保证任意调度/child数量；不等于通用typed-call dispatch、递归child执行、TaskControl spawn、
remote effects、跨run容量或完整I02/HOST/advanced25接受。
