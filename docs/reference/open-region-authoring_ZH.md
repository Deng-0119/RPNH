---
name: rpnh-open-region-authoring
description: "显式非终结选择、来源组件闭合与直接 Assembly 目标证明。"
metadata:
  document-kind: reference
  audience: trusted-host-integrator
  language: zh-CN
  counterpart: open-region-authoring.md
  revision: "2026-10-09.2"
  status: implemented-bounded-author-contract
---

[English](open-region-authoring.md) | [中文](open-region-authoring_ZH.md)

# 开放区域创作

这些接口由可信 HOST 显式选择启用。保存开放选择使用
`open_region_schema_data()`；强闭合使用 `open_region_closure_schema_data()`；
直接 Assembly/v4 消费使用 `open_region_assembly_schema_data()`。
既有 catalog 函数与版本化 recipe 不变。本页说明已实现的结构接口及其限制。
[普通派生接口](open-region-descendants_ZH.md)另行说明显式 edit/copy 与 Assembly/v5。
历史验证范围见[有限验证](../guides/release-validation_ZH.md)；接口存在不等于运行时认证。

## 保存非终结选择

`OpenRegionAuthor.publish` 必须收到 `source_revision_ref`、排序且唯一的
`component_element_ids`、`lineage_mode="new_lineage"` 与 `command_id`。
首个适配器仅从一个精确的普通 closed Module/v1 来源选择完整基本 `operation`
组件。在 A→B→C 且 C 已声明为终结的例子中，只选择 B 会保存开放 O，同时保存
A→B 入边、B→C 出边及缺少 completion 的义务。`validate_open_revision`
完整重读来源、提取 provenance、材料及真实 lowering 证据，不把 O 编译为 Module。

清单覆盖类型化 data/control 边界、feedback、lease、pool、slot、reset、外部依赖、
effect 与 completion。判定不存在也必须有显式检查证据。未知实际 fragment、
尚未支持的 return/effect/resource 协议继续作为 O 的未解义务；编译成功不能
消除它们。覆盖范围是声明与真实类型化 fragments，不包括任意 executor 的业务行为。

## 显式补充上下文与目标意图

`OpenRegionClosureAuthor.prepare_request` 是只读预览。调用方需提供 O 精确的
`open_revision_ref`、`provenance_ref`、command ID，以及全部以下输入：

- `context`：精确来源 revision 与排序后的额外组件 ID
- `completion`：来源 primary terminal ID，以及按来源顺序保留的全部 alternative terminal ID
- `extract_plan`：`kind="components"`、选择与上下文组件名称的排序精确并集、
  `boundary_policy="preserve_all_dependencies"` 与输出名称
- `intent`：预期的精确 source/owner、Assembly/v4 command、parent 选择、member ID，
  以及显式 public-entry 或精确 Assembly-connection 入边期望

预览返回包含每项 disposition 与来源/结果证据的完整请求。
`publish(request=..., command_id=...)` 校验整个请求，在准备结果材料前首先固定请求。
显式选择 C 作为上下文、选择 C 的既有终态并声明入边处理，才会通过既有 extractor
与 compiler 重建 B+C。可达性不会替调用方选择 C、completion 或入边策略。

`validate_closed_revision` 按 D 的闭合 marker 进入完整证明消费。
返回的 `ValidatedAdaptedRevision` 明确携带
`adaptation_claim="exact_current_result"`、`target_status="prospective_unverified"`，
以及精确 command/adaptation/intent/origin refs。D 是独立闭合 lineage，历史父项为空。
应为 D 新建 Branch；把 O 的 Branch 推进为 D 会因不满足直接父项规则而失败。
中断后只可恢复原请求；更改 context、intent、schema authority 或结果都会冲突。

`assembly_connection` intent 固定未来 other-member revision 与 exit 选择。
这些选择是对未来 Assembly 的条件；D 不消费或证明该另一 member 的材料及已登记身份。
D 完整校验自己实际使用的 S/O/context 材料。只有实际 v4 消费者完整读取所选另一
member、校验其 exit 与最终上下文 carrier 后，连接声明才得到满足。

## 消费预期目标

`AssemblyMemberV4` 必须显式选择 `plain_closed_v1` 并令证明 refs 为 null，
或选择 `adapted_result_current` 并提供精确 adaptation 与 intent refs。
`AssemblyAuthorV4` 与 `validate_assembly_revision` 从实际 Assembly 推导目标，
核验 source、owner、command、parent、member、入边、根 completion、完整最终上下文
lowering/origins，以及严格配对的普通 generated revision。
`read_assembly_revision` 仅读取 descriptor。

当前 adapted member 必须以自己的 primary terminal 提供 Assembly 根 completion。
将 O 复用到另一目标必须使用新的显式 closure 与 intent。两个这样的 adaptation 可在
两个独立 Assembly 中分别消费；同一 Assembly 不能同时选择两个不同根 completion。
这不是全局 member 数量限制。

generated 普通 revision 保留自己的独立 closed-v1 权威。单独读取它只证明普通定义；
adaptation/composition 证明需要显式 Assembly/v4 ref。旧 Assembly/merge 消费入口及
D 的旧式普通后继均拒绝不能承载的新声明。显式普通 edit/copy 后继使用独立的
[派生创作接口](open-region-descendants_ZH.md)。Merge-v2、graph/node adapters、
嵌套 adapted occurrence 和更丰富边界/completion 协议不在本支持范围内。

以上均为结构性创作证据，不创建 run、runtime adoption、输入可用性、lease、capacity、
permission 或 provider execution。
