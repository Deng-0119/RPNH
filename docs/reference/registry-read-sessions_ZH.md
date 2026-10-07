---
name: rpnh-registry-read-sessions
description: "以明确 owner 签发的观察权限执行固定 cut 的 Registry 读取。"
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: zh-CN
  counterpart: registry-read-sessions.md
  revision: "2026-10-07.1"
  status: implemented-offline-functional-coverage
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
