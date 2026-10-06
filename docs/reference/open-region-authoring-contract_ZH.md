---
name: rpnh-open-region-authoring-contract
description: "显式开放选择、强闭合证明与精确预期装配消费的复核草案。"
metadata:
  document-kind: authoring-contract
  audience: trusted-host-integrator
  language: zh-CN
  counterpart: open-region-authoring-contract.md
  revision: "2026-10-06.1"
  status: historical-draft-with-current-status
---

[English](open-region-authoring-contract.md) | [中文](open-region-authoring-contract_ZH.md)

# 开放区域作者合同

## 2026-10-06 状态说明

下文保留原静态草案及历史NOT_RUN矩阵，不是当前实现的等待状态。已集成的有界作者合同见
[open-region authoring](open-region-authoring_ZH.md)和[descendants](open-region-descendants_ZH.md)。
历史矩阵未逐行重跑，不将整张48行/185变体矩阵改写为PASS；当前有限集成接受和剩余边界见
[有限验证](../guides/release-validation_ZH.md)。下文“下一授权阶段/编码前”仅指原草案时点。


本文件冻结的是**供独立复核的 DRAFT 提案，不是已批准协议或实现**。以下新名称均为拟议合同。[验收矩阵](open-region-acceptance-matrix.json) 全部为 NOT_RUN。本次不新增 production schema、类、项目 import、测试、执行、采用或外部动作。

## 1. 已定方向与第一条真实路径

已选作者策略为显式 `lineage_mode="new_lineage"`，不得省略或默认。提取选择属于来源证明，不是完整父历史或 merge 祖先。独立持久化真正非终结的 O；另一个强命令依据调用者明确提供的上下文和完成规则，闭合 O 的精确选择。预期目标在实际 Assembly 证明匹配前始终待定。结构闭合不授予输入、lease、容量、效果许可、readiness 或运行采用资格。

最小真实路径：发布闭合 S，内容为 A→B→C，且 C terminal 已明确声明；只选择 B；发布并重新打开 O，保留 A→B ingress、B→C egress 及缺少完成规则的义务；调用者明确选择 C、其 terminal 和 ingress 策略；使用实际 `extract_module(S, ExtractPlan(kind="components", components=(B,C), boundary_policy="preserve_all_dependencies", output_name=...))` 重建 B+C；编译并发布强闭合 D；真实新 Assembly/v4 在其精确计划成员处发布并完整消费 D。重新打开后的 Registry 完整读取必须遍历这些实际资源。伪造 terminal、计数器、独立 helper 断言或未被消费的 adaptation 文档均不达标。

初始重建 adapter 为 `rpnh/collaboration/source_component_completion/v1`：从一个精确普通闭合 Module/v1 来源选择完整基本 `operation` components。选择与补充上下文的 component 声明、内部 links 按 canonical JSON 原样保留；只允许现有 extractor 的公共边界／名称投影。调用者明确给出并集，不由可达性自动选择 C。现有 extractor 保留的来源级 requirements／budgets 继续保留并核验。graph 内部、opaque author constraints、任意 PN/node 选择及更丰富的适配都返回具体能力缺口，不伪装成普通 component，也不成为永久产品排除项。

## 2. 权威、历史与引用

S、O、D 的逻辑身份互异。O 和 D 都有 `parent_revision_refs=[]`、`selected_change_refs=[]`；来源关系放在下述新 provenance 资源中。O 为 `definition_kind=open_region`；D 为 `closed_module`，且 `open_region_contract_ref=null`。不得在 D 描述符中隐藏未闭合义务。

O 可成为 Branch/v1 的 head。D 建立自己的 closed Branch，fork base 为 D，不声称其 upstream Branch 是 O。即使版本／head／stream 期望完全正确，O Branch→D 推进也必须失败：D 不是 O 的直接后继。既有 upstream fork 语义要求 head=fork base，不能表达提取。真正的闭合后继 E 可以推进 D Branch。Branch 发布验证作者 head 历史，不证明材料或目标资格。

全部引用沿用 source-qualified 精确版本／资源格式。首个 publisher／完整 reader 使用一个已绑定本地 source 与精确已登记 owner task 版本；其他 source/owner 下同形 ref 不是别名。在同一读取切面检查 source binding、bootstrap／producer 权威、精确 schema authority、canonical publication／producer relations 与 object-store bytes。名称、路径、Branch head 和 latest 都不能选取证明。

元素身份仍有谱系作用域。O、D 分配新 ID；跨谱系 origin 行保存精确 source revision+element ID。既有 `author_element_map/v1.copied_from` 保持仅指父修订，不用于 S/O 来源。E 保留同 kind 元素 ID；copy 使用新 ID。相同字节不产生祖先关系或成员事实。

## 3. 拟议精确记录族

每个记录具有规定的 `schema_version` 和全部列出的字段，拒绝未知字段；使用 canonical data-only JSON，需要可空处显式 null。作为集合的列表按 canonical identity 排序且唯一；声明数组保留来源有语义的顺序。“Pin” 精确为 `{resource_ref,schema,schema_authority_ref,payload_bytes,payload_sha256}`。实际 payload bytes 必须等于 canonical JSON，不能只比较解析后对象。受保护 schema 使用精确冻结 catalog；应用 schema 使用精确 schema resource；检查支持的 schema 内容及实际字节摘要。ref/pin 必须实际验证，不能因有 hash 就信任。

| 记录／`rpnh/collaboration/` 下的新 schema 后缀 | schema_version 之外的精确字段及解释 |
|---|---|
| 提取来源 `open_region_extraction_provenance/v1` | `lineage_mode`、`source_revision_ref`、`source_materials`（definition/element/boundary/HOST pins）、`selection`（`kind="component_element_ids"`、有序 `element_ids`）、`source_compile_signature`（canonical inventory 字节数/hash）、`projection_recipe="whole_operation_components/v1"`。选择 ID 必须在来源当前 map 中指向 components；完整消费来源材料。 |
| 开放定义 `open_region_definition/v1` | `provenance_ref`、`components`（精确选中声明）、`internal_links`（两端均选中的精确来源 links）、`source_public_boundaries`（仅保留的来源 entry/exit 行）、`completion=null`。跨界 links 放 inventory，不伪装为内部 links。本记录不是 ModuleDeclaration。 |
| 开放元素映射 `open_region_element_map/v1` | `provenance_ref`、`elements`，每行 `{element_id,kind,locator,source_revision_ref,source_element_id}`。精确覆盖选中 components/ports/operations 与内部 links；不造 Module/terminal 元素。O ID 在 O 谱系内均为新身份。 |
| 边界清单 `open_region_boundary_inventory/v1` | `provenance_ref`、`scanner="source_declaration_and_actual_fragments/v1"`、`source_compile_signature`、`coverage`、`items`。coverage 对 data/control/feedback/lease/pool/slot/reset/external_dependency/effect/completion 各有一行 `{kind,status,source_locations}`，status 为 complete 或 unsupported。每个 item 采用下述格式。“不存在”只能来自完整检查，不能来自缺证。 |
| 开放 HOST 需求 `open_region_host_requirements/v1` | `provenance_ref`、`source_host_requirements_ref`、`declaration_pins`、`scope="conservative_source_support"`。这是来源编译实际消费的精确声明，不代表 O 已编译或实际 handle 已存在。 |
| 开放合同 `open_region_contract/v1` | `provenance_ref`、`boundary_inventory_ref`、`unresolved_item_ids`、`required_choices`（精确条目及必需的 context/completion/ingress/adapter 选择）、`claim="open_only"`。所有未闭合与 unsupported 项保持可见。 |
| 预期成员意图 `prospective_member_intent/v1` | `kind="prospective"`、`source_id`、`owner_task_ref`、`assembly_protocol="collaboration_assembly_revision/v4"`、`resolver_contract="rpnh/collaboration/direct_adapted_member_resolver/v1"`、`assembly_command_id`、`parent_assembly_revision_ref`（精确 v4 父或 null）、`target_scope="direct_member"`、`member_id`、`ingress_expectations`、`completion_expectation="member_primary_terminal"`。每个 ingress expectation 指定 O inventory item，并显式选择 public_entry 或 assembly_connection；后者固定另一个 member ID/revision/exit element ID。无默认。这不是 Assembly ref 或已登记 membership。 |
| 边界适配 `open_region_boundary_adaptation/v1` | `open_revision_ref`、`provenance_ref`、`context`（同一 source_revision_ref 与额外 component element IDs）、`completion`（精确来源 primary terminal element ID、有序保留的 alternative terminal element IDs）、`extract_plan`（kind/components/boundary_policy/output_name）、`intent_ref`、`dispositions`、`origin_map_ref`、`result_materials`（Module/element/boundary/HOST pins）、`claim="exact_current_result"`、`deployment_intent="same_run_candidate"`。context 与 selection 不重叠，并集精确；不暗加 component 或丢弃保留 terminal。 |
| 跨谱系映射 `open_region_origin_map/v1` | `open_revision_ref`、`source_revision_ref`、`result_revision_ref`、`elements`，每行 `{result_element_id,result_locator,origin_kind,source_elements,open_element_ids,boundary_item_ids}`。kind 为 selected/context/boundary_projection/module_projection；每个 D 元素恰好覆盖一次。列表能表示真实多对一来源；首个 adapter 不做作者 component split/fusion。公共 entry/exit 和 terminal 都明确解释来源。 |

Inventory item 为 `{item_id,kind,source_locations,selected_element_ids,outside_element_ids,contract,extraction_disposition}`。location 为 `{revision_ref,material_role,json_pointer,element_id}`；仅有 compiled 证据时 element_id 显式 null。contract 为 `{facts,unknowns}`，fact 为 `{semantic_key,value,source_locations}`；semantic_key 仅限 schema/direction/cardinality/order/generation/outcome/completion/arbitration/resource/effect。保留真实声明值，未知 key 显式列出。item ID 为 `boundary:` 加 UUIDv5(namespace URL, canonical `{contract:"open_region_boundary_identity/v1",source_revision_ref,selection,kind,source_locations}`)；重复 ID/location 的冲突事实必须失败。extraction_disposition 恰为 internal/boundary/outer_owned/needs_adapter/unrepresentable。

适配 disposition 为 `{item_id,status,adapter,output_element_ids,evidence,remaining_requirements}`；status 为 direct/adapted/needs_adapter/incompatible；evidence 为 `{source_locations,output_locations,request_fields}`，分别是精确来源 location、结果 material-role/JSON-pointer 对、closure-request JSON pointers。只有直接保留的关系可将 adapter 置 null。首版支持：保留内部关系；selected→context link 转内部；经调用者明确授权把 ingress 投影为公共 entry；明确选择已声明 C primary/alternatives 作为 completion。每个条目恰有一次处置，并追加检查 context 新产生的跨界项。unknown coverage、缺完成、未解释基数、carrier 冲突、资源仲裁／效果责任／返回语义缺失都阻止 closed 发布。已知 unsupported 边界可留在 O。

remaining_requirements 可保留结构合同已完整的实时输入可用性及未来 handle/claim/adoption 需求；不可藏入结构仲裁或业务完成含义的缺失。无可用结构协议证明的 outer-owned lease 为 needs_adapter，不是“已闭合，仅待运行”。真实来源 lowering／编译必须与清单一致；只看命名 Module links 不够。

## 4. 完整命令、身份与发布

拟议命令 schema 为 `open_region_author_command/v1`、`open_region_closure_author_command/v1`。字段均恰为：`schema_version,algorithm,source_id,owner_task_ref,producer_principal_ref,command_id,request,schema_authorities,schema_authority_sha256,result_revision_ref,parent_revision_refs,selected_change_refs,prepared_materials`。

open request 固定精确来源／provenance selection 及必填 lineage_mode；closure request 固定精确 open_revision_ref、provenance ref、调用者 context/completion、完整 extract plan、全部显式 dispositions 与精确 prospective intent。prepared-material 集合精确为：open 的 provenance/definition/open element map/inventory/open HOST requirements/open contract；closure 的 intent/origin map/adaptation/Module/ordinary element map/ordinary boundary map/ordinary HOST requirements。每项为 `{role,schema,resource_ref,document,metadata,payload_bytes,payload_sha256}`。O 描述符链接对应六项；D 普通四项指向结果。强 closure command 直接固定每个输入和输出，包括 adaptation/origins。

新定义只用一个 dispatch marker：`open_region_author_command_v1` 或 `open_region_closure_author_command_v1`，值是 canonical source-qualified command resource ref。每项命令所属材料都有精确 marker；命令自身 descriptor 标识 schema，不递归读取自己。marker 发现保持窄边界，未知或双 `_author_command_` marker 拒绝。source/command/material 可以互引确定身份，但验证按有类型依赖路径，不把 command 的 prepared-material 回指当新 parent 递归。

拟议确定性身份：`key=protocol_prefix+canonical_text({command_id})`，两个独立 prefix 为 `collaboration-open-region:`、`collaboration-open-region-closure:`。resource/resource_version 使用现有 TypedId/UUIDv5 namespace-URL 规则，输入 canonical `{scope:key,task:owner_task_logical_id,source:source_id,kind}`。O、D 都是 root，不复用 S 逻辑身份。材料身份沿用 bootstrap 作用域 `_material_ref`，key 为 `key+":"+role`。身份不 hash 可变 payload；同命令异内容冲突。新元素 ID 为 `element:` 加 UUIDv5(namespace URL, canonical `{contract:"open_region_element_identity/v1",lineage:result_logical_id,kind,origin}`)；selected/context 的 origin 是精确来源元素 tuple，投影的 origin 是输出 locator 加有序 source IDs。完整 reader 重算。

写入前 prepare 检查；中断后允许保留不可变 command/material 前缀。最终修订发布前，在最终读取切面重新验证所有精确依赖和输出。不可变 revision commit、Branch 创建、Assembly commit 是不同结果。同命令同内容必须完整验证原结果、返回精确原 ref 且零写；同命令的 request/owner/producer/context/intent/schema authority/output 任一变化都冲突。最终 cut 被拒后的重试只能在原输入恢复可得时继续原前缀，不能覆盖或改义。writer epoch 与既有 canonical transaction/publication 检查仍必需。

## 5. 消费链与 Assembly/v4

1. Descriptor reader 只返回 NetRevision 结构。新 full open reader 在一个 snapshot 内检查 source、command、O projection、inventory、精确需求及开放义务；不得把 O 当 Module 编译。
2. 普通完整 closed 入口 `validate_closed_revision` 把 D 的精确新 marker 派给强 closure consumer；该 consumer 完整读取 O/S，验证调用者选择，用真实既有 extractor 重建，用精确 Registration/HOST 声明编译，再比较完整 command、结果 ref/bytes/maps/origins/dispositions。所有 closed 入口继续拒绝 O；O 是 provenance，不是闭合祖先。结果显式提供 `adaptation_claim=exact_current_result`、`target_status=prospective_unverified` 与 command/adaptation/intent refs。不得只靠 subclass 判断而在普通包装中丢失事实。
3. 新 opt-in Assembly/v4 使用新 descriptor/plan/command/lowering schemas 与精确 `direct_adapted_member_resolver/v1` recipe。首个实现支持直接普通 closed-v1（包括通过既有完整 reader 验证的 merge-v1）和直接 adapted_result_current。§6 ordinary-new-definition 转换及 E/copy/merge-v2 正向消费属于紧接的有限后续里程碑，不是首闭环的前置。graph／child Assembly 继续走已接受 v2/v3 合同；v4 不暗中扩展。v4 自身历史仅接受 v4。成员行增加显式 claim、精确 adaptation_ref/intent_ref 或 null、精确锁定材料 resolution。适配失败不得 fallback 成 ordinary。
4. Assembly producer／完整 reader 从自身实际 source binding、精确 owner、command、v4 recipe、精确 parent selector、member ID、D ref 与 proof refs 推导目标，逐项独立核对 intent。验证真实 ingress 暴露，或明确承诺的另一个 member+revision+exit connection；检查全部结果边界、根 completion、carrier 唯一性／fusion 及当前最终上下文 HOST lowering。intent 不要求已有 Assembly。提交前保持 prospective；成功 Assembly proof 返回精确匹配成员解析；不得原地把 intent 改成 membership 事实。
5. v4 重建整个 final Module 与 actual-fragment lowering/origin map，保留严格 generated 配对：**精确 ordinary `ValidatedClosedRevision` 类型**、确定性 generated command/ref、精确 owner/producer、generated parent 链、四项 material refs/documents 与真实 compilation。merge/adapted subclass 不能替代 generated。parent/member/generated 依赖共享 snapshot 与 active path；重复 sibling 不是 cycle。最终发布再次核验同一依赖。

v4 具体格式：descriptor 使用已有 v2/v3 字段集 `revision_ref,owner_task_ref,producer_principal_ref,command_id,parent_revision_ref,plan_ref,generated_revision_ref,compiled_inventory_ref,lowering_mapping_ref`，schema 改为 `registry_v1/collaboration_assembly_revision/v4`。Plan/v4 保留精确 v2 plan 字段与 material-lock/signature 字段；成员行改为 `{member_id,display_name,revision_ref,claim,adaptation_ref,intent_ref,resolution}`。首闭环 claim 仅为 plain_closed_v1/adapted_result_current；resolution 为 `{proof_kind,command_ref,definition_ref,element_mapping_ref,boundary_mapping_ref,host_requirements_ref,historical_origin_refs}`。ordinary legacy 的 command text 已在材料 marker 中，故此 proof_kind 的 command_ref 显式 null。适配行必有两个 proof refs，其他行两者均 null。connections/completion 保留精确 element IDs。Recipe 固定同版本历史、direct_members/v1 containment、exact_direct_member/v1 instance identity、stable member UUID scoping、显式 shared_exact、selected primary with alternatives、actual final-context fragments、仅移除 Assembly connections 的 carrier cut，以及 canonical JSON byte locks。完整 recipe 无未知 key 或可选 fallback。Command/v4 沿用 Assembly 完整 envelope 格式锁定 plan/generated materials，但使用新的 v4 marker/prefix 和同版本确定性 result/generated IDs。Lowering-map/v4 保留完整 actual-fragment origins，增加成员 claim、精确 proof refs 与已验证 target match。它不是新可变运行拓扑。

拟议精确 recipe 为：

```json
{
  "contract": "rpnh/collaboration/direct_adapted_member_resolver/v1",
  "member_contracts": ["plain_closed_v1", "adapted_result_current"],
  "history": "same_version_logical_history/v1",
  "containment": "direct_members/v1",
  "instance_identity": "exact_direct_member/v1",
  "component_scoping": "stable_member_uuid_prefix/v1",
  "public_boundary_scoping": "stable_member_uuid_prefix/v1",
  "constraints": "rpnh/assembly_member_constraints/v3",
  "budget_policy": "shared_exact",
  "completion": "selected_primary_with_alternatives/v1",
  "lowering": "actual_final_context_fragments/v1",
  "carrier_cut": "remove_only_assembly_connections/v1",
  "source_origins": "rpnh/collaboration/direct_adapted_member_origins/v1",
  "material_lock": "canonical_json_sha256_bytes/v1",
  "proof_authority": "explicit_assembly_entry/v1"
}
```

**generated-v1 权威决定。** 保留已接受边界：Assembly 拥有组合和 member-origin proof；generated G 保有独立普通 closed-v1 合同。仅调用 `validate_closed_revision(G)` 只证明 G，既不声称也不验证 Assembly 的 adapted-member 匹配。需要 composition/adaptation proof 的消费者必须传入显式 Assembly/v4 ref、调用其 full entry。不得增加强制 G→Assembly 证明回链；这会引入无必要的循环，并暗中强化普通-v1 承诺。反之，把 G 当成 D 的原始来源／目标证明，或用它替代要求 Assembly proof 的入口，必须因类型化 claim 未被证明而失败。intent/claim 检查不是追随任意重写字节的数据防泄漏标签。

复用同一来源 O 面向 m1/m2，需要不同精确目标适配 D1/D2，即便 Module 字节相同。首个复用正例将 D1、D2 放入各自独立的有效 Assembly，分别选择自己的适配成员为根 completion。同一 Assembly 的根 completion 只能选一个成员，当前 member_primary_terminal expectation 不可能同时满足两个不同的适配成员；必须拒绝未匹配目标。单 Assembly 多 adapted 正例留待明确 alternative/contribution-completion 合同。这是语义能力缺口，不是任意全局成员数量上限；普通成员数量仍由真实组合规则决定。另一个 Assembly command 或 parent selector 是新目标；显式针对 O 用新 intent 重新闭合，产生独立 root D2。首版无 wildcard target 或可复用 retarget-attestation。嵌套适配需未来完整 occurrence-path 合同；当前 resolver 明确 unsupported，不是永久深度上限。

## 6. 普通 E、merge、copy 的显式主张

以下工程建议状态为 **DRAFT_REVIEW_REQUIRED / NEXT_EXTENSION**。E/copy/merge-v2 正向实现不构成首个 S→O→D→Assembly 闭环的前置。首片必须同时设置 legacy producer 与 legacy full-reader 的拒绝门：若 closed_author_command_v1 修订 E 的 parent 是新 proof-bearing D，或其完整祖先需要尚不支持的新 claim，即使 E 其余普通材料 canonical，也拒绝。ClosedModuleAuthor.publish 与 materials._validate_materials_at 都必须守住边界；只检查公开 publisher 会留下读侧降级。legacy Assembly/merge 也拒绝 D。不得把 E 静默返回／标记为不含 claim 的基础 ValidatedClosedRevision。验证这种 canonical old-marker 拒绝不需要实现整族或构造正向 derived/merge-v2 fixture。

后续显式转换既防止旧 current-result claim 成为无意编辑／合并权威，也避免历史成为永久死路。未来将 ordinary_new_definition 加入 Assembly member recipe 时须使用明确后续 resolver-contract 版本；首版 direct_adapted_member_resolver/v1 中没有该成员合同，不得静默扩大。

| 作者动作 | 拟议发布／完整读取规则 | 资格／claim |
|---|---|---|
| 按精确调用者输入重闭合 O | 新 closure command、新 root D2、新 target intent，不造 D parent | 仅 D2 的 exact-current proof；直到自己的目标消费前均 prospective |
| D 或 derived E 后的普通 E | 新 `open_region_derived_author_command/v1`，必填 authority_mode=ordinary_new_definition、operation=edit；一个真正精确闭合 parent；自己的完整 Module/maps/HOST 与历史 origin summary | E 完整闭合、可 branch；D 派生为 historical_only，E 当前 adaptation/target proof 为 none。legacy `ClosedModuleAuthor.publish` 必须拒绝这种新 proof-bearing parent，不静默转换 |
| D 谱系的 E1/E2 merge | 独立新 `plain_merge_analysis/v2`／`plain_merge_author_command/v2`，recipe=`closed_history_with_adaptation_origins/v1`；固定所有输入／祖先 proof kind、command、material refs、历史 summary；既有 author-atom resolution/reconstruction 加必填 ordinary_new_definition | 真正唯一闭合共同 base D、有序真实 parents E1/E2、全祖先验证及新身份规则。M 为普通当前作者权威，只有历史派生，绝非刷新 target proof。已接受 v1 merge 继续拒绝新 proof-bearing 输入 |
| D/E/M copy 成独立 root | 同一新 derived command，operation=copy_root，必填 ordinary-new-definition mode、精确 copy source/materials、空 history parents、新 logical/element IDs、全覆盖 copy-origin map | 只有历史 copy provenance；不制造与 D 的共同 merge base，不继承目标资格 |
| E/M/copy 的 ordinary Assembly 使用 | 后续显式版本的 resolver member 选择 ordinary_new_definition，固定 conversion/merge proof 与历史 summary，执行当前完整 closed checks | 不声称保留 source-bound closure/target；adapted-required 入口拒绝。适配失败不能 fallback |

Derived command 沿用 §4 完整命令字段、byte/schema/metadata 检查及 replay 规则，独立 prefix=`collaboration-open-region-derived:`、marker=`open_region_derived_author_command_v1`；request 为 `{operation,authority_mode,parent_revision_ref,copy_source_ref,module,element_ids,copy_sources}`。edit 要求非 null parent、null copy source；copy_root 相反。固定普通四项结果及全覆盖 `open_region_historical_origins/v1` 资源，包含精确 origin revision/command/adaptation refs 与 ancestor/copied_from 关系，`current_adaptation=null`。完整 consumer 从真实已验证历史／copy provenance 推导 summary，不信任调用者“historical”标签。summary 沿 E→E 和 merge 保留；删除 marker/summary 导致新命令验证失败，不靠扫描相同字节猜测来源。

Merge-v2 是已接受 v1 analysis/result 合同的显式新 schema/algorithm/command 身份版本，增加输入 claim/provenance pins 和 authority mode。保留 v1 所有 conflict、有序 parents、canonical identity、schema/HOST、精确重建及发布义务。新 result marker=`plain_merge_author_command_v2`，不接受任意可编译对象。pins 为 `{revision_ref,definition_ref,element_mapping_ref,boundary_mapping_ref,host_requirements_ref,proof_kind,command_ref,historical_origin_refs,current_adaptation_ref}`，null 显式；D 固定当前 adaptation，但 M 输出 current adaptation=null。D 与 S／独立 D2 为 unrelated_histories；common-base 搜索不遍历 extraction refs。以上是本提案复核范围，不代表 v8 已实现。

**未来业务语义边界 SD01，位于本片之外：** 某真实工作流是否允许用新普通定义替换原本必须满足的 source-bound contribution/completion 合同？本片不改变任何既有 workflow requirement；ordinary 转换只改变新作者结果自称的 proof 资格，不授予替换要求的权限。复核和实现这个独立 author/Assembly 合同无需新增用户业务问题或决定。未来若应用要求该贡献合同，E/M/copy 必须有适当 fresh adaptation，或另外得到明确授权的需求变更，才能满足它。若届时缺少选择，返回精确未解决需求；不得从编译、改名、重试、ordinary Assembly claim 或相同字节推断同意。最小 B+C 路径已明确给出 C completion 与 ingress 语义。

## 7. 失败处置与兼容边界

失败结果定位精确 ref/item/field 和阶段。所需类别：unsupported_contract（未知 recipe/version 或缺 adapter）、invalid_selection、unresolved_boundary（O 可保存，无 closed candidate）、incompatible_boundary、missing_exact_material、authority_mismatch、material_integrity_mismatch、command_conflict、stale_writer_or_branch、proof_cycle、target_mismatch、claim_not_proved。缺失／未知不得当作空、闭合、当前或授权。这些是拟议语义类别，不声称现有异常已经同名。

新 catalog 只在 schema/type 精确相等后组合既有合同；已接受 builder inventories、schema/recipe bytes、v1/v2/v3 承诺保持。legacy Assembly/merge eligibility gates 必须显式拒绝新 D/E/copy/merge-v2；仅 `isinstance(ValidatedClosedRevision)` 不够。共享 full reader 新增 marker dispatch 不扩展旧 resolver 合同。已接受 ordinary/graph/merge-v1/Assembly-v2/v3 输入及严格 generated pair 保持既有行为。普通 G 可继续按自己的 v1 合同使用，但不因此获得 composition proof。

unsupported graph/node adapter、无关谱系变换、跨 owner 协议、更丰富 feedback/lease/pool/slot/reset/effect 处置、可复用 target binding、更深 adapted occurrence 都保留为总方案真实能力缺口。不增加任意产品上限。未知业务 outcome、取消、持续交互不能变成成功完成。本片不涉及 runtime/provider/model/network/browser/worker execution/new run/effect、remote/PR/Actions/security 或 14C 工作。

## 8. 证据与复核门

本次以恢复后的 formal-v8 为准：998 文件；target manifest SHA-256=`3083985d45973ec9bec66e85bdef652bb0f8c7b69569114c2118a69d2e264d07`。恢复 receipts 明确仅 static recovery，新增 product imports/tests 均为零；恢复 70 个原始已索引 execution-evidence 文件，不含原始 raw fixtures。这些是历史证据，不是本草案新验收。已消失的早期 I01 目录和 note 不作为本次来源。

在精确恢复源码中重读的锚点：`authoring.py` 42–119（descriptor 分型）；`materials.py` 190–296、329–406（full dispatch/materials/ordinary publication）；`net_operations/definitions.py` 26–146（closed extractor）；`plain_merge.py` 43–146（ancestry/eligibility/pins）；`plain_merge_result.py` 98–202（strong command/read）；`branches.py` 172–190 与 `registry/_event_store/branch_publication.py` 189–239（fork/CAS/direct parent）；`assembly_v2.py` 1–5、223–236、343–378 与 `assembly_v3.py` 1–5、254–283、394–427（权威与配对）；`_assembly_lowering.py` 104 起（actual carriers）；`_assembly_v2_lowering.py` 13–113（constraints/completion）。collaboration 路径位于 `cpn/rpnh/collaboration/`，其他路径位于 `cpn/rpnh/`。

规范规划依据：权威统一方案 §§2.3、3.1–3.5、I01；实施映射 I01；Registry 设计 §§2.1、3、4.1、7、8.1–8.2；Registry I01/I02 和 V02/V02.a/V02.b。2026-10-05 只读 ADR 作为建议在此重新对照源码；§5 明确否决其强制 generated-source-link 建议，不依赖其已经消失的旧来源路径。

独立复核指出的 OR-26 completion 冲突与 legacy full-reader 降级入口已在本草案修正。首实现覆盖 S→O→D→真实 Assembly/v4、必需精确输入／重放／目标检查，以及 legacy/descendant 拒绝门。矩阵标记 first_real_loop_required、inherited_helper_contract、next_extension；继承的已审 helper 合同不等于新执行通过。复用既有 helper 并验证受影响边界，不等待全部 48 行／185 个 case-variant，也不先实现 §6 整族。E/copy/merge-v2 正例与更丰富边界按有限后续里程碑推进；未实现正例不得记为已交付。编码前由根／reviewer 确认这个收窄的首闭环处置及具体合同。冻结 hash 标识精确复核输入。SD01 是片外未来调用者／业务边界，不阻塞本片冻结或实现；工程复核不能静默代答。实现与矩阵运行均属于下一授权阶段。
