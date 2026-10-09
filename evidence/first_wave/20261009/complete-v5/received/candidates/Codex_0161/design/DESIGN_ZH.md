# Codex 0.161 最小兼容增量设计

日期：2026-10-08。状态：**设计与官方源码 / JSON 契约核对完成；产品实现、版本门禁修改、stock 原生认证均未做。**

## 1. 结论

不应重新实现历史存储，也不应把旧包简单改成只接受 0.161。下一增量沿已交付的 owner 历史包，复用同一个 MainSession、同源只读 Registry、frozen cut、native page、safe renderer 和 rpnh-history-v1 string cursor。

最小实质变化是：

1. 在未来明确授权的实现阶段，加入精确 0.161 客户端能力配置，同时保留 0.155；CLI 实际版本与 initialize 必须一致，不自动安装、降级或放开任意版本。
2. 只为 0.161 的 thread/items/list 增加 `{type:"item",itemId:...}` 起始定位。它必须绑定非空 turnId，解析到同 cut 中已经 committed 的 exact turn / item slot，转为现有 exclusive native anchor，随后仍输出原 string cursor。
3. 0.161 的历史 entry 时间戳和 resume 新字段无需新事实存储；Rust 可缺省。推荐在 0.161 wire 中显式补真实的空值 `startedAtMs:null`、`completedAtMs:null`、`collaborationMode:null`、`disabledPluginIds:[]`，满足新版生成 TS 对象形状。不得填当前时间伪装历史时间，不宣称实现 collaboration 或 plugins。
4. 原生 gate 复用一轮本地准备与同一隔离合成 Registry，按已存在且逐个核验的 stock 0.155 / 0.161 binary 顺序运行。新 TUI 的初次 item limit 与缺失 turn 补页是动态值，不能沿用“每次必须 100 items / 5 turns”的旧断言。

**不能把 42 个纯 JSON fixture probe 当成产品或 Rust/TUI 通过。** 官方 schema 接受缺 turnId、空 itemId、旧 ses_ thread ID、超 uint32 的部分 JSON，这些实际 consumer / server 不接受，已分别记录。

## 2. 输入冻结与精确依赖

本次只写 `rpnh-codex-0161-delta/`。旧 source、patch、native runner 与版本 pin 保持原样。`INPUT_LOCK.json` 记录 31 项冻结输入；最终复核须与读取前 hash 一致。

本次 GitHub main 读回：`8dd360e4848912a998dbd83220c3f0ce0a1caa86`，父提交 `d92ff3704b6002bf5ecbccb3e6a3d1489809a805`。8dd 是证据 README 路径勘误；产品仍同 d92。来源见 `main-readback.json`。

历史包实际冻结组合并非裸 8dd checkout：

| 层 | 精确身份 | 说明 |
|---|---|---|
| 原组合 main | 715468dab0b1bea07d7e94a7aa0606eaf194365c | 已冻结、验证的 source subset，不是完整仓库 |
| native reader overlay | f4726d7c1a230c8935b22310bb1d29f7c8e68342ef9b660cabb6430b8a3d4654 | native-main-thread-history.patch；原 patch base ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4 |
| effort codec overlay | 3be0de65edb5a4f68579f115f5072bacc3e4fe4ffefeba96b226e45cbcab8a8d | codex-effort-wire-codec.patch；同原 patch base |
| owner history 增量 | df0c3090e84f532d158dd02d7b65489323563a470843e4bd4e2688ffcc25900b | 相对上述组合的 21 文件；已交付最终文档单行修正版 |
| 单独新主线产品 overlay | d92ff3704b6002bf5ecbccb3e6a3d1489809a805 | task_control.py SHA256 b8658b6c19d64eb4bc34a9c320c84a9140cab624e236ffe8c8edfcc7bd026729；registry/run_authority.py SHA256 7475c2d5403dea49dc0a45796a3f84beab6c7516f18466bc8e1338bf0be684e3 |
| 后续文档 HEAD | 8dd360e4848912a998dbd83220c3f0ce0a1caa86 | 不改产品，不为此重跑产品矩阵 |

reader / codec / history 均不能当作已合 main。未来增量补丁必须相对**确认组合后的最终字节**生成，逐文件标明继承哪个 overlay；不要重新打包老补丁为新实现。已有历史补丁 doc-only 前身 c1d5665… 的 128 独审与最终 df0c3090… 的 doc-only 补查分开记录。既有作者 119+43 是两个不重复集合，独审 128 与作者重叠，d92 的 119 是同矩阵复测；本次不重复这些测试，也不新增“产品通过”计数。

R1 / R2 / H2a / DSH session codec 等其他独立包不自动成为此次前置依赖。若落地时 main 又变，先计算实际重叠与新组合，按受影响边界追加验证，不能把旧 subset 验证写成新完整 HEAD 认证。

## 3. 三层契约，避免 schema string 的盲区

详细逐项证据见 `CONSUMER_AUDIT_ZH.md`、`COMPATIBILITY_MATRIX.json`。官方快照与 URL / Git blob / SHA256 在 `upstream/*PROVENANCE.json`；10 个 JSON schema、10 个原 TS fixture 继承旧包已核 blob 的内容，新增 Rust / TS 源码来自官方精确 tag。

### 3.1 已覆盖，可复用

- thread.id 必须是 Rust ThreadId 可解析的 UUID。现 `codex_history.py:25–36` 把既有 ses_ display identity 的同一 128 bits 转 UUID；generic frontend / Registry 身份不改。0.161 仍通过 `ThreadId::from_string`，不可退回 schema-only string。旧 ses_ alias / token 继续拒绝。
- turn.id、entry.turnId 与 item.id 在 TUI 按字符串相等匹配/去重。必须保持既有 `public_id(thread, native ordinal, kind)` 与 live `_ui_id(...,active-*)` 同源；failed ordinal 间隙不能压缩成列表索引。
- resume 双起始 cursor 共一次 cut；turns 初始 desc/inclusive 且 view 初次 unbound；后续 view/filter/order 严格绑定；items 初始 desc/inclusive。空 cut 保持 empty，不能被未来 commit 填入。
- 0.161 history builder 继续发 string opaque cursor。`history.rs:102–114` 用 `cursor.map(ThreadItemsListCursor::Opaque)`；新增 object anchor 是协议新入口，不是普通 cold hydration 的必需请求。
- TurnsListParams 两版 Git blob 完全相同。默认 turns desc / summary、items asc，uint32 limit 默认 25 后 clamp 1..100 与已实现逻辑一致。动态 limit 变化不改变 page identity。
- item/started 与 item/completed 的 **live** 毫秒时间戳两版都是必需 i64；现 `codex_app_server.py:1037–1053` 已发送。不能用 history entry 的 optional 结论放宽通知。
- `cwd` 由 Rust AbsolutePathBuf 消费，不只是任意 string；继续从已 resolve 的 bound state.cwd/root 输出，不接受 cursor 带路径。permission wire label 只用于兼容显示，Registry / RPNH 执行权限不变。

### 3.2 真正需要增量的 wire 入口

官方 0.161 `v2_thread.rs:1755–1792` 定义 string 或 object cursor。`thread_processor.rs:3432–3478` 显式要求 object anchor 的 turnId 非空，再走 creation-order paging；`segment_paging.rs:207–294` 还要求 itemId 非空、存在于请求 visible turn，并设置 include_anchor=false。这些条件未由 JSON schema 全部表达。

现 `_history_page` 总是 `HistoryCursor.decode(token)`，因此 object 被拒绝；不是已覆盖的“形状不同而已”。需要新增严格分支，但不改变 generic Registry 核心。

### 3.3 可缺省字段，准确限定

- 历史 `ThreadItemEntry.startedAtMs/completedAtMs` 在 Rust 是 Option<i64>；本次 TUI merge 按 turn/id/item 合并，不使用这两个 entry 字段。无已验证 producer fact 时可省略或 null；推荐新版响应显式 null。绝不从请求时钟或当前 active duration 推回历史。
- resume `disabledPluginIds` 有 serde(default)，空即 `[]`；`collaborationMode` 是 Option，定义明示兼容旧 server。TUI 确实消费 collaboration_mode，再走默认 model / effort 回退；不能说“字段没人读”。现 empty discovery / 禁用 plugins 的受限前端可保留 None/[]。不根据这个字段添加或恢复尚未实现的协作执行能力。
- JSON schema optional 与生成 TS optional 不是同一件事：新版 `ThreadItemEntry.ts` 和 `ThreadResumeResponse.ts` 的这些属性是必需的 nullable / array 形状。故“Rust 能读缺省”不等于所有 TypeScript 构造器无需改；本次未编译 TS 或 Rust。
- Schema 嵌套增量含 McpAppUi / image fileId / 更开放 CodexErrorInfo、描述调整等。安全历史只发 userMessage(text) / agentMessage(text)、completed / error:null，当前分支不需要新增 MCP、图片读取、backend、child 正文或 error 枚举映射。未来若扩大输出 variant，须另审对应 consumer。
- Rust 还有 experimental 默认字段；公开 non-experimental schema并不穷尽Rust结构。已查看实际默认与消费；不因源码存在 initialTurnsPage / runtimeWorkspaceRoots 等，就声称需新 runtime，或无条件实现所有实验功能。

## 4. Object anchor 归一化设计

拟在现 `CodexAppServer._history_page` 的前段完成，后半段 page / hydrate / serialize 原样复用：

1. 先复核 initialize、原 owner lease、预绑定 ThreadState 与 source；同现 `_history_state`。能力按已经选择并验证的客户端版本决定。
2. cursor=null：沿原新查询路径捕当前 cut。cursor=string：沿现 `HistoryCursor.decode/bind`，**不重新捕 head**。
3. cursor=object：只允许 0.161 的 items query；严格只含 type/itemId 且 type=item、itemId 是有界非空 string、turnId 是有界非空 string。bool / array / number / 多余 root/path/source/cut/order 字段拒绝；turns query 和 0.155 仍拒绝 object。
4. object 是**新查询**，只捕一次当前 cut。它没有旧 cut 信息，不能假装继承较早 resume 的 cut，也不能从 UUID 逆推旧 cut。若调用者要维持旧 cut，应使用已经收到的 string cursor。
5. 用同一 `history_registry.project_thread_at(cut)` 做 refs-only 查询，在该 cut committed turns 中按公开稳定 turn ID 找到唯一 exact VersionRef；只在该 turn 的现有 0/1 slots 中计算 user / agent public_id 并精确匹配 itemId。不能仅 UUID 格式合法就接受、跨 turn 搜索、按任意 ordinal 构造 anchor 或查询当前 TaskControl。
6. 构造现 `MainThreadHistoryAnchor(cut, query="items", order, turn_filter=exact_ref, turn_ref=exact_ref, turn_ordinal, item_index, inclusive=False)`，进入原 `page_items_at`。native seam再次验证 cut/source/membership/position。原 safe renderer 与 send-lock 内 source recheck 保留。
7. 输出原 `{data,nextCursor,backwardsCursor}`；非 null cursor 始终是既有 v1 string。next 为 last/exclusive，backwards 为 first/inclusive 的反方向。不得把输入 object 原样回传成 continuation。

**不新增：** Registry、持久 cursor 表、runtime、权限 token、grant、writer epoch、缓存事实、目录遍历或 raw-body reader。一个短 parser / resolver 属于协议适配；原字段过滤后复用 refs projection即可，不为此再建立 core reader。

新入口只覆盖已支持的 committed 安全文本历史。未 committed / pending / active 的 item、原生 tool/child item 和不存在的 turn 继续明确拒绝；不把官方较广 visible-item 能力偷换为已完整实现。stock 当前 cold hydration 不依赖 object anchor，不因此受阻；文档继续标识受限兼容。

### 两个安全 slots 的重要测试限制

当前每个 committed turn 固定两个 safe items。exclusive object anchor 后，同 turn 最多剩一个 item，所以合法 object 请求的 nextCursor 实际总为空。不能为了“证明续页”伪造第三个生产 slot 或把跨 turn item混进来。验证应覆盖：

- user anchor + asc -> agent；agent anchor + desc -> user；相反方向 -> empty；limit 1、默认、clamp边界一致。
- 非空页有原 string backwards cursor，反向 inclusive请求仍绑定相同 cut / turn filter；空页两个 cursor均null。
- 原 thread-wide / turn-filtered string continuation、resume双cursor与restart另外保留验证。
- 未来新增第三slot才有 object起点后的 next分页自然样例；本设计不为测试扩大正文面。

## 5. 版本方案：不丢旧版，不偷偷换 binary

本轮不改当前 `CODEX_FRONTEND_VERSION="0.155.0"`、manifest、launcher或 initialize。

未来实现建议以现兼容 manifest为唯一版本能力表，保留原0.155默认常量/行为用于已有调用方，新增精确0.161 profile。不要用 `>=0.155` 或对外宣称任意新版都可用。

- `resolve_codex_binary` 仍只检查用户明确路径、RPNH_CODEX_BIN或现PATH入口；读取同一个 resolved binary的 `--version`。未来返回/关联已知精确profile；不试另一份binary、不下载、不修改全局安装、不调用包管理器。
- 单次server启动绑定这个 immutable profile。initialize必须恰好报告同一版本及 codex-tui名称；不要接受一个版本启动但另一版本握手，也不要用未验证的client字段绕能力门禁。一个server只服务本次选定的版本，顺序运行两版无需第二owner/Runtime。
- 双版本共享同一 v1 string cursor编码。因为内部source/cut/anchor含义不变，不需要把cursor bump成v2或写版本到Registry；旧v1在同source/owner重新打开后照常重验。0.155不接受新版object形状。
- `_thread_document` 的cliVersion当前是0.155兼容展示字段。未来需由明确wire profile统一生成，避免0.161 initialize与响应还误报0.155；不更改Registry事实或稳定ID。是否原生TUI实际展示的证据单列，不能把该字符串当认证。
- 版本状态按 source/schema qualified、native pending、native passed分别记录。只有真正本地stock证据满足后才能把0.161标为支持；source完成不提前更换产品pin或移除旧版本。

## 6. 未来最小代码和测试范围

预计生产变更限在既有 `cpn/frontend/codex_app_server.py`、必要的小 `codex_history.py` parser helper及兼容manifest；不改 Registry / MainSession renderer / owner访问边界。如果实施者发现必须改core，先报告原因与具体契约，不扩展成新历史系统。

差异测试只针对新增边界：

1. exact版本分支与CLI/initialize一致、已发0.155入口/旧caller兼容；未知版本明确失败，无自动binary替换。
2. 上述object四个方向/边界、缺失/空/wrong type/额外键、跨thread/turn/source、failed/pending、伪UUID、非member全部fail closed；错误不回显body/cursor/path。
3. object只捕一次cut；append发生于捕cut之后不影响本次page；string继续沿旧cut；重开后同source重验。timestamp无nativefact仍null。
4. 新入口仍以同source RO handle、纯historypage、same renderer和send-lock重核；禁止新增writer/TaskControl/provider/childread。只对新增分支做定向探针，沿用旧包已验证事实，不因8dd文档变化重跑旧矩阵。
5. raw JSON -> Rust consumer字段清单核对：UUID、AbsolutePathBuf、Option/serde(default)、enum、必需live timestamp、turn/item一致性、string cursor推进。JSON完整通过仍不得代替Rust/stock。

## 7. 一轮本地准备复用新旧 stock gate

这是一份后续计划，不是现在执行，也不承诺用户装了任何版本。

### 准入与一次性准备

- 本地先只读记录用户授权环境、Git HEAD/dirty、每层patch/hash、Python依赖、AF_UNIX/交互PTY条件；保留用户修改。新实现合成字节与已交付旧包的关系必须可追溯。
- 接受显式 `--codex-0155 PATH` 与 `--codex-0161 PATH` 两个候选，分别执行该路径 `--version`，记realpath/hash/完整版本文本。缺一份就把该lane记BLOCKED，其余可继续；不安装/自动降级、不假定PATH就是新版。
- 新增gate orchestrator应作为新的测试交接文件，使用新实现manifest；**不改已交付旧runner的21文件hash锁，也不临时monkeypatch版本门禁让0.161硬闯旧产品。** 重用原fixture内容、无正文logging/执行拒绝思路和真正transport。
- 同一个合成cold root建立60 committed turns / 120 safe items，另一个pending root建立6 committed + 1未执行pending。正文/私有sentinel仍是合成材料。准备只做一次；两版顺序重开同一canonical root，不能靠复制到新路径后要求UUID一样，因为既有display identity绑定canonical path。

### 顺序验证与复用

两版均在同一已实现兼容版本的source snapshot上运行，各自真binary/真Unix socket/真TUI；不同时占有同一owner lease。可在同一轮本地工作中完成每版cold首次/重开、每版pending首次/重开，日志分lane保留。旧history包自身的独立0.155 gate若已在**相同字节**上完成可复用；若新增兼容逻辑改变相关字节，至少做受影响旧版短回归，不能复用未覆盖新字节的绿灯。

- cold请求链必须真实出现resume -> turns/list(desc,notLoaded) -> items/list(desc)，每个cursor绑定同cut且推进；UI能滚回第001条，最终60 turns/120items，无遗漏/重复、private sentinel不见、原text不吞。
- 0.155初turn5、item通常100；0.161初turn仍5，但初item受terminal height×3行预算约束、后续补turn按missing count。验收记录真实limit且≤100，不强制每页相同size或初包100，不把受viewport正常停止当后端截断。
- 同root跨版本UUID/turn/item ID应相同；同一版本重开也稳定。可在只读RPC probe中重验另一版发出的v1 token，必须同source/cut且无capability/授权扩张；0.155接object应拒绝，0.161接object按第4节核验。
- **stock TUI正常分页不产生object anchor。** 对object的原生transport检查须单列为受限JSON-RPC probe，标明它不是stock TUI发出的请求；不能靠改stock源码让它发object而称stock通过。
- pending保持相同ordinal和active/pending状态，不混入committed body，不执行turn/start、child或模型。新gate保留只读RPC allowlist；新revert或其他写RPC不因存在于客户端源码而获实现/执行许可。

### 原生认证边界与停止

必须分开记录：pure JSON、Rust源码审核、0.155 stock cold/paging、0.161 stock cold/paging、各版pending reconnect、额外原生RPC object probe。仍无Rust编译/运行、真实running-worker、真实provider或整体TUI所有命令认证。本地遇到缺binary/版本不符/登录请求/transport被拒/意外权限或模型需求，保留原错误并停对应lane，不绕过。

日志只含版本、patch/代码hash、请求方法、limit/view、public IDs、cut ordinal/event、counts与错误码，不含正文、完整cursor、private profile或凭据。真实UI观察与无正文日志要同时留证；只见空页面或缓存不能记通过。

沿用旧包SQLite边界：RO可能产生SHM/空WAL协调文件；Authority DB、objects、非空WAL/head/epoch观察窗口与writer关闭checkpoint要分清。不用immutable=1掩盖append，不在history handler里GC/flush/checkpoint；不承诺抵抗同OS owner恶意ABA竞态。

## 8. 本次确实执行与未执行

- 读回官方main身份，核旧包设计/最终source/patch/独审及overlay记录。
- 逐blob保存官方0.161 Rust/TUI与必要0.155对照；schema结构diff完整列为34处变更记录（嵌套数组差异在同一节点内保留完整before/after）。
- `python probes/check_contracts.py`：42个纯合成JSON断言全部通过；含刻意接受却应被runtime拒绝的盲区说明。这不是新产品实现的测试。
- **产品实现0、pin修改0、当前包产品重测0、Codex启动0、安装0、登录0、模型调用0、Actions0、push0。**

下一批准点：先审本设计与consumer矩阵；获准后才做上述adapter最小实现与差异验证，最后在一次明确环境/二进制清单确认的本地gate中完成新旧stock认证。
