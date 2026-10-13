---
name: rpnh-typed-registry-exchange
description: "通过分别授权的公共边界读取 exact 类型化 PN 投影并交换普通 closed 子网。"
metadata:
  document-kind: reference-guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: typed-registry-exchange.md
  revision: "2026-10-13.1"
  status: source-reviewed-pre-release
---

[English](typed-registry-exchange.md) | [中文](typed-registry-exchange_ZH.md)

# Registry 类型化投影与子网交换

已安装的读取 HOST 通过独立 Registry read session 使用 `TypedReaderCatalog`。
Catalog 只是解析器/reader 清单，不授予权限。Session 在调用 reader 前检查所选来源、
exact 引用、当前权限和固定 cut，在结果交付前再次核验。

## 支持边界

| 条目 | exact 公共记录 | PN 投影 | 子网交换 |
| --- | --- | --- | --- |
| resource v1 | 仅 header，不含正文 | 不适用 | 仅必要声明材料 |
| author revision v1 | 类型化 descriptor | 普通作者、显式 plain transform 与 plain merge 的存储投影 | plain closed 根/单亲历史；经验证的有序双亲 plain merge |
| graph author v2/v3 | 类型化 descriptor | 明确不支持 | 明确不支持 |
| Branch v1/v2/v3、SourceSet v1 | 类型化 descriptor | 不适用 | 不导入为新 Branch/来源选择 |
| Assembly v1–v8 | 类型化 descriptor | 明确不支持 | 明确不支持 |
| Assembly v9 | 类型化 descriptor | 新生产者存储的最终 inventory，以及实际最终上下文映射重建 | 明确不支持 |
| candidate plan v1/v2 | 有界 descriptor 投影 | 不声称 candidate 有效/ready | 明确不支持 |
| native run identity、terminal evidence | 有界公共 descriptor | 不虚构执行历史 | 默认不包含 |
| net instance v1、marking checkpoint v1 | 有界公共 descriptor | exact 存储 compiler/dependency 闭包；checkpoint token occurrence | 不导入运行态 |
| execution checkpoint v1 | 有界公共 descriptor | 明确不支持 | 明确不支持 |

未知版本闭合拒绝。旧作者记录未存储新投影时返回 `PROJECTION_UNAVAILABLE`，
读取不会修复它。Descriptor 支持不代表材料验证、candidate 有效、可采用、可执行或
环境准备完成。资源身份保留 source-qualified 引用；artifact digest 不是 Registry ref。

## 存储投影与映射

`ClosedModuleAuthor`、`PlainModuleTransformAuthor`、`PlainModuleMergeAuthor` 和 `AssemblyAuthorV9`
在 revision 成功出版前发布独立、不可变的投影资源，保存生产者实际消费的 compiler
inventory，以及 exact 材料依赖摘要。公共读取复核这些字节、source/element/boundary/
HOST 材料、投影、层级及映射，只用离线 verifier 重新读取 compiler wire，不调用
lowerer、不发现插件、不编译声明、不启动 HOST。

可选 parent/copy/transform 证据一开始不可访问时，reader 独立重验所选图自身材料，
返回 topology、`mapping_coverage = not_provided`、空关系，不推断成员范围。
自身图材料缺权限仍失败；完整性失败或读取中访问变化不静默降级为缓存旧图。

公共图沿用 Viewer 的 node/edge 白名单。configuration 仅公开 exact
`host_requirements_ref` 和 `declared_configuration_ref`，不公开任意 operation config
或凭证。作者 HOST 注册声明与包 environment requirements 是不同合同。Assembly
公共 origin 不包含原始 fragment context/命令 metadata。运行图配置只提供所选存储声明引用。

Retained/copy 与 split/fusion 保留 exact 来源/结果 revision、完整 endpoint 组和原始证据。
一般 N→M、跨多代推断不支持。复制产生新的作者身份，作者对应不证明 runtime token
同一性。edge role 来自明确 operation/port 归属，重复且无法区分的 role 不按名称或数组位置猜配。

Assembly v9 从存储成员和最终 inventory 重建实际最终映射，复核 source material lock
和纯数据 composition，保留声明的成员 occurrence；不会假设成员独立编译的 fragment
与最终上下文完全相同。

Checkpoint 投影检查 exact commit witness、所选 net/declaration 闭包和 token occurrence
引用。单独 net 的 runtime coverage 为 `not_provided`；checkpoint 只提供所选 marking，
不暗示 firing/业务结果历史完整。

## 导出

先调用 `plan_subnet_export(session, root_revision_ref=..., cuts=...)`，再调用
`export_subnet(session, plan=..., destination=...)`。当前唯一默认选项是
`definition_closure`。结果包含已验证 portable package 及 exact dependency archives。
调用方处理已授权目的地；此 API 不把 destination 当成任意磁盘路径或 URL。

Plan 闭合普通 definition、element/boundary map、实际 HOST/schema 声明、必要 parent
证据及存储投影。缺源、缺依赖、环、错误版本或超预算均明确失败。真正导出会重建 plan、
重读每份正文、对每个 included ref 及 exact destination 单独核验 export 权限，再终检
来源/权限。调用方自行拼装 plan 不能省略导出检查。

包仅保存历史 SourceCutEvidence，不携带 session/cursor/cut handle。除外层 portable
manifest inventory，还核验内层 exchange 的每份 byte count、digest、exact material ref、
parent/material 闭包及存储依赖 pin。ZIP 排序和时间戳确定；统一路径分配器同时避开所有
已保留及新生成路径，包括大小写碰撞。

## Plain merge 交换 v2

闭包包含有序双亲 plain merge 时使用内部 `rpnh/subnet_exchange/v2` 清单。
外层采用 `rpnh/share_package/v3`，必填 `subnet_exchange` artifact 路径及 exact 内部 schema 版本。
v3 preview 和 resolution-lock 合同保留这一标记；原 v1/v2 schema 及默认 API 行为不变。
没有惰性 v3 来源的普通根/单亲历史继续使用原外层/内部版本。

本地导入修订再次导出时，如果原始惰性包为 v3，外层也保留 v3；新本地历史仍为内部 exchange v1。
这避免旧 reader 将来源证明静默解释为无关 generic document。4544cdc 的 v1/v2 parser 在导入前
以 `UNSUPPORTED_PACKAGE_SCHEMA` 拒绝外层 v3。手动改 manifest 版本不是受支持的兼容转换。

v3 可选 `environment_requirements_path` 沿用原 exact v1 环境合同，不从 HOST 声明编造环境。
v1/v2/v3 混合闭包使用 lock v3，低版本 root 不能选择 v3 依赖。
只要有一个所选 entry 缺正式环境要求，环境准备仍为未声明；版本门不授予执行权限。

导出必须通过既有 `rpnh/plain_merge_public_pn/v1` 固定 cut 生产者验证器。
完整收录每个 exact proof 依赖：来源身份、owner/bootstrap/principal descriptor、
固定 schema 权威、祖先修订、原始材料、analysis、调用方 choices、result command、
compiled inventory 与存储图。这些 descriptor 仅为惰性证据；每项都须单独具备导出权限，
与材料、修订同样检查。不复制 observer grant、participant/session 授权或活跃运行态。

导入复用原纯 merge verifier，重构 exact base、有序 parents、归一化 analysis、choices、
结果材料、HOST inventory 和公共图。外层祖先记录必须与 proof descriptor 字节一致；
重复、缺失、未消费的 proof pin、身份变化及不支持的 producer 均闭合拒绝。
来源证明只建立历史内部一致性，不认证外部发布者，不证明当前访问权限，不生成新本地 merge、
运行连续性或接收 owner 的授权。新本地 revision/element 身份只保留显式 copy-origin 对应。

共享祖先深度按最长路径计，所有 parent/proof 边在导出和导入中累计到同一包预算。
Merge archive 及保留原包的再次导出保持固定成员顺序和时间戳，并使用 DEFLATE 以适配既有惰性 provenance 大小限制；
展开字节、单 artifact 和压缩比限制继续有效。原 archive 字节仍在 provenance 中精确固定；
新压缩导出产生新的 archive digest，不改写原包。同 command 导入重试仍要求原 exact plan。超预算历史仍不支持。
designer constraints、graph、Assembly、selected-change 及其它 specialized 合同继续拒绝。

## 原包祖先与再次复用

原始 package 从已验证修订祖先闭包查找，不只读取当前 merge root 的字段。
导入的 seed 可以修改、fork、merge，而不丢原任务 fixture、文档、许可证、环境 artifact
和 exact 依赖 archive。要求唯一原 archive/lock；不一致多来源以 `AMBIGUOUS_PACKAGE_ORIGIN`
明确拒绝。保留原 requirement ID 供环境映射；新增 HOST requirement 另行附加，
不声称原环境已经满足新合同。

惰性 copy-origin 会递归核对原 archive/lock、source exchange、所选 definition、import plan digest、
copy command 及全部新分配的 element 映射。逐个 archive 在展开材料解析前按剩余预算准入；
所有传入依赖收费，未选入 lock 的多余 archive 拒绝。嵌套包与修订祖先深度共同计数，
全程共享 archive/展开字节/member/edge 预算。历史验证不在接收方创建来源principal、grant、
lease、Branch、运行事实或权限。

这也收紧旧内部 v1 的惰性历史接纳：以前可能接受的不一致、损坏或超预算历史现在会失败。
普通 v1/v2 schema 字节不变，不意味着所有旧包都会通过加强后的语义检查。

## 导入

`plan_subnet_import(package, target=TargetRegistrySelection(...), local_packages=...)`
完全惰性，所有 required dependency 必须从 exact 本地 archives 解析，不联网获取、不安装、
不准备环境、不采用、不运行。

`import_subnet(owner_author, plan=..., command_id=..., expected_target_head=...)`
要求目标原有的 `ClosedModuleAuthor`，再次验证包与闭包。首次命令预约在原生
`BEGIN IMMEDIATE` 写事务内比较 target ordinal 和 writer fence，保持既有 canonical
resource publisher/owner 验证。同命令改输入冲突；相同命令重试复用原本地 revision 和
资源身份，即使 Registry 后续已有追加。中断可能留下不可变准备/预约事实；同命令重试
完成原操作，不覆写既有对象。

导入的 revision 和 element 使用新的本地身份，结果附 exact copy-origin map。
Registry export 保留 source-qualified 历史 provenance；普通包使用带 manifest digest、
artifact path、entry 和 element locator 的 `package_artifact` origin，不伪造 source_id 或
Registry 历史。包内来源声明只是历史包证据，不是可转授权限，也不证明今天仍能访问来源。

惰性 schema 通过目标原有 owner gateway 注册，同 schema 身份冲突时拒绝。可执行实现
必须已经在受信任目标 Registration 中。Registry export 记录的 HOST declaration identity
必须与目标一致，但这不证明实际运行行为。不推进 Branch、不覆盖配置。

原始选中 root archive 和每份 exact lock dependency archive 保存为惰性、带摘要的导入
证据。重新导出保留所有原始 environment artifact 字节，包括非 entry 文档。环境身份是
`(manifest_digest, path)`，不同包使用相同路径仍分别保留。生成路径冲突时更新输出
manifest 引用；依赖 archive 原字节继续用于确定性重导出/重导入。过大证据在首次出版前拒绝。

## 完整性和验证证据

旧 `resource_version/v1` 没有普遍记录 payload checksum。当前计算的 SHA-256 与已记录
完整性分别标注；仅长度匹配不能证明旧正文同尺寸未变。新投影/导入 publication 记录
`content_sha256`，投影闭包验证已记录依赖摘要。不能把旧正文现算 digest 说成历史 checksum
验证通过。

定向离线测试位于 `tests/test_registry_typed_exchange.py` 和
`tests/test_registry_read_session.py`，覆盖真实生产者、正式独立 observer 签发/session
导出和目标导入、撤权、split/fusion、上下文敏感 Assembly v9 重建、exact runtime/checkpoint
读取、过期 head/竞争写、重试/中断恢复、内层 inventory 篡改、缺依赖及 v2 路径/字节保留。
这些功能测试不替代独立权限审阅、真实浏览器验收或同一精确包的业务 terminal 验收。

导入写入 command reservation 前，来源 HOST 每个 schema 都必须在 exact selected package
闭包中有相同字节；同 `$id` 的 artifact 替换不能改变来源证明所选 schema。

Merge 专项确定性回归命令为
`python -m unittest discover -s tests -p 'test_subnet_merge_exchange_unittest.py' -v`。
它需要已声明的 runtime 依赖，不需要 pytest；验证真实 Registry/observer/session 交换及对抗闭包案例，
不代表整套多人项目旅程或导入后的业务执行已经完成。

lock 选中的所有包（包括 v3 依赖）的 mandatory exchange 均在同一共享预算内验真；
未选候选仅做有界 preview，不遍历其 exchange 语义。单 archive 的 members 仍为128；
新增末尾参数 closure_members=256，累计所选及递归保留的 archives，重复 archive 仍收费。
完整18-artifact seed 实测 M 导出总96 members，再导出195，单 archive 最大99；
紧聚合限额会明确拒绝，不能扩大单包限额。
