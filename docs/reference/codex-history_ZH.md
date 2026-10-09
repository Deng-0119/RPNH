---
name: rpnh-codex-history
description: "Codex 0.155 owner 会话固定 cut 历史投影。"
metadata:
  document-kind: reference
  audience: trusted-host-developer
  language: zh-CN
  counterpart: codex-history.md
  revision: "2026-10-08.1"
  status: partial-frontend-compatibility
---

[English](codex-history.md) | [中文](codex-history_ZH.md)

# Codex owner 历史

支持版本仍为 **Codex 0.155.0**。thread/turns/list 与 thread/items/list 从 basic
主会话同一个 MainThreadRegistry 分页；state.turns 仅保留原 live reconciliation
缓存职责，不作为历史 authority。0.161 官方 schema 仅用于研究，不构成运行认证或换 pin。

## 原有 owner 权限边界

launcher 预绑定单 MainSession，使用私有临时目录与 0600 Unix socket。initialize
核版本/协议，不独立认证身份。每次历史读取前，以及取得响应发送锁后，均重核原 owner
lease/source。owner关闭、root/lock/Registry DB/object-store根替换、实际DB路径重绑、
native source身份变化都拒绝请求。cursor只说明位置，不能授权其他root/source/caller/child。

MainSession.history_registry 经既有 _RegistryCore(create=False,read_only=True) 对同一
物理Registry创建只读句柄，连接为mode=ro/query_only；不新建Registry、writer epoch、
grant、owner lease或第二authority。live owner仍使用原writer。历史查询不new/resume
MainSession、不persist profile/state、不调provider、不打开child、不launch/reconcile。
thread/resume是组合入口：保留原live reconnect/tracking，但初始历史cursor由独立纯读
路径生成。原后台tracking稍后可以reconcile terminal turn，不应把它算成分页动作。

保持原可信local-owner/immutable Registry威胁模型。路径pre/post检查不保证抵御同OS
权限恶意ABA竞态；全局ObjectStore no-follow/race加固不在本包。SQLite首次只读打开可
产生-shm/空-wal bookkeeping；query-only不代表目录每个字节都不变。物理字节测试在
fixture writer结束、读句柄就绪后建立窗口，分别核canonical DB/objects/非空WAL与
event head/writer epoch。-shm读标记是易变协调信息，不是authority。

## 同 cut 安全渲染

native cut绑定task/branch、boundary event与ordinal、exact thread version。每页重核
cut上canonical lineage。child-link发布可能只推进boundary而不改变thread_ref，所以
必须保留完整cut。仅committed turn生成历史items；accepted/running/interrupted/failed
不自动变为对话正文。

MainThreadRegistry._committed_turn_documents_at 将committed membership与exact-at-cut
hydrate封在一起。MainSession独占公开renderer，只输出MainDisplayTurn：原用户text、
decision.reply、原protocol_valid提示、cut上exact origin link对应的不可变launch annotation。
不输出raw user_input/answer、native_plugins、profile、task.prompt、instruction、graph
或child answer/decision/receipt正文。不沿当前TaskControl fallback把未来annotation补入旧页。
没有真实历史时间事实的timestamp为null，不用请求时钟虚构。

## 协议细节

- turns默认desc、itemsView=summary；items默认asc。默认25条；合法uint32 limit按0.155
  clamp到1..100，0也归1；错误类型、负数、溢出拒绝。items可按已committed公开turnId过滤。
- notLoaded严格items=[]；summary/full包含RPNH支持的两个安全文本item。full不意味着
  child/tool内部数据。items响应为{turnId,item}条目。
- nextCursor从末项继续且exclusive，只在确有剩余时出现；backwardsCursor锚定首项，
  inclusive并绑定反方向。空页两者均null。
- resume只捕一次cut生成双初始backwards cursor；初始锚inclusive。空cut boundary token
  确保resume后追加也不会污染两个首次查询。初始turns token明确itemsView未绑定，因为
  官方resume未指定view；第一页的continuation绑定实际view。
- cursor最多4096字符，严格解析版本、thread/source/cut/query/order/view/filter/位置；
  无新持久cursor store或签名/授权服务。重新打开同source owner可重验旧cut；cursor不能
  换source或绕过失效绑定，也不声称它是防篡改grant。
- thread/read(includeTurns=true)在一个cut上通过safe native pages履行显式全历史读取。
  普通thread文档不携带state.turns缓存正文，cold hydration走分页。

默认仍以每个对象registered size作为物理读取上界。可选server构造参数
history_object_max_bytes/history_response_max_bytes明确超预算整请求失败；默认均None，
无隐式4MiB cap或静默截断。page/refs计数有界，但native每页仍验证lineage，不能把页大小
当整体扫描CPU/RSS或全历史总量的界限。

## wire稳定身份与升级

stock Rust客户端实际以UUID解析threadId，JSON schema却只标string。Codex现在把既有
确定性ses_<hex>显示身份的同一128bits规范为UUID；其他前端ID、Registry身份、root与
持久事实不变。旧Codex sidecar protocol ID本来就不是authority，加载时忽略，可在不删
历史的前提下重建显示缓存。旧ses_请求/cursor明确拒绝，不作为别名授权。更新后重新打开
原canonical session即可取得UUID。

turn/item ID复用live命名空间与真实native ordinal，保留failed/interrupted序号间隙；
terminal notification与cold items的ID一致，不按committed列表重排。

0.161新增exclusive的object item anchor（{type:"item",itemId:...}）、可选item毫秒时间戳
与resume可选字段。当前0.155 adapter拒绝新版输入形状。真实stock0.155 cold resume仍是
单独native gate；纯JSON/Registry测试不能证明transport/TUI已通过。
