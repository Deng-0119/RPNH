---
name: rpnh-registry-read-sessions
description: "以明确 owner 签发的观察权限执行固定 cut 的 Registry 读取。"
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: zh-CN
  counterpart: registry-read-sessions.md
  revision: "2026-10-09.1"
  status: implemented
---

[English](registry-read-sessions.md) | [中文](registry-read-sessions_ZH.md)

# 独立 Registry 只读会话

## 权威与准备

安装后的只读 HOST 可以在第二进程打开已有 Registry，无须创建 task、admit
Invocation、启动 run、获取 writer fence 或记录 Observation。原 source owner
必须先通过既有 `RegistryRegistrationGateway` 明确签发 observer profile 和
grant。`cpn.rpnh.registry.observer_access` 中的 `issue_observer_access` 与
`revoke_observer_access` 是可信 owner 命令，浏览不会调用签发操作。续期需要新
的明确命令与新会话，不会静默延长旧会话。

可选 `observer_access_schema_data()` 清单增加 v2 profile、grant 以及撤销记录。
canonical owner publication 验证精确 source、task、bootstrap、principal、命令、
writer fence 和治理 head。任意构造的 context、可读取的目录、不透明 source 名、
SourceSet 成员、包摘要或 `HOST` 字符串都不能代替授权。

`ObserverReadScope` 分开声明索引字段、记录字段、精确材料引用、精确导出引用及
导出目的地。字段权限明确列出已注册 entry type 和字段，任何一层权限都不推导
另一层。原有 v1 metadata observer 和受管 Invocation 仍使用既有权威验证。
metadata-only observer 不能读取正文；受管执行的材料交付仍须使用既有 governed
delivery gateway。

这是同一 OS 用户内由 owner 管理的本机 HOST 边界，不是抵御同一 OS 用户恶意
进程的身份认证，也不是远程多租户授权服务。

## 安装后的入口

独立浏览使用可信本机配置文件：

```bash
rpnh net --read-host-config /absolute/path/to/read-host.json --view
```

文件采用 `rpnh/registry_read_host_config/v1`，明确指定 `purpose` 和选定的
`sources`。每个 source 包含 `source_ref`、`access_path`、`registry_root`、
`binding_generation` 和已签发的 `observer_context`。配置 reader 从 OS 用户
获得 caller，查询请求不能自报 principal 或可导入 adapter。文件必须是当前
用户拥有的普通文件、非符号链接，权限为 `0600` 或 `0400`，父目录必须可信。
不要把它放进分享包。

可选 `source_set` 配置指定一个精确的 owner 已发布 SourceSet 及其 manifest
Registry。它把所选来源约束到明确成员及 access path，不负责发现路径或签发
权限。访问边界重新核对配置文件、身份和内容。打开不存在的 Registry 会失败，
不会创建目录或数据库。

安装后的 adapter 调用 `open_readonly_source` 与 `open_registry_session`，
调用方不必使用 Registry 私有构造函数。定制可信 HOST 的
`RegistryReadHostBinding` 把已核验 caller、resolver、现有 authority provider
和 typed-reader catalog 与 `ReadSessionRequest` 分离。resolver 返回带明确
binding generation 的 `ResolvedReadSource`。关闭会话释放它自己的 reader 句柄
和内存缓冲，不撤销 grant，也不停止 run。

## 查询与 cut

`query_index` 接受类型化 `IndexQuery`、`TypedIndexClause` 元组以及有界
`eq`、`in` 或 catalog 声明的数值/时间比较。未知类型、字段、projection 和
predicate 在扫描前拒绝。未披露字段不能成为隐藏过滤或计数侧信道。稳定顺序
为 source、entry type、logical identity、exact version。

索引条目包含精确 source-qualified ref 和获准字段，不内联资源正文。
`read_exact` 另行验证 record 权限并返回固定公开 projection。`describe()`
列出有限支持类型和字段目录。支持 schema-validated descriptor 不代表验证了
材料语义、可以 lower/compile 该类型、具有采用权限或已经可运行。

公开 `SourceCut` 只有 `source_id`、`cut_id`、
`head {ordinal, writer_fencing_epoch}` 和 `reader_contract_version`。
会话内部保留完整 Registry head 与有界 canonical snapshot，不返回原始
任务控制及 stream-head 细节。历史选择使用独立 `HistoricalCutRequest`；
客户端不能修改已返回句柄的 ordinal。cut 之后才 canonical promotion 的对象，
即使 provisional publication 更早，也仍然不在旧 cut 中。

continuation 是不透明的 session-local 内存句柄，绑定完整查询、page size、
来源选择、cuts、当前 authority、resolver binding generation 及精确
reader/schema catalog。普通 canonical append 不移动既有 cut。更换 filter、
字段、page size、source、principal、binding 或 catalog，不能沿旧 cursor
继续读取。相同 cursor 的重放保持同 cut、同一页，且没有 Registry 写入。

默认每页 100 条，整页最多 1,000 条；最多 64 个 source/clause、32 个
predicate/projection 字段、每个 `in` 最多 64 个值、64 KiB 查询输入、4 MiB
响应/材料、100,000 扫描行以及 64 MiB 扫描/保留的 canonical 数据。HOST
可以收紧限制。标量行数/字节预算在无界 history hydration 前检查；每个 cut
只读一遍历史，不对每个索引对象重新扫描。超限返回 `LIMIT_EXCEEDED` 或合法
有界 continuation，不伪报 complete。

## 固定产物来源查询

`session.query_product_origin_v1(root, at_cut, include=None, page_size=None,
cursor=None)` 是公共 Python 会话能力。模块级便捷函数
`query_product_origin_v1(session, root, at_cut, include=None, page_size=None,
cursor=None)` 行为相同，两者都可通过 `cpn.rpnh.collaboration` 使用。
常量 `PRODUCT_ORIGIN_PROFILE`、`PRODUCT_ORIGIN_PAGE_SCHEMA`、
`PRODUCT_ORIGIN_CONTRACT_REVISION` 分别标识 `product_origin_v1`、
`rpnh/product_origin_page/v1` 和修订 `2`。没有 `fields` 参数或逐请求限额覆盖。
现有比较查看器及其 HTTP 接口不提供此查询。

### 精确根与请求

先选择一个已获授权的 source 并捕获 cut。`at_cut` 必须是本会话签发且未被修改的
`SourceCut`，其 source 与 `root` 相同。支持以下根形式：

- `SourceQualifiedResourceRef` 包装原两字段 `ResourceVersionRef`：来源为 canonical
  `petri_output` 或 `workspace_write`，且 producer 为 Invocation
- `SourceQualifiedVersionRef` 包装精确 `operation_result/v1` 的 `VersionRef`，
  ID kind 分别为 `operation_result` / `operation_result_version`

不接受 generic `VersionRef` 包装的 resource、名称、路径、裸 ID、`latest`、隐式 source
或跨源搜索。resource 来源限制只用于根，不限制 Start 输入或 claim 的 resource。
合法请求不会在内部追踪更新的 head。

`include=None` 按固定顺序请求全部三个关系：`producer_execution`、`start_inputs`、
`claims`。显式非空 list 或 tuple 必须由无重复字符串组成，且包含 `producer_execution`；
任何输入顺序均正规化到同一固定顺序。只要生产者证明时，传
`include=("producer_execution",)`，此时既不查找 Start，也不枚举 claim token 或已消费
claims。仅另加 `claims` 不查找 Start；仅另加 `start_inputs` 不要求 Claims 公开字段，
也不返回消费分类。不支持的关系在读取对象前拒绝。

`page_size` 必须是正整数，拒绝布尔值。最大值是
`min(100, session.limits.max_page_size)`，默认值是 `min(20, maximum)`。
错误类型和非正数返回 `INVALID_QUERY`；显式超限返回 `LIMIT_EXCEEDED`，不会截断到上限。
这些 profile 限制不改变 `IndexQuery` 或 `ReadLimits` 的默认值。

### 必需字段权限

在检查对象存在性、completion 数量、Start 缺失或依赖完整性之前，先完整预检本请求
需要的所有 record/index 字段。仅有根读取权限不足。沿用 owner 签发的
`ObserverReadScope`，查询不签发 grant。record 与 index 权限分别检查。

| Entry type | 所有 include 必需的 record 字段 | 条件 record 字段 |
| --- | --- | --- |
| `invocation/v1` | `invocation_ref`, `task_ref`, `net_instance_ref`, `own_transition_firing_ref`, `operation_binding_ref`, `operation_execution_lease_ref` | 无 |
| `transition_firing/v1` | `transition_firing_ref`, `task_ref`, `net_instance_ref`, `operation_binding_ref`, `firing_admission_ref`, `claim_marking_delta_ref`, `admission_marking_checkpoint_ref` | Start：`start_event_id`, `start_transaction_id`, `start_ordinal`, `start_input_binding_refs`, `start_input_resource_refs`；Claims：`claimed_input_refs` |
| `firing_admission/v1` | `firing_admission_ref`, `transition_firing_ref`, `invocation_ref`, `operation_execution_lease_ref`, `claim_marking_delta_ref`, `admission_marking_checkpoint_ref` | 无 |
| `firing_completion/v2` | `firing_completion_ref`, `transition_firing_ref`, `invocation_ref`, `operation_result_ref`, `successor_checkpoint_ref` | 无 |
| `operation_result/v1` | `operation_result_ref`, `invocation_ref`, `transition_firing_ref`, `business_outcome` | Resource 根：`output_resource_refs` |
| `marking_checkpoint/v1` | `marking_checkpoint_ref`, `net_ref`, `settled`, `transition_firing_refs` | 无 |
| `resource_version/v1` | 无 | Resource 根：`resource_id`, `resource_version_id`, `origin_kind`, `producer_ref`, `provenance_producer_invocation_ref`, `provenance_operation_binding_ref` |
| `marking_delta/v1` | 无 | Claims：`marking_delta_ref`, `net_instance_ref`, `phase`, `transition_firing_refs`, `operation_binding_refs`, `consumed_refs` |
| `petri_token/v1` | 无 | Claims：`petri_token_ref`, `net_instance_ref`, `resource_ref` |

所有 include 还需要 `firing_completion/v2` 的 index 字段
`transition_firing_ref`、`firing_completion_ref`、`invocation_ref`、`operation_result_ref`。
Result 根不需要 `output_resource_refs` 权限，也不展开其他 outputs。内部校验依赖不产生
额外 record 或正文披露权。原默认投影不变；新增六类 reader 默认只投影自己的 self ref，
Start 字段必须显式请求。

### 页、证明与行

成功响应恰含：`schema_version`、`profile`、`contract_revision`、`root`、`source_cut`、
`access_revision`、`root_proof`、`rows`、`coverage`、`continuation`。随包提供的
[page content schema](../../cpn/schemas/rpnh/product_origin_page.v1.schema.json)
仅为惰性校验数据，不是新增 Registry 对象或持久事实。

每页重复 `root_proof`，且恰含六字段：`producer_invocation_ref`、`transition_firing_ref`、
`firing_completion_ref`、`operation_result_ref`、`operation_binding_ref`、`root_role`。
所有引用均完整且带 source 身份。`root_role` 的含义如下：

- `registered_output`：精确 resource 属于 canonical result 的 `output_resource_refs`
- `invocation_produced_resource`：resource 不是上述成员，但全部必需 producing closure 成立
- `operation_result`：根为 result

非成员角色仍要求完整 producing closure。同 Invocation 或单独的 `produced_by` 关系
既不足以证明正式输出成员资格，也不足以使本 profile 查询成功。响应不披露其他 outputs、
私有闭包 ID、task/round/net/lease 细节、标题或正文。

只有以下两种行形状：

- `start_input`：`role`、原始零起始 `position`、`input_binding_ref`、`resource_ref`、
  `evidence`、`verification`。Binding 是带 source 的 generic resource 或 token
  `VersionRef`，resource 使用两字段 `ResourceVersionRef` 包装。Evidence 恰含
  `start_event_id`、`start_transaction_id`、`start_ordinal`；event/transaction ID 保持
  typed 字符串。Verification 恰为 `binding_identity="exact_at_cut"`、
  `resource_identity="exact_at_cut"`、`target_record="not_requested"`、`material="not_read"`。
- `claim`：`role`、`token_ref`、可空 `resource_ref`、`classification`、`evidence`、
  `verification`。Classification 为 `consumed_claim` 或 `non_consuming_claim`。
  Evidence 恰含 `transition_firing_ref`、`claim_marking_delta_ref`。Verification 恰为
  `token_record="verified_at_cut"`、`resource_target="not_requested"`、`material="not_read"`。

Start 行在 claims 之前，保留原 position，包括不同位置重复的 resource。Claims 按完整
source/entity/logical/version 身份排序，不同 role 不合并。Start 记录实际输入 resource
版本，substitution 后它可以合法地不同于 token 的 resource。Claim 的 resource 引用
不代表读取过目标 metadata 或正文。仅生产者的成功响应为 `rows=[]`、`continuation=null`。

### Coverage、分页与有限范围

首个成功页之前，所有必需 producing closure 和请求关系的全部 candidate/endpoint 检查
都已完成。后续位置有损坏项、缺少必需 witness、字段未授权或校验预算不足，均不能返回
前半成功结果或 cursor。

`coverage` 仅含 `scope="authorized_root_at_cut"`、`state`、`relations`。生产者关系始终
为 `{state: complete, witness: verified_at_cut}`。已请求 Start/Claims 的
`witness="verified_at_cut"`；截至本页累计交付完整时 state 为 `complete`，否则为
`partial`。合法空关系即使其他关系尚未交付完也为 complete；尚未开始交付的非空关系为
partial。未请求关系恰为 `{state: not_in_profile}`。以下固定不支持标签始终用相同形状，
不查询存在性：`direct_derivations`、`calls_in_execution`、`observed_reads`、`formal_access`、
`declarations`、`parent_child`、`recursive_ancestors`、`content_influence`。请求任一项返回
`UNSUPPORTED_RELATION`。没有隐藏、候选、扫描或总数量。

全局 `partial` 仅表示完整校验且已授权的行尚未交付完，同时 continuation 非空；全局
`complete` 的 continuation 为 null。整页 envelope、重复 proof、coverage、continuation
均计入响应字节上限。为满足字节限额，行数可少于 `page_size`。必要 proof/envelope 或单个
必要行装不下时返回 `LIMIT_EXCEEDED`，不返回空行页加 continuation。历史 capture、逻辑
校验工作、保留状态和响应字节分别受现有 session 限制约束。

续页必须带回未变的完整请求，包括 root、cut、include、page size，以及返回的 cursor。
仅顺序不同的等价 include 会正规化为相同集合。Origin 与 index cursor 共用会话槽上限和
生命周期，但不能互换（`CURSOR_MISMATCH`）。重放复用已验证 rows 和下一 token，不新增槽。
成功、重放和受保护数据相关错误均在序列化后复核当前 authority、source binding、
schema/catalog 和到期时间。安全错误响应只含 `code`、固定 `message`、`reopen_session`；
authority/到期错误覆盖待发的数据相关错误。

支持普通同-net sibling Success。合法 changed-net settlement 返回
`UNSUPPORTED_SETTLEMENT_SHAPE`，本查询不执行 bridge 或 replay。晚到 canonical promotion
不能让对象在旧 cut 中变得可见。新会话必须重新捕获当前或显式历史 cut，并通过当前权限
检查；旧 handle 和 cursor 不能转移。

`complete` 仅描述本次有限授权根、cut 和所选关系，不证明全部 workflow 历史、递归祖先、
实际阅读、delivery acknowledgment、工具调用因果或模型/内容影响。
完整请求续页示例见[独立读取指南](../guides/independent-registry-reader_ZH.md)。

## 交付与失效

每页、精确记录、正文、projection 和导出授权，都在交付前重新核验当前权限，
包括 callback 完成后的会话到期时间和精确 catalog fingerprint。默认会话
最长十分钟，HOST 限额与任何 authority expiry 都可缩短它。

撤权、writer fence/治理 head 变化、source/path binding 变化或 reader/schema
合同变化，会令相关缓存和 cursor 失效。Query 缺口保持 null 计数，不把不可读
source 伪装成零对象。Comparison 的任一必要侧失效时，其消费者清空整对。
`global_atomic_snapshot` 始终为 false：逐源 cut 和 final check 不构成跨源
分布式原子快照。

`read_material` 是独立的有界正文操作。默认 `representation="bytes"`；
`representation="utf8"` 对已验证字节严格解码，并在 `body` 中返回文本。
错误 UTF-8 返回 `INVALID_UTF8`，不支持的表示返回 `INVALID_REPRESENTATION`。
响应保留现有调用者使用的 `bytes` 和 `sha256`，并提供 `material_digest` 及
已登记的 `content_schema_ref`。两种表示的限额和 `byte_count` 都按原始字节计算。
它验证已登记字节数和已记录的摘要；旧资源没有历史生产者 checksum 时，不把
新计算的摘要冒充历史校验值。不猜测编码，也不静默截断正文。导出授权另行核对
每个精确 ref 及明确目的地。观察读取
不会生成受管执行的 delivery receipt。

`capture_observation` 明确返回 `UNSUPPORTED_OBSERVATION_CAPTURE`。旧
SourceSet/Observation capture 合同仍通过原 API 使用；新 session 的普通分页
不依赖它。修改 wire 值不能恢复已关闭或已到期的句柄。导出包保留的 source-cut
证据只是 provenance，不是可转移的当前权限。

## 验证边界

确定性测试 `tests/test_registry_read_session.py` 使用真实合成 owner 签发，
覆盖独立第二进程配置读取、零事实/fence 写入、分页、分层权限、固定 cut、
缺失来源、读取中途失效和有界扫描。不调用 provider，不签发真实外部权限。
这些是普通功能回归，不代替另行设门的独立对抗性权限审计。
