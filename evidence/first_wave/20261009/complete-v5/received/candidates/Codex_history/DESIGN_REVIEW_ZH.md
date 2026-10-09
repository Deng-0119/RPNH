# Codex 0.155 owner 固定 cut 历史投影：实施前审查稿

日期：2026-10-08。当前只提交设计审查，尚未实现公开 body 或 RPC。

## 结论与权限纠正

可以沿现有本地 owner MainSession 显示边界做窄扩展。缺少 TypedReaderCatalog 的
main_turn 类型不等于本地 owner 无权读取自己的既有会话；不新增 reader grant、认证
token 或第二授权通道。真正需要补的是原绑定/生命周期的每请求复核和同 cut 安全渲染。
initialize 是版本/协议门禁，不是独立身份认证；owner lease 是单 owner 锁，不是 bearer grant。
既有主体准入来自 launcher 的私有 TemporaryDirectory + Unix socket 0600，以及 server
预绑定的单 MainSession。WebSocket 不能传 root/path/source 选择任意 Registry。

证据位置（codec overlay 的 Codex 文件，history overlay 的其他文件）：

- codex_app_server.py:151,205–208,323–395,605–608,637–645：根目录、单 session、lease 与
  threadId 已有绑定；1390–1420：私有 Unix socket；1140–1162、1290–1332：初始化与生命周期。
- session_access.py:32–65：现有 root/profile/Registry/symlink 检查；68–148：owner lease。
- main_session.py:466–476、535–587、78–88：输入、answer 验证与安全显示白名单。
- main_session.py:601–617、1077–1092：当前 display 会刷新当前头，并查询当前 TaskControl。
- main_thread.py:159–229：已有 exact + canonical view + registered size 校验；1400–1602：
  source-bound cut / refs-only page，可复用，不把 refs 视为新的正文权限。
- registry_read_session.py:101–145、262–278、307–348 是通用多 source reader 的独立准入；
  可以借鉴 recheck 的检查方式，不能把该另一客户端授权体系强加给 owner display。

现有 close() 释放 lease 后仍保留 _threads；仅凭 _thread() 缓存命中不能在后续响应继续
输出正文。安全检查应在读前、以及取得 _send lock 后实际序列化/发送前各做一次。

## 最小原生接口方案（待 reviewer 确认）

1. 在现有 session_access.py 的 MainSessionOwnerLease 增加只读 assert_held()：fd 未关闭、
   fd/path 为同一普通文件、root 与 lock 非 symlink 且身份不变。不得重新 flock/acquire。
   在既有 MainSession root 检查旁增加 bound-source fingerprint/recheck：canonical root、
   Registry/.registry_v1/object-store 根目录与 DB 的 dev/inode、session/core/native task/branch/thread logical identity。
   只复核原绑定；不注册权限、不存新的授权库、不让 cursor 提供 path。
2. Codex 原 _thread 路径增加 owner-history 校验 helper：已初始化的当前连接、_threads 中
   同一 ThreadState、原 session/core/root/source、仍持有原 lease。正文读取与 response
   交付均校验。发送前的校验放在已有 _send 的同一 lock 内；不得在等待 lock 之前检查完
   就视为永久有效。错误只报固定类型，不能回显原始 body/cursor/profile/path。
3. 新增 MainSession.display_history_at(cut, turn_refs) -> 不可变 safe display DTO：
   由 MainThreadRegistry 私有 _committed_turn_documents_at(cut, exact_refs, max_object_bytes=None)
   将 membership 与 hydrate 封装在一起：project_thread_at(cut) 重核完整 lineage，要求 exact
   refs 全部属于 committed turns，去重且最多 100；随后沿 native _read_exact(view=cut.view)
   逐对象校验，max_object_bytes 同时贯通 projection 与 hydrate。该私有接缝仅供 MainSession
   safe renderer 使用，adapter 不接 raw documents。
   这是既有 owner-host 内部读取接缝的 at-cut 扩展，不新增公开 raw-body read API。
   原 _turn_input、_decision_from_document 和 render_main_decision 的安全投影语义复用；
   只返回 ordinal/exact turn identity、原用户 text、安全 assistant text、同 cut annotation。
   不返回/序列化 raw user_input/answer、native_plugins、profile、task.prompt、stages instruction、
   workflow_graph、answer/decision/receipt 的 child body，也不读取任何 child Registry。
   该方法不修改 self.history/_authority_projection，不 refresh/reconcile/activate/persist。
4. annotation：只用 projection.child_links 中 origin_turn_ref 与 exact committed turn 相等
   的已验证 link，并核 task kind，一 turn 多个不一致 link 则 fail closed。生成现有安全
   [launched task_id: kind] 文本，但只代表此 cut 已登记的 launch_registered/registry_attached fact。无 link 不查当前
   TaskControl fallback；设计明确旧 cut 不补未来 launch。当前 live annotation 继续归已有
   live 通知路径，不混入历史页。本轮不虚构 pending/failed launch 事实。
5. 同 cut 的 body 与 refs 校验只使用已绑定 session._main_thread，复用现有 Registry
   canonical/immutable read，不开第二 Registry/cursor DB。公开 DTO 只在完成 safe renderer
   后进入 Codex；adapter 不接 raw JSON。通用 RegistryReadSession catalog/grant 不变。
   不 new/resume MainSession 来重核：其构造会 persist profile/state。正常模型/profile 切换
   不算 Registry 换源；profile 字节不进身份指纹。本轮不改 ObjectStore；其全局 dirfd/
   O_NOFOLLOW 加固单列后续 core 候选，尚未实施。本轮依赖既有可信 local-owner 与
   Registry immutable 假设，路径/lease pre/post recheck 拒绝持续存在的替换与关闭，
   不声称抵御同 OS 权限恶意 ABA namespace/content race 或绝对无 TOCTOU。
6. 默认仍按每个对象 registered size 物理上界，不新增任意 4MiB cap。显式可选
   max_object_bytes 与 response_max_bytes 若配置则严格校验、超预算整请求失败，绝不截断
   或吞正文；响应预算检查在 send lock 内编码后/send前。None 保留现有大合法 body 兼容。
   page<=100/refs<=100 是条数限额，不声称整体 lineage 扫描CPU/RSS有界。

以上接口均是拟议方案；独审通过前不实施公开 body。如 reviewer 要求替代接口，仅改变
owner 原生接缝，不能临时把 refs 权限当正文授权。

## 0.155 RPC 与 cursor 设计

- turns/list：默认 desc、itemsView=summary；items/list：默认 asc，可 turnId filter。
  真实上游 thread_processor.rs:5574–5585 和 3467–3470 使用默认 25，合法 uint32 limit
  clamp 到 1..100（0 -> 1，超 100 -> 100）。bool、负数、float、超 uint32 拒绝。
- turns notLoaded 的 items=[] 且显式 itemsView=notLoaded；summary/full 都由 safe display
  reader 提供 RPNH 已支持的 userMessage/agentMessage 两项；不假装提供 child/tool 私有历史。
  response items/list 是 {turnId,item} entries，不是裸 ThreadItem 数组。
- nextCursor：仅在确有更多 entry 时，从本页最后一项产生 continuation-exclusive anchor。
  backwardsCursor：非空页从本页第一项产生 initial-inclusive 反方向 anchor，空页为 null。
  上游 segment_paging.rs:438–473 与 395–413 已证实 first/inclusive、last/exclusive 及 >=/>/<=/<。
- resume：只 capture 一次 cut，然后构造 turns/items 两个 desc initial-inclusive 起始 cursor。
  不能直接复用普通反方向 backwards token，也不能分别做两个 cursor=null 当前头查询。
  空历史返回同 cut 的 boundary-only 初始 cursor（无 entry anchor）；首次列表返回空/null，
  防止 resume 后新 commit 意外混入其中一个首次列表。初始 turns cursor 的 itemsView
  显式 initial-unbound，允许该 cut 初始 desc/inclusive edge 任一合法 view（官方 resume
  未约束 view，stock0.155 选 notLoaded）；第一页的 next/backwards 绑定实际 view。
  同一初始 token 可重放另选 view，因为它不是 grant。Thread.turns=[]，historyMode 保持
  paginated；此时 resume 仍须完成同 cut 的纯只读头投影，不靠 state.turns 正文。
- transport cursor 是有界版本化 JSON/base64url 位置提示：protocol threadId、native cut
  task/branch/boundary/event/thread exact ref、query、order、itemsView、exact turn filter、
  native ordinal/item-index/inclusive。严格拒绝重复 JSON key、未知字段、异常类型、无效 typed
  IDs、超长/深嵌套、跨源/query/方向/filter/视图。native cut/anchor 在实际 Registry 重验。
  不把 token 当 grant，不存新 cursor store；不声称它是签名或防篡改 credential。重新打开
  同一 owner/source 后，可重验旧 cut；换源/换绑定/关闭 lease 必须失败。
- 常规 cursor=null 是新查询，可捕新 head；resume 提供的初始 tokens 确保两条 hydration
  查询共用 old cut。limit 可跨 continuation 改变，不属于页面身份。summary/full 变换需要
  新查询；不能静默重解释旧 token。
- live/cold ID 必须统一到已验证 native turn ordinal（允许 failed/interrupted 导致间隙）与
  同一 session/source identity，不能用 committed list index。优先保留现有 _ui_id 的
  active-turn/active-user/active-agent 名称，在冷历史也使用真实 ordinal，避免 terminal
  notification 与 cold page 双份。同一 native ordinal 在源内唯一；anchors 仍绑定 exact ref。
- turns/items/read 的历史 helper 不调用 _observe_active_turn、active_turn_snapshot、
  _ensure_active_tracking。前者可经 tracking 异步进入 _finish_turn/reconcile，不能因名字
  叫 observe 就称纯读取。thread/resume 是组合入口：保留既有 live reconnect/tracking
  生命周期，但其同 cut 初始历史 token/body 路径完全独立。测试将 live reconnect与纯历史
  读的副作用分开记录；不把原 live 行为删除，也不声称整个组合 resume 永远零执行状态变化。
- 历史时间戳若没有已验证 native fact，返回 null，不使用当前时钟伪造每页 completedAt。

## 0.161 单独差异（不扩大 pin/验收）

新鲜官方 schema 已下载到 upstream/，保存原始 blob 与 URL。Turns params blob 完全相同。
0.161 ThreadItemsListParams 的 cursor 新增 {type:"item",itemId:string}，要求非空 turnId，
且为 exclusive item anchor；后续仍用 string cursor。0.155 仅 string/null，当前实现应拒绝
这个新版 object，而不是暗中放宽。0.161 item entry 新增 optional startedAtMs/completedAtMs；
resume 新增 optional collaborationMode/disabledPluginIds。其余细节继续用 schema diff 记录。
当前 initialize 与 CLI version check 仍只允许 0.155.0。schema 通过不是 native 0.161 认证。

## 基线与 overlays

独立目录 rpnh-codex-history-projection；所有冻结输入目录保持只读。main 本次读回为
715468dab0b1bea07d7e94a7aa0606eaf194365c。reader 与 codec patch 原 base 都是
ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4；两个 overlay 均独审、均不可假装已合主线。
reader SHA256 f4726d7c1a230c8935b22310bb1d29f7c8e68342ef9b660cabb6430b8a3d4654；
codec SHA256 3be0de65edb5a4f68579f115f5072bacc3e4fe4ffefeba96b226e45cbcab8a8d。
实施前在独立 baseline 合并 715468 main + 这两个 overlay，并记录逐文件 provenance；新 patch
必须相对于组合 baseline，不重新混入 reader 七文件/codec 补丁；若需修改 native helper，
只将对该组合 baseline 的增量列清楚。

## 验证门槛

1. 纯 JSON schema/严格 cursor/方向/limits：两版区分，0.155 功能主验收。
2. 真 Registry fixture：>=3 committed turns 且有 failed/interrupted 间隙、两页以上、两个
   query 共 cut、空 resume 后追加、old/new cut 交错、restart、跨源/query/filter/view 拒绝。
3. Safe body：private sentinels/native_plugins/answer.task prompt/stages/graph 不泄露；原 text
   与 reply 不因私有字段存在而被整体吞掉；protocol_valid 提示；cut 前后 child link 稳定。
4. owner recheck：未 initialized、unknown thread、close 后、lease fd/path/root/DB/source 替换，
   以及等待 send lock 期间关闭/换源均拒绝，且错误无秘密。
5. 读取期间禁 writer/SQL mutation/persist/TaskControl/provider/child open，核 DB/object/profile/
   lease/head/epoch 不变；native AF_UNIX 限制原样披露，不 monkeypatch transport 绕过。
6. stock 0.155 cold resume 原生运行独立授权/环境验证；未跑明确未跑。0.161 另立认证，
   不因离线 fixture 成功改变 pin。无安装、登录、模型 API、Actions、push。


## 实施审查后的明确增量

- 设计经独立审查GO后实施；现有owner客户端权限足够，不创建任何新grant。
- 独审发现owner writable core的SELECT连接会按SQLite生命周期checkpoint，实际一次
  items/list(limit=1)曾开235个rw连接；原失败日志保留。本次改为同源原生
  _RegistryCore(create=False,read_only=True)句柄，构造不mkdir、不_initialize、不acquire_writer；
  MainSession.history_registry复用这个句柄，capture/page/hydrate与source meta重核均mode=ro。
  没有第二数据库或authority。去掉普通页一次不必要的整lineage预投影后，同fixture一次测量
  为178个只读连接、0rw、0.195826秒；仍不声称扫描复杂度已优化为常数。
- readonly首次打开可产生-shm/空-wal bookkeeping；测试setup先结束writer连接并建立
  只读句柄，再观察DB/objects/非空WAL/head/epoch，不宣称全目录绝对零写。
- 新查官方protocol/src/thread_id.rs（blob 2e0f21561b7bdc8f58f330e8a7132a3f70a95257）
  证明stock0.155以Uuid::parse_str读取thread.id；原ses_<hex>虽过schema却不能被解析。
  经主线程批准，仅Codex wire层将既有稳定session身份同一128bits渲染为UUID；其他前端
  stable_frontend_session_id与所有Registry身份保持不变。旧sidecar ID原本忽略，display cache
  可重建；旧ses_请求与cursor明确拒绝而非授权别名。
- frozen组合base仍715468+reader+codec。新增d92ff370两文件仅在latest-main-overlay单独验证；
  随后8dd360仅证据README路径勘误，不触产品，不因它重复运行。
