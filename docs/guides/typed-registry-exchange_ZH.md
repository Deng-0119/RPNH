---
name: rpnh-typed-registry-exchange
description: "通过分别授权的公共边界读取 exact 类型化 PN 投影并交换普通 closed 子网。"
metadata:
  document-kind: reference-guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: typed-registry-exchange.md
  revision: "2026-10-07.1"
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
| author revision v1 | 类型化 descriptor | 新普通作者与显式 plain transform 的存储投影 | 仅普通 closed、根/单亲历史 |
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

`ClosedModuleAuthor`、`PlainModuleTransformAuthor` 和 `AssemblyAuthorV9`
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
