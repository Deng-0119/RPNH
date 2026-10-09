---
name: rpnh-main-thread-history
description: "在固定 Registry cut 读取原生主线程历史。"
metadata:
  document-kind: reference
  audience: trusted-host-developer
  language: zh-CN
  counterpart: main-thread-history.md
  revision: "2026-10-08.1"
  status: native-read-primitive
---

[English](main-thread-history.md) | [中文](main-thread-history_ZH.md)

# 固定切面的主线程历史

## 边界与权威

`MainThreadRegistry` 拥有原生主线程 lineage。历史 API 是高级的
**trusted-host、refs-only** 只读原语，不是授权服务、公开 transcript API、
Codex DTO 或执行／恢复入口。它使用已有的只读 `_RegistryCore`，不获取 writer，
不写 metadata，不 reconcile TaskControl、不 launch child，也不调用 provider。

`capture_read_cut()` 返回 frozen `MainThreadReadCut`，包含既有 task/branch 身份、
一个 `CanonicalView`、exact boundary event 身份和 exact thread version。
裸 `CanonicalView` 只有 ordinal，因此不能直接当 source-bound cut。每次读取重新
检查持久化 source/boundary identity、canonical 可见性、exact object envelope/bytes，
并复用既有 thread、turn 与 child-link lineage 校验。future ordinal、跨 Registry/thread、
损坏的 lineage 或不一致引用均明确拒绝。
既有 child-link path 校验仍检查当前 filesystem 的不安全路径／symlink，但不打开 child
Registry 或读取其 body。因此 filesystem 安全边界变化仍可能让旧 cut 读取失败。

后续追加或重开同一 Registry 后，原 immutable objects 和 identity 仍有效时可重读
旧 cut。同一 native Registry identity 的副本仍是同一逻辑 source；filesystem path
不成为 cursor 权威。cut 和 anchor 都不是 bearer grant；调用方每次请求必须重新检查
已有 session/source 权限。本 API 不扩展 `RegistryReadSession` 或
`TypedReaderCatalog` 的授权范围。

## 投影与分页

`project_thread_at(cut)` 返回 `MainThreadHistoryProjection`。只有 committed turn
产生对话条目，保留原 display membership 过滤。accepted、running、interrupted、
failed turn 不因此获得历史 items；线程 state 与 exact active/latest pointers 单独保留。

每个 turn 保留登记 ordinal 和 exact committed turn ref，并按顺序有两个字段槽：
`user_input`、`answer`。槽的 identity 是 exact turn ref 与字段位置，不是重新编号的
UI list offset。它们是**内容引用，不是公开 body**。原 user_input/answer JSON 可能
含插件配置、task prompt 与 instructions；新 API 不返回这些 JSON，也不沿
answer/decision/receipt refs 解引用 child 资源。没有 body-read 方法，也不接受任意 path。

`page_turns_at(cut, limit=50, order="asc", anchor=None)` 和
`page_items_at(cut, limit=50, order="asc", turn_filter=None, anchor=None)` 返回 typed
page。limit 必须是 1–100 的整数。item 按 `(turn ordinal, field position)` 排序，desc
反转完整顺序。可选 item filter 必须是该 cut 上 exact committed turn version。

原生 `MainThreadHistoryAnchor` 绑定 cut、query kind、order、filter、exact turn identity、
ordinal 和可选 item position。初始 anchor 可设 `inclusive=True`；返回的 continuation
anchor 为 exclusive。只有确实还存在下一条时才返回 next anchor。更换 query、方向、
filter 或 cut 后旧 anchor 被拒绝。没有额外 cursor store 或 transport codec。调用方需要
同一快照时，turns 与 items 必须共用一次捕获的 cut。

## Body 边界与兼容

不存在默认 4 MiB history cap。默认按每个对象的 registered size 向既有
`ObjectStore.read_registered` 传递物理读取界限，backing 超过该登记 size 时拒绝，
不无界读取。capture/project/page 的可选 `max_object_bytes` 是调用方明确选择的预算；
超限使请求失败，不截断或跳过条目。该预算不能将物理界限放大到登记 size 以上。
合法的大 input/answer JSON 默认继续支持，原字典投影也不被收紧。

每一页目前都重建并验证该 cut 的 lineages。页返回数量和逐对象 payload 读取有界；
不宣称扫描工作量或整段历史的总内存有界。Registry row metadata 仍经既有 native query
API 读取。refs-only 返回很小，不代表底层 descriptor 很小。

`project_current_thread()` 和只读别名 `recover_thread()` 保留原字典形状与内容，内部
改为一次捕获的 current canonical view。其信任／披露边界不变。这不意味着
`MainSession` activation/reconciliation 可充当历史读取入口。

## Annotation 与下一 adapter 边界

`child_links` 只包含所选 cut 上 main Registry 的 immutable link facts，不包含 child
path/body。当前 TaskControl annotation 不属于该快照。既有
`MainSession.display_history` 可能用当前 TaskControl fallback 渲染 launch annotation；
新 native projection 不假称能在旧 cut 重建这段实时文本。两个必要历史字段槽完整保留，
immutable link facts 独立返回。

公开 adapter 使用这些值之前，必须把既有安全 display filter 接到固定 cut、已有授权下的
body/renderer 边界，并决定如何单独展示实时 annotation。不得公开任意字段 JSON，也
不得从 ref 推导 child 读取权限。本包不发明 public reader grant 或 catalog entry；没有
实现或认证 Codex `thread/turns/list`、`thread/items/list` RPC、transport cursor、
`itemsView`、cold-resume hydration 或新版 client 兼容。

## 确定性验证

聚焦 native Registry 测试覆盖双向多页、turn 内位置、exact filter、初始／续页边界、
追加稳定性、交错 cut、重开 reader、错误身份、empty/partial/stopped/failed membership、
provisional/future 过滤、大 descriptor、物理 body 界限、损坏与 refs-only 披露。
read-only 测试禁止 writer 操作，检查 event/object counts、physical head、writer epoch、
Registry DB/object bytes、profile 和 owner-lock bytes；SQLite 易变的 `-shm` read marks
不参与 byte 比较。MainSession 兼容测试与 stock frontend／真实 provider 验证分开记录。
