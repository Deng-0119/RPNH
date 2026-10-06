---
name: rpnh-declaration-reference
description: "Reference data-only declarations, trusted registration and compilation boundaries."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: zh-CN
  counterpart: declarations.md
  revision: "2026-10-05.1"
  status: source-reviewed-v0.1.0rc1
  basis: "core; adapter differences explicitly labelled"
---

[English](declarations.md) | [中文](declarations_ZH.md)

# 声明、注册与编译

## 职责与使用时机
此层在 run 存在前描述、验证可复用工作流。`ModuleDeclaration` 是数据；`Registration` 是可信宿主把符号 key 绑定到实现/schema 声明的机制。模型写出的 JSON 不授权导入任意实现。编译不采用图，也不执行供应商。

| 接口 | 参数与返回 | 异常和效果 |
|---|---|---|
| `ModuleDeclaration.from_dict(document)` | JSON 兼容 mapping → 已验证声明 | 非法数据/schema/port 抛 `DeclarationError`，不发布 Registry |
| `ModuleDeclaration.from_json(document)` | JSON 文本 → 同一验证边界 | JSON 解析失败也转为 `DeclarationError` |
| `module.to_dict()`、`module.to_json()` | 重新验证后的数据表示 | 不生成运行身份 |
| `lower_module(module, registration)` | 公共包装 → `SymbolicNet` | 错误 module 类型抛 `TypeError`；会调用可信 lowering |
| `compile_module(module, registration)` | 真正声明对象与 Registration → `CompiledPetriNet` | 类型错误为 `TypeError`，非法 lowering 为 `DeclarationError` |
| `symbolic.validate_products(operation, outcome, products, registration)` | 限定 operation/outcome 和 output-port 列表 | 验证候选产品，不发布 |

声明主要字段是 `name`、`components`、`links`、`entry`、`exit`、`terminal`、`required_schemas`、`budgets`；可选包括 `designer_constraints`、`analyzers`、`terminal_alternatives`、`budget_buckets`。schema_version 为 `rpnh/module_declaration/v1`。组件有 name/key/config_schema/config/ports 和可选 operation。`Endpoint(component, port)` 定位端点；terminal binding 包括 key/source/operation/outcome/config。

## 验证保证什么
Python 构造和 JSON 使用同一边界，包括有限 JSON 转换及声明位置的严格整数检查。必需 schema 与已注册配置契约必须匹配。lowering 观察组件实际返回的 `PNFragment`；compiler 记录声明/fragment inventory 后构建编译文档。用户提供的 lowering 仍是可信可执行宿主代码，必须只声明；命名为 lower 不会自动令任意回调纯化。

声明 operation 协议与编译 inventory 检查会区分 JSON 布尔与数字，包括 operation
和 effect 配置内部：`true` 不能替换 `1`，`false` 不能替换 `0`。`1` 与 `1.0`
等既有数值等价保持不变；schema 声明的整数字段仍严格检查。outcome／product／effect
集合和 tool inventory 延续既有次序规范化，operation 的 inputs／outputs 与 config
数组仍保持有序。
place fusion 对初始 payload 也使用相同的布尔／数字区分；initial-token 库存仍是
多重集合，payload 数组仍保持有序。

`SymbolicNet` 是限定符号名，`CompiledPetriNet` 是发布输入，二者都不是 Registry 执行权威。link 融合端点，不为每个消费者复制 token。产品、弧、预算决定行为，UI 画的拓扑不能替代声明。

## 示例：只读取给定声明，不启动 run
输入文件应为真实完整声明，例如[自定义指南](../guides/customization_ZH.md)中原生 `plugins build` 的输出。下面仅解析：

```python
from pathlib import Path
from cpn.rpnh import DeclarationError, ModuleDeclaration

def read_declaration(path: Path) -> ModuleDeclaration:
    try:
        return ModuleDeclaration.from_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, DeclarationError) as exc:
        raise ValueError("Declaration could not be loaded; no run was started") from exc
```

编译还需匹配的**可信宿主 registration**，不能猜一个空绑定集。插件 CLI 就同时构造 `plugin_registration(catalog)`。JSON 合法但缺注册 key，仍不能当可执行工作流。

## 可选的来源限定引用

`cpn.rpnh.collaboration` 提供需显式选择的高级内容合同，作为协作身份的首个边界。
`SourceQualifiedVersionRef(source_id, ref)` 包装现有的精确 `VersionRef`；
`SourceQualifiedResourceRef` 包装 `ResourceVersionRef`。两者不可变，比较时包含来源身份，
因此两个来源中的同形本地 ref 仍然不同。调用方提供稳定、非空的 source ID，并将其与
定位地址、显示别名、访问路径分开。这里不建立来源 Registry、解析器或访问授权。

`to_dict()` 返回独立的版本化文档，字段为 `schema_version`、`source_id`、`ref`。
对象引用保留既有 JSON 字段 `entity_type`、`logical_id`、`version_id`；资源引用保留
`resource_id`、`resource_version_id`。`from_dict(document, catalog=catalog)` 要求显式
配置的 `SchemaCatalog`，拒绝未知版本、缺字段和多余字段，不推测来源或降为旧 ref。
具体对象类型对应的 ID kind、实际存在性和权限仍由后续记录的生产／读取边界检查。

```python
from cpn.rpnh.collaboration import collaboration_schema_data
from cpn.rpnh.registry.schema_catalog import SchemaCatalog

schemas, types, paths = collaboration_schema_data()
catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
```

清单仅包含 `rpnh/collaboration/source_version_ref/v1` 和
`rpnh/collaboration/source_resource_ref/v1`。`types` 为空：它们是资源内容 schema，
不是新增的 Registry 对象／事件类型。可信 HOST 可通过现有 `Registration` 和
schema-resource 发布 gateway 组合这些文档。新的可选内容发布仍需其精确已登记
schema 资源；内容合同不会绕过这一权威。

| 读取方／输入组合 | 结果 |
|---|---|
| 既有本地 ref 和机械 v1 schema | 不变 |
| 新来源限定内容配默认 catalog | schema 不支持，明确拒绝 |
| 新来源限定内容配显式清单 | 严格 v1 类型读取 |
| 未知 v2 交给 v1 decoder | 拒绝，即使另外登记了 v2 |
| 未加载清单的旧 raw resource reader | 原始字节仍可读；不代表已支持其类型语义 |

本引用边界只实现身份。作者发布、Branch CAS 和 Assembly 使用下文独立启用的
合同；runtime binding／采用及 viewer 整合不属于本引用合同。仅导入这些合同不会修改运行默认
行为，也不会自动加入生产 HOST registration。

## 可选的不可变作者修订记录

`NetRevision` 增加显式选择的 `collaboration_net_revision/v1` 对象合同。
通过 `authoring_schema_data()` 和同一 `SchemaCatalog` 构造器，组合两个引用 schema
与该对象类型。仅引用的清单和默认 Registry 清单保持不变。新对象的精确本地 ID
复用 `resource`／`resource_version`，不增加全局身份服务或机械 ID kind。

描述记录固定自己的来源限定 revision ref、本地 owner task、声明的生产 principal
和 command ID。完整 parent revision refs、选中 change 的资源 refs，以及定义、
元素映射、边界映射、HOST 需求的精确资源分别保存。保留 parent 次序，不把选中
change 的来源改写为完整 merge ancestry。`closed_module` 不带开放区域合同；
`open_region` 必须给出未闭合边界／装配条件的精确合同引用。

`NetRevision.from_dict(document, catalog=catalog)` 检查版本化对象合同与类型不变量。
`read_net_revision(core, reference, local_source_id=...)` 是高级可信宿主的只读边界：
调用方提供已可信关联的 core 与来源身份。读取核对精确 canonical 对象、schema
支持、payload／metadata 一致、自引用、owner task，以及已登记的 owner／producer
身份。这些检查与 Branch descriptor 验证复用同一逻辑，且保持同一读取 snapshot：
对象／发布／唯一 commit 与 PUBLISHED promotion 证据必须 canonical，确切 JSON
媒体、locator、大小、字节必须一致。未显式登记该对象类型的默认 reader 明确报告不支持。该读取不解析远程来源、
登记 schema、修复记录或改变运行状态。

本片实现描述记录的类型与读取，不证明 producer 命令授权，也不校验所引用的映射和
HOST 需求、不编译定义、不发布 Branch head、不采用运行网。权威发布／CAS 和材料
消费者校验仍是独立实施边界。标为 `closed_module` 的记录本身不能证明目标
Registration 可编译它引用的定义。

未显式绑定本域来源时，事务可见性扫描保守地按本地引用收集嵌套 ID。因此外源限定
ID 若与本地另一 provisional firing 的对象碰撞，仍可能被拒绝。以下显式启用的
source-aware 边界消除这类歧义，同时保留本地 provisional guard。

## 显式本域来源绑定

`source_identity_schema_data()` 在作者／引用清单上增加可选的
`collaboration_source_binding/v1` 对象类型。持有现有
`RegistryRegistrationGateway` 的可信 HOST 可以明确调用
`bind_source_identity(source_id=..., command_id=...)`。仍由同一 Registry 当前 writer
及其精确已登记 task／bootstrap 权威控制；principal 字符串、传入作者记录或任务生成
的 JSON 不会授予这项能力。source ID 与 run 身份、名称、路径及访问授权分别保存。

不可变绑定对象与既有 `object_version_published/v1` 事实原子提交。固定的提交内
验证核对当前 writer fence、精确 native owner registration、唯一的本地绑定，以及
已存作者自身份的一致性。绑定不能重命名旧作者，也不能由新命令更换。同命令同内容
返回原绑定，异内容冲突。不增加新的机械 event type、全局 Registry、远程信任或权限设置。

引用扫描只采用当前事务前已经提交且不属 provisional 区的绑定事实。完整、已支持的
v1 来源限定引用若属于本域，继续接受原本地可见性检查；外源限定引用不因 ID 碰撞而
被解释为本地依赖。裸本地 ref 保持旧 guard；畸形、多余字段或未知版本 wrapper
不能取得例外。未绑定 Registry 保持保守行为。这一区分不解析、接纳或授权外源对象。

`get_local_source_identity(core)` 只读关联，不写入或取得新 writer。
`read_net_revision` 在接受调用方的本域关联前核对已存在的绑定。绑定证据缺失／损坏
或未支持可选 catalog 时明确拒绝。作者材料语义校验、获授权的 producer operation
与跨 Registry 交付仍是后续独立工作。以下说明窄范围的 Branch 发布边界。

## 同来源的作者 Branch 发布

`branch_schema_data()` 增加可选的 `collaboration_branch/v1` 对象合同。本域来源明确
绑定后，现有可信 registration gateway 提供关键字参数接口
`create_author_branch(head_revision_ref, command_id, upstream_branch_ref=None)` 和
`advance_author_branch(expected_branch_version_ref, expected_head_revision_ref,
expected_stream_head, next_revision_ref, command_id)`。目标为同一 owner／来源内可精确
读取的 `NetRevision` ref。fork 可固定本域上游 Branch 的某个确切版本与其 head 为基线。

每个不可变 `BranchVersion` 保留 owner task、publisher bootstrap、确切 head、
fork base／upstream、前驱、command ID、sequence，以及调用方的三项期望。对象与
既有发布事实在 Registry 原事务内提交。固定提交内验证同时比较真实 Branch 版本、
revision head 与 stream sequence，不能用新的读取替换调用方的旧期望。其它对象
类型或额外事实不能侵占已存在 Branch 的对象 stream。同一提交 snapshot 还校验
Branch 及其 revision／owner／producer 的确切不可变 descriptor 字节、媒体、locator、
大小和 canonical publication／commit 闭包。直接 Core 或 EventStore batch 调用
不能绕过这些检查。已 PUBLISHED 的 revision／producer firing 成员在具备 canonical promotion
证据时仍可使用。

本域 source binding 与 publisher bootstrap 仍是更严格的静态 owner 权威：
Branch 提交和 exact 读取在原同一 cut 内额外核对这两份不可变 descriptor payload。
早期 Branch 检查只核它们的登记 metadata／closure，没有证明这两份 payload 可读；
本次 v1／v2 共享检查修复该继承的存储损坏缺口。不把静态 owner 权威放宽成
PUBLISHED membership，也不改变全局 source identity 策略。

同命令同内容在后续推进或重开之后仍返回原结果，异内容冲突。多个命令从同一期望
元组并发推进时只有一个提交；SQL 发布失败不登记部分后继。`read_branch_version`
精确读取历史；`current_branch(core, branch_id)` 选择当前本域发布版本。

首个发布片只支持直接后继：新 revision 的 parents 必须明确含当前 head，且不能重用
本 Branch 谱系已经使用过的 exact revision head。历史检查阻断含环输入 descriptor
造成的 A → B → A 回退，但不声称已将全部作者祖先验证为 DAG。不开放
reset、新命令原 head 重发、跳过中间 parent、远端 Branch 解析、merge 算法或
运行采用。Branch 发布核对可读的类型化修订权威，不校验全部所引用作者材料，也不证明目标
可编译。Registry 运行 `branch_id`、adopted net、profile 和执行策略不变。
测试同时覆盖 ABA 形状的 snapshot 版本谓词，以及真实 gateway 的 A → B → A
回退尝试；后者被原子拒绝。

普通单源 graph 修订另由显式 `graph_branch_schema_data()` 启用
`GraphBranchVersion` 及 owner 的 `create_graph_author_branch`／
`advance_graph_author_branch` 入口。既有公开 current／history reader 按已知 exact
v1／v2 分派；v1 schema、record decoder 与 producer 保持严格。graph-v2 Branch
沿用 descriptor 权威、共享 command 域、三元 CAS 和完整 noreset 检查。
full consumer 接线及分开的 observation cuts 见[graph Branch 发布](graph-authoring_ZH.md#显式启用-graph-v2-branch-发布)。

## 闭合 Module 作者材料

`author_material_schema_data()` 增加 Module 声明元素映射、闭合边界映射、HOST 需求
三个可选内容 schema。`ClosedModuleAuthor(gateway, registration, producer_principal_ref)`
由可信 owner／HOST 显式配置，复用现有 registration gateway 与本域来源绑定。
principal 只记录归属，不是可自行声明的授权凭据；JSON 不加载可执行实现。

`publish(module=..., element_ids=..., command_id=..., parent_ref=None,
copy_sources=None)` 接受已经闭合的 `ModuleDeclaration`。调用方为声明的 Module、
component、port、operation、link、公共 entry／exit 与 terminal locator 各给一个
稳定的 `element:<32位小写十六进制>` ID。locator／显示名不是身份：rename 保留
选定 ID；copy 使用新 ID，并通过 `copy_sources` 明确引用确切 parent 元素。
支持 root 或一个已验证本域 parent。缺漏、重复、悬空及改变 kind 的身份被拒绝。
本片 terminal 必须引用显式声明的 operation。

publisher 使用指定可信 `Registration` 真正编译，固定此次 compile 实际消费的
确切声明，并登记四类资源：Module 定义、元素映射、边界映射、HOST 需求。
protected 内容 schema 沿用既有 frozen catalog 权威；三个可选 schema 使用精确
登记的 schema resource。不把物理 handle、readiness、凭据、运行 net 身份或
adopted 状态作为作者材料输出。

既有 private resource 身份规则每个 command transaction 只支持一份该类资源，
因此不可变准备材料分别先登记，最终 NetRevision 提交才是成功点。首份准备资源的
不可变 metadata 固定完整规范化命令，包括 owner、producer、parent、全部四份文档
及确切 HOST 选择；中断后不能更改尚未写入后续资源的输入。失败可留下准备
资源，但不产生成功修订、Branch 推进或采用。相同重试复用原确切资源；同命令异材料
冲突。最终 SQL 发布失败回滚 revision 事务。

`validate_closed_revision(core, reference, registration)` 是显式材料消费者，与
`read_net_revision` 的 descriptor 语义分开。它在同一 Registry snapshot 检查
canonical exact 对象、publication／commit／promotion 证据、不可变字节、bootstrap
来源与 producer 关系、所选 schema 权威、完整映射、首份资源的完整命令清单，以及
实际编译消费的 HOST 合同。JSON 比较保留类型差别（如 `1` 与 `true`），只忽略格式
和键次序。
材料悬空／不一致的 raw descriptor 仍可作为描述读取，但不能通过该材料消费者。
Branch 发布本身也不会默默升级为材料已验证的承诺。

本片只支持本域 closed module 与 root／单 parent 历史。open_region／BoundaryAdaptation、
选择性移植、三方 merge／resolution 及运行 candidate／采用服务仍属后续工作。普通 graph
来源重建使用[graph 作者](graph-authoring_ZH.md)中的独立 v2 合同。闭合 Assembly
member／lowering map 见下节。
测试将真实材料 publisher 的修订
接到 Branch 创建／推进，期间不创建 socket、子进程，不执行 executor、provider
调用或运行采用。

## 稳定成员闭合装配（v1）

`assembly_schema_data()` 显式增加 `collaboration_assembly_revision/v1` 及 Assembly
plan／lowering-map 内容 schema，不改变当前默认类型清单。
`AssemblyAuthor(gateway, registration, producer_principal_ref)` 复用现有可信 owner
gateway 与 HOST。

`publish` 参数为 `name`、`members`、`connections`、`completion`、`budget_policy`、
`deployment_intent`、`command_id` 及可选 `parent_ref`。成员使用
`AssemblyMember(member_id, display_name, revision_ref)`；连接使用
`AssemblyConnection(source_member_id, source_exit_element_id,
target_member_id, target_entry_element_id)`；根完成选择使用
`AssemblyCompletion(member_id, terminal_element_id)`。成员身份为
`member:<32位小写十六进制>`，生成符号前缀为 `m_<32位十六进制>`。
显示名可重复或改名，不改变生成符号。每个成员固定本域 closed NetRevision 的
确切版本，不跟随 Branch head。允许一个确切本域 parent Assembly；标签和 parent
均是请求内容。

连接选择稳定的公共 exit／entry 元素身份，不用 port 名或数组位置。成员和连接按
规范次序排列，组合始终使用 `ComposePlan(mode="explicit")`。空连接保留独立通道，
不生成广播或隐式 all-settled join。completion 显式选择一个成员的 primary terminal，
并保留其全部 alternatives；其他成员 terminal 显式留作来源，不成为额外根完成条件。
所选 terminal 或 alternative 的 carrier 被连接消费时拒绝。别名／多 producer 与
隐式多 consumer 也被拒绝。

上述检查使用最终上下文中 HOST 真正产生的 place，包含成员内部 link 形成的 alias。
比较 carrier 时保留内部 fusion，只排除新增 Assembly 连接；不会把正常串联在
连接后的融合相等误判为重复 producer／consumer。source 连接消费同实际 carrier 的
全部公共 exit 别名，将其从生成出口移除，并把每个原元素映到同一显式 connection。
target carrier 被多个公共 entry 共用时，即使只有一条连接也拒绝。本片保守拒绝
已连接 carrier 的公共 input／output 跨方向别名。公共边界收缩复用实际最终 fragments，
经过现有 compiled inventory 验证路径重建，不额外调用 HOST lowering；生成作者材料
仍由正常 closed author 消费者重新编译并交叉核对。

调用方必须选择 `budget_policy="shared_exact"` 和
`deployment_intent="same_run_candidate"`。同 key 的 Module budget／bucket 共用，
必须 canonical JSON 完全相等，包括数字类型差别；并非每成员隔离计账。不执行部署。
本片拒绝非空成员 `designer_constraints`，也拒绝已知 AgentWorkflowGraph v1–v4
component key，包括 constraints 被清空的 graph。compile 成功本身不能证明
graph 与派生 operation 一致。

任何依赖 Assembly 的材料准备之前，先用独立不可变 plan resource 固定完整规范化
请求：确切 owner／source／producer、command、parent、member ID／标签／revision、
连接、completion、策略，以及实际编译消费的 HOST 声明和确切资源 ref。随后推导
Module，经过 `ClosedModuleAuthor` 发布，再登记真正的 `rpnh/executable_net/v1`
编译清单和 lowering map。编译清单沿用 protected frozen-catalog schema 权威，
它是资源，不是运行中的 `net_instance`。最终 Assembly descriptor 发布才是成功点。
中断可留下 plan、准备资源或生成的作者候选，但不产生成功 Assembly、Branch 推进
或采用。同命令重试保持原确切资源；即使生成拓扑相等，同命令异输入仍冲突。
严格消费者还核对由 command／parent 确定性派生的结果身份；把同 plan 克隆为
第二个 version 不构成有效重试。

lowering map 分两级：每个成员原元素记录 member ID、确切 revision、稳定 element ID、
原声明 locator、显式处置及生成声明 locator。被展开的 module、已消费的公共边界、
未选中的 terminal 均完整覆盖。独立的 Assembly 来源说明根、引入的 link、暴露边界
及 completion。每个生成声明再指向 Assembly descriptor 所选确切 compiled resource
中的 JSON pointer：component 覆盖实际完整 fragment 与派生节点；port 覆盖 qualified
清单、handle 及最终融合 place；operation 覆盖 handle 和实际 transitions；所有原始
fragment place 记录融合代表。允许 fusion 多对一、lowering 一对多。数组位置只在
确切资源版本中有效，不是新的稳定作者 ID。映射来自最终组合的真实 compile，
不是给成员预编译图机械加前缀。

`read_assembly_revision(core, reference)` 只读 canonical descriptor。
`validate_assembly_revision(core, reference, registration)` 在一个读取 snapshot 中
核对完整材料闭包：原始命令 plan 的确切资源、已验证成员和 parent、独立重建的组合及
HOST compile、生成作者材料、完整映射、canonical 持久字节、schema 权威与
publication／producer 证据。返回 `ValidatedAssemblyRevision`，含 `revision`、
`plan`、`generated`、`compiled`、`lowering_map`。此只读消费者不发布、修复或采用。

这只是 I01 的单层 closed-member 部分，不代表统一方案完成。open_region／
BoundaryAdaptation、递归 Assembly、member copy／split／fusion 来源、三方
merge／resolution、选择性移植仍待实现。下面独立的 v2 合同增加普通 graph 来源证明。
运行 candidate binding／采用仍是独立后续工作。离线检查覆盖真实发布、依最终
上下文派生的 lowering、fusion、改名／升级、非法边界／材料、准备中断点、完整
命令冲突、并发与只读 reopen。

## 来源权威的闭合 Assembly（v2）

`graph_assembly_schema_data()` 显式组合 graph-author 和 Assembly 清单，增加
`collaboration_assembly_revision/v2`、`assembly_plan/v2` 和
`assembly_lowering_map/v2`，不修改默认 catalog，也不重新解释 v1 record。
使用 `AssemblyAuthorV2(gateway, registration, producer_principal_ref)` 和
`AssemblyMemberV2(member_id, display_name, revision_ref)`。publish 沿用上文
参数名及显式 connection／completion record，但使用独立 v2 command 域。
必须是同 exact 本地 owner／source，可有一个同 v2 谱系 parent；不隐式选择 Branch head。

每个成员先经过完整材料 consumer。支持普通 v3、来源权威的
`collaboration_net_revision/v2`，以及 plain closed v1 Module。graph 成员从完整
source／recipe 重建整个 Module，在组合前核对 stable source map、派生身份和实际
HOST selection。plain v1 的非空 designer constraints 或已知 graph v1–v4 component
key 均拒绝，包括删除 graph constraints 的伪装。独立 v1 reader 仍只提供原 closed
Module 证明。带 `native_composition` 的 generated v1 不能作为 plain 证明递归输入；
当前仍是单层 Assembly 合同。

成员和公共边界使用 stable member UUID 前缀。显式 `shared_exact` 保留同 key bucket
身份，并要求 canonical 预算值完全相同；没有新增隔离预算默认。graph constraints
按成员保留在 `assembly_member_constraints` 中。普通 rework permit 仍属于各自
fragment，多个 feedback 输入的一次 rework activation 也只消费一个 permit。
原每节点 cap 与显式 null 的含义不变。`same_run_candidate` 只说明意图，不执行 run、
binding、采用或业务回调。

编译使用真实最终 member context。上文 carrier 检查保留内部 fusion，构造比较基线时
只移除新增 Assembly connections，并复用最终 fragments，不为这次比较额外 lower。
publish 之前必须已经登记最终实际使用的 HOST exact resources；缺件在首 plan 写入前
拒绝。选择依据真实最终 compile inventory，不是 member inventories 的并集。
请求校验不自动登记或修复缺失的 final-context HOST 材料。

第一份 canonical plan 固定完整规范化请求、resolver recipe、exact member revisions
及其材料 refs、最终 HOST refs；也固定随后六份材料的 exact ref、schema、字节数和
SHA256。plan metadata 另锁完整 plan/document envelope。七资源分别为 plan、生成
Module、element map、boundary map、HOST requirements、compiled inventory 和 lowering
map，加上 generated v1 与最终 Assembly-v2 两 descriptor，共九个 durable publication
cut。只有最终 Assembly descriptor commit 才是成功。在此之前可用新的已授权 owner
重开并完成原请求；最终提交但回复丢失时，replay 返回原结果且不新增事实。
即使生成 Module 相同，异请求仍冲突。只有 prewrite blob、尚无 canonical publication
时不构成登记／authority 事实；不过原不可变字节仍可按 ObjectStore 的 exact-version
合同限制重试。

v2 lowering map 保留声明来源，并增加 source-field／role origins 与实际 graph fragment
primitive coverage。没有独立 Module 声明的内部 source port／arc 用 source field 和
compiled role 表示，不虚构 Module element。同一 primitive 可有多个 source origins。
final-context pointer 和数组位置只定位这个 exact compiled version，不是稳定元素 ID。
无来源解释的 primitive 及不支持的 ordinary fragment topology 会被拒绝。

既有 `read_assembly_revision` 与 `validate_assembly_revision` 按 exact descriptor 版本
分派。v2 full read-only consumer 返回 `ValidatedAssemblyRevisionV2`，含 `revision`、
`plan`、`generated`、`compiled`、`lowering_map`。它在同一读取 snapshot 中验证本地
authority、祖先、成员及来源证明，独立重建组合／map，核对完整 command／signatures、
exact generated-v1 pairing、持久材料、schema authority 和 canonical producer／commit
闭包。descriptor-only reader 不提供该证明。另一个独立合法、Module 相同的 generated
v1 不能替换此 exact pair；只读 generated v1 也不会重验 Assembly 的组合来源证明。

本合同只覆盖普通 graph 的有界单层 closed 组合。open region／BoundaryAdaptation、递归组合、
member copy／split／fusion 来源、merge／resolution、selected changes、其他 graph 版本、
跨版本 author／Branch 转换和 runtime adoption，仍属统一方案后续工作。
来源输入与显式 API 边界见[graph 作者](graph-authoring_ZH.md)。

## 显式选择有限作者 ControlIR

`ControlIR.from_dict` / `from_json` 接受 `rpnh/control_ir/v1`，结果交给同一个
`compile_module(author, registration)` 入口。作者文档包含普通 `module`、显式
`bindings`、完整 `atoms` 清单，以及显式 `calls`、`continuations`、`closures`
列表。backend 必须是 `business_pn/v1` 或 `execution/v1`；后者在此 Module
编译器中明确报告不支持。review JSON 不是可执行输入。

首个适配器执行**有限作者期特化**：对明确提供的不可变值求值，得到真实传给
trusted component lowerer 的 operation 配置。每个 executor 必须显式登记
`contracts.control_ir`，声明 `schema_version=rpnh/finite_atomic/v1`、effect
类别、Record `config_type`、精确输入/输出 schema 映射及包含产品数量的闭合
outcome union。Registration 在向 gateway 发布前验证此合同。当前支持 pure
原子合同、普通 `consume_occurrence` / `emit_occurrence` 弧和已经证明为 true
的不可变作者 guard。适配器核对真实 lowered operation/transition 清单、配置
类型、弧多重数和 outcome 路由，从不调用 executor 或 tool。原 operation config
必须是空占位，避免特化静默替换既有配置。

有限类型包括 Bool、Int、Rational、Text、Enum、ExactRef、Record、Sequence、
Set 和 Maybe（显式 known 值或 unknown 原因）。数值 `unit` 维度映射参与算术
和比较检查。Bool 与 Int 区分；Int 不接受包括 `1.0` 在内的浮点数。Rational
沿用 `Fraction(str(value))`，保存规范有理数字符串。字段/参数、算术、floor、
min/max、count、membership/absence、布尔短路、match-known 和确定性 sort
构成闭合 AST 操作集。sort 必须声明 key 及最终唯一 tie-break。表达式不提供
字符串 eval、import、时钟、网络、隐式 history 或 callback，也不增加全局
执行次数或数量上限。

`evaluate_expression(expression, bindings)` 返回 `EvalResult`，包含 value
或 `EvalError(code, path, binding_ref)` 以及精确 `ReadSet`。缺字段是错误，
不是 Unknown；只有实际执行分支增加读集。origin 分别固定每个可变
source/stream/identity/version，或不可变 source-qualified ref。
`ReadSet.stale_heads` 与调用方提供的当前 heads 比较全部观察，不读取 Registry，
也不授予 admission。依赖可变 head 的表达式、false/动态 guard、共享观察、
access 和 quantity 弧需要真正的 runtime adapter；当前编译器明确拒绝，
不会冻结可变事实或丢弃语义。

`control_calls.call_contract(site)` 提供七个固定 compatible-profile 合同：
Q.segment_compact、Q.length_call、Q.pressure_call、Q.normal_call、
C.request_summary、N.adapter_call、M.observe_existing。`rpnh/typed_call/v1`
保留全部 46 输入的类型/mode/source/default/omission 规则及 35 返回，包括
payload 必需字段、父投影/destination、额外 context 和重复/迟到规则。修改
字段或遗漏返回会产生精确作者错误。合法 call 仍产生带 `NEW_VERSION_REQUIRED`
的 `ControlIRCapabilityError`：本作者适配器没有实现 attach/return、wake、
continuation、closure、Q/C/N 迁移或 runtime admission。compatible 的
normal/leaf/compaction attempt 策略仍为 1/1/2。

每次接受的编译在保留字段 `designer_constraints.rpnh_control_ir_v1` 中携带
完整作者输入、求值结果、读集和逐构件能力报告。普通 compiled-net loader
不调用 HOST 即重建和核对此证明。typed 文档固定采用 offline schema 验证，
保留本地递归 schema scope，不访问远程 schema 资源。typed dictionary wire
输入必须使用精确 JSON builtin 容器；tuple 和 dict 子类在回调发生前拒绝。
旧的通用 Mapping 输入接口仍是 trusted Python 接口。保留命名族
`rpnh_control_ir_` 的未知版本或存在但为 null 的 proof 均拒绝。
`load_compiled_control_net(document)` 是要求 typed proof 的消费者使用的固定、
纯数据公开 reader；ControlIR compiler 自己也用它回读输出。缺失、被剥除、
格式错误或不支持的 proof 均拒绝。默认 `load_compiled_net` 刻意继续接受已
删除全部 typed 标记的合法 legacy v1，不声称其具有 typed 证明；没有外部要求
时，无法将这份材料与普通 v1 区分。无标记 Module/v1 的数值 int/float 等价和
默认验证行为不变；executor 的能力声明本身不是 typed author opt-in。

编译仅证明支持的作者到 lowering 边界，不证明发布、采用、执行、provider
行为、派发权或返回至多应用一次。

## 生命周期与稳定性
声明用于作者/验证阶段的候选，不得通过修改对象代替 Registry ref。`cpn.rpnh.__all__` 的声明导出与高级 owner 接口、下划线内部实现分开；schema 有版本不等于每个 Python helper 都是稳定 SDK。字段变化时同步 schema、compiler、示例和文档；旧验证保留原来源。

代码：`cpn/rpnh/__init__.py`、`module.py:ModuleDeclaration,SymbolicNet,validate_document`、`registration.py`、`compiler.py:compile_module`、`petri_contracts.py`，`cpn/schemas/rpnh/module_declaration.v1.schema.json`。发布边界见[运行/Registry](runtime-registry_ZH.md)。

### Assembly v9 的有限 typed 成员

`typed_assembly_schema_data()` 显式安装 `AssemblyAuthorV9` 及 v9 plan、完整
command、lowering-map 合同。成员明确声明 `finite_control_ir_v1` 或
`plain_closed_v1`，所选精确修订及其实际单 parent 历史必须保持该角色。
旧 Assembly 域不变。连接、选定 primary completion、`shared_exact` budget
和 `same_run_candidate` 意图均由 caller 明确选择。本入口不接纳 graph、
open、derived 或 nested Assembly 成员，这些成员需要各自合同。

有限 typed 成员完整保留原 ControlIR author、evaluations、immutable read
origins 和 capability proof。强 consumer 先完整校验原成员，再用最终
component namespace、required schemas 与共享 budgets 下真正降低所得的
fragment 重验原 proof。只把 fragment 字典外层 key 投影回原 component 名；
不猜测局部符号，不代入旧 member 预编译结果。lowering map 固定精确 context、
fragment 字节/hash、source carrier、连接前最终 carrier 及最终融合代表。
Int/Rational、单位、pure atomic 合同、occurrence 弧与生成材料精确字节都会
再次校验；不虚构全局 ControlIR author。

生成 G 是普通 closed Module。普通 G reader 独立使用时不证明 typed Assembly
成员语义。应对精确 A 使用 `validate_assembly_revision`，或用
`validate_generated_assembly_v9(core, A_ref, G_ref, registration)` 强制完整
A/G 配对证明。缺 proof 或错误配对均拒绝，不静默降级。v9 的编译、出版、
carrier 重建及强读回全程固定使用 offline schema validation；旧公共调用
签名及默认行为保持不变。

首次持久完整 command 固定全部材料、身份及 schema authority；恢复与 replay
必须精确匹配。这只完成有限作者装配，不新增执行、call/return、admission、
worker/provider 或 runtime continuation 能力。
