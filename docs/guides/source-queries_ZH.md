---
name: rpnh-source-queries
description: "登记并查看有限、真实授权的 SourceSet 观察。"
metadata:
  document-kind: how-to
  audience: trusted-host-developer
  language: zh-CN
  counterpart: source-queries.md
  revision: "2026-10-05.1"
  status: opt-in-source-observation-v1
---

[English](source-queries.md) | [中文](source-queries_ZH.md)

# SourceSet 查询与持久观察

可信 HOST 可以发布版本化预期来源清单，通过每源既有 ResourceService 资格查询，显式
记录结果，再让 Agent 与 [Viewer](viewer_ZH.md) 读取同一结果。这是显式启用的元数据接口，
不启动或恢复工作、不交付正文、不颁发 grant、不发现远程 Registry，也不建立远程信任。

## 登记范围

在 HOST 的显式 catalog 中组合 `source_observation_schema_data()`。既有
`RegistryRegistrationGateway.bind_source_identity` 建立本地唯一来源身份。
`SourceMember` 包含精确 source-qualified task 引用、访问路径标识、别名和 `link_kind`。
来源链接分类是 `immutable_snapshot`、`editable_fork`、`exclusive_capability` 和
`control_message`；分类本身不复制、转移或授予能力。

初版调用 `gateway.publish_source_set(members=..., command_id=...)`。后继必须同时传入
`expected=old.source_set_ref` 和 `expected_sequence=old.sequence`。既有 Registry 事务
在提交时比较精确前驱与 sequence；中途推进会冲突。同一 command 重放返回原不可变版本，
同 command 异内容冲突。重新打开后仍可读取旧版。

离线、拒绝、尚未附着来源仍属于预期清单。按精确 source identity 对别名去重；同源身份
或路径清单冲突会拒绝。别名、路径标识都不是文件系统位置，也不授予权限。

## 查询与记录分开

用只读清单 Registry、显式 HOST resolver 以及本地记录操作的 `capture_context=...`
构造 `SourceSetQuery`；canonical Viewer reader 不传 capture context。resolver 接收
`(source_id, access_path)`，返回 `RegistrySourceReader(read_only_core, existing_context)`，
或抛出 `SourceUnavailable`。它必须提供已获授权的精确 `InvocationContext`，或已经颁发且
兼容的 observer context。本功能不提供 observer grant issuer，也不自动替换资格。

`query(source_set_ref, paths=..., limit=..., media_types=...)` 对每个预期源精确选择一条
已登记路径，调用既有 ResourceService query。每源独立固定自己的 canonical head、cursor
和当前资格。同源另一条路径不会扩大该路径权限。缺源计数为 null；完整且有权读取的空查询
可以是 0。任何源缺失或尚有续页时，全体总数都是 null。计数只针对本查询有权披露的 headers。

query 返回不可变 `SourceObservationDraft`，零 Registry 写入。显式调用
`gateway.record_source_observation(query=query, draft=..., command_id=...)` 时，共享source
validator在固定cut回放精确有限页，完整比较canonical header、cursor、offset及完成依据，
并再次核验capture资格，拒绝伪造draft。记录属于本地capture Invocation的provisional firing区；
普通事务提交不会自动晋升其引用。这次独立写入不是资源读取，也不证明正文已交付。

下一页传入 `previous=recorded_observation_ref`，同时保持清单、路径、limit 和 filters
不变，再显式记录新 draft。暂存旧页只能通过精确同Invocation的FiringView读取，canonical
GET与另一firing不可见。之后原Success通过未修改的Registry门一次晋升整组观察。
仅未完成源读取新页；已完成源只重新核验资格，不扫描整源。
返回值包含仍获授权的旧 headers 和新页，消费者应替换结果，不拼接未经核验的客户端数据。
原观察中缺失的源继续保留缺口；后来可用性通过新查询或显式补读调查。S@2 发布后，S@1
续页不会混入 S@2 成员。

初次查询可传 `through={source_id: canonical_ordinal}`，分别选择每源 cut。这些位置是
观察向量，不是全局原子快照。历史读取仍受当前资格约束。

所有持久capture完整保留原source-qualified资格引用，不用fingerprint替代Invocation依赖。
Success之后原Invocation已关闭I/O，不能续旧cursor；另一reader须新建Observation或补读。
Success后完全相同record command重试只返回原ref，不重开I/O；异内容或producer会冲突。

## 独立补读

`supplement(observation_ref, dependency=SourceQualifiedResourceRef(...))` 沿原来选择的
路径读取一个精确资源 header，保留自己的 cut 和依赖引用，可用独立 command 记录。
缺失或拒绝的依赖保持明确。原 Observation 不变；较晚补读不证明旧观察向量已经因果完整。
不得静默替换发生变化的 source/path 资格。

## Viewer 接入与当前访问资格

配置 `SourceObservationView(query, explicit_observation_refs)`，通过 `source_observations`
传入 `RegistryDashboard`。既有 Viewer 提供 `GET /api/v2/source-observation`，可传精确 JSON
`observation_ref`。GET/HEAD 只读取选定的已存结果，未选中引用拒绝。实际“已记录来源查询”
面板显示来源 cut、路径、已加载／总数／未知和新清单提示；观察按钮只导航显式配置的持久结果。
主图／checkpoint 时间轴和来源观察分别保留各自位置。

GET仅可选择已canonical晋升的capture。同一consumer将原登记capture context作为历史
证据核验，另行检查原路径当前合法reader资格；逐header与原cut的canonical源投影比较。
独立validation query只核对已存有限页与完成依据，不给旧cursor替换资格，不追加新可读行。资格改变或来源不可用时，从响应和 UI 清除旧 headers 与计数。持久捕获本身不变；
响应分别显示捕获时分类、查询过滤与披露标识以及当前访问。
历史缺源若没有新probe，当前访问为not_checked；未提供reader为not_established，明确离线
为unavailable。缺清单不能根据在线来源推断完整范围。HOST 显式选择 Viewer
可展示的结果是一项披露选择，并不授予源访问权。

## 验证边界

定向测试在合成SQLite Registry初始化并准入普通纯operation，再通过原Start/products/Success
API与显式schema-valid静态输出字节晋升观察。Start只ack合成Registry输入；注册executor
从不调用，也不执行模型、provider、worker、socket、外部effects、workspace捕获、replacement
或远程transport。query/GET零写入与显式record、fixture结算写入分别计账。测试覆盖同firing
分页、Success前canonical不可见、新当前reader读取历史GET、closed cursor拒绝、CAS/replay、
source/path身份、缺失计数、资格变化、精确补读及实际UI model/panel消费。真实目标OS浏览器
绘制与导航另验；Node测试不证明浏览器或远程传输行为。
