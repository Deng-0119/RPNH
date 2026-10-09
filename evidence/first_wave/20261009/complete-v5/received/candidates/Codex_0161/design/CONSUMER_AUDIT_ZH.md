# Codex 0.161.0 Rust / stock TUI 消费者审计

## 结论

本报告是**源码消费契约审计**，不是 0.161 原生客户端运行通过证明。没有安装、登录、运行 Codex、调用模型、运行当前产品测试、推送代码或触发 Actions；没有修改冻结的 history 产品目录。

1. 0.161 的 stock cold-resume / 滚动历史链仍使用字符串 opaque item cursor。新 `{type:"item",itemId:...}` 是 API 扩展，不能把它误写成 stock TUI 冷恢复的必发请求，也不能据此省略完整 0.161 API 声明所需的 object 入口。
2. 线程 ID 的真实 TUI 消费要求 UUID 可解析；schema 的 `string` 并不足够。UUIDv7 是官方生成习惯，不是这里的版本校验。turn/item ID 在所审链为字符串，并以字符串关联、去重；没有强制 UUID 转换。
3. 新 history entry 时间戳缺失可被 Rust 读成 `None`，本链合并时只取 `turn_id` 和 `item`，不会据它排序。此兼容结论只覆盖该消费链，不代表完整 TypeScript/API 输出形状，也不能推广到 live 通知。
4. 新 resume `collaborationMode` 确实有新增消费：0.161 把它送进 session 并恢复 UI/后续提交模式。缺失走兼容旧服务端的默认分支，不是字段“无人使用”。当前 RPNH 的 default-only 范围可保留该降级语义，但不能声称恢复非默认模式等价；适配设计宜明确输出策略并独立验收。
5. resume `disabledPluginIds` 在 Rust 类型中显式 `serde(default)`；已审 resume session 映射没有读取它。RPNH 当前空插件目录范围允许缺失退为空数组，但应把该事实与一般插件禁用状态保真分开。
6. live `item/started.startedAtMs` 与 `item/completed.completedAtMs` 是必需整数，0.155 已如此，0.161 未新增；当前冻结产品已经发出，不能报成这次新增缺陷。
7. 0.161 初次页尺寸更依赖 viewport，补 turn 元数据页也不再总是 5。旧 gate 的“5-turn 页 / 100-item 页”应改成初始 turn=5、item 请求上限100、实际分页顺序与最终覆盖，不应要求每页恰为100。

## 1. 证据来源与读取范围

- 新抓取均来自 `openai/codex` 的固定官方 tag `rust-v0.161.0` 或对照 tag `rust-v0.155.0`，使用 GitHub connector `fetch_file`。
- `upstream/YOUR_PROVENANCE.json` 列出本审计取得/复制的24份文件：官方路径、tag URL、GitHub blob SHA、原始字节 SHA256、字节数、严格 Git blob 验证结果。校验算法为 `SHA1("blob " + decimal(byte_count) + NUL + raw_bytes)`；全部与 connector 的 blob SHA 一致。
- 其中 0.155 `history.rs` 和 `thread_id.rs` 从冻结证据目录只读复制，仍对原 manifest 和 blob SHA 验证。未改原文件。
- `v2_thread.rs`、`v2_turn.rs`、`v2_item.rs`、`v2_notification.rs` 是同批 server-side 审计提供并核验的0.161证据；本审计只读引用。其来源见同目录 server provenance。
- 下文简称 `161/<文件>` 为 `upstream/0.161.0/<文件>`，`155/<文件>` 为 `upstream/0.155.0/<文件>`。文件重命名和官方路径的完整对应在 provenance：例如 `rollout_history.rs` 对应 `codex-rs/tui/src/app_server_session/rollout_history.rs`，`chatwidget_protocol.rs` 对应 `codex-rs/tui/src/chatwidget/protocol.rs`。
- 本报告的“当前产品”特指只读检查的 `rpnh-codex-history-projection/source/cpn/frontend/codex_app_server.py` 冻结副本，不把之后其他分支或线上 main 自动视为相同。

主证据官方链接：[history.rs](https://github.com/openai/codex/blob/rust-v0.161.0/codex-rs/tui/src/app_server_session/history.rs)、[rollout_history.rs](https://github.com/openai/codex/blob/rust-v0.161.0/codex-rs/tui/src/app_server_session/rollout_history.rs)、[app_server_session.rs](https://github.com/openai/codex/blob/rust-v0.161.0/codex-rs/tui/src/app_server_session.rs)、[thread_id.rs](https://github.com/openai/codex/blob/rust-v0.161.0/codex-rs/protocol/src/thread_id.rs)、[session_flow.rs](https://github.com/openai/codex/blob/rust-v0.161.0/codex-rs/tui/src/chatwidget/session_flow.rs)、[protocol.rs](https://github.com/openai/codex/blob/rust-v0.161.0/codex-rs/tui/src/chatwidget/protocol.rs)。

## 2. thread/resume → paginated hydration → turns/items/list

### 2.1 真实请求及反序列化入口

- `161/rollout_history.rs:150-214` 构造请求，在支持分页且未确认 legacy 的条件下设置 `exclude_turns=true`，调用 typed `ClientRequest::ThreadResume`。
- `161/app_server_session.rs:2120-2178` 的请求 builder 未设置 `initial_turns_page`，余项来自 `ThreadResumeParams::default()`。这条正常链不要求返回首批 `initialTurnsPage` 来替代额外分页请求。
- `161/rollout_history.rs:216-237` 把响应解成 `ThreadResumeResponse`。只有特定 unsupported 错误才重试 `exclude_turns=false`；不是对任意错误宽松降级。检测细节在 `161/app_server_session.rs:204-230`，包括 method-not-found，或 invalid request/params 且错误内容匹配 historyMode/excludeTurns/两个分页方法等。
- `161/rollout_history.rs:238-246` 无条件调用 hydration，传入响应里的 `turns_backwards_cursor` 和 `items_backwards_cursor`。`initialTurnsPage` 未在这里消费。
- `161/rollout_history.rs:250-256` 在 hydration 成功后才把 response 映射到 session。`161/app_server_session.rs:2276-2294` 取 `response.thread.turns` 交给 started-thread；不能仅返回 shape 正确的空 turns 并期望客户端自行绕过分页错误。

### 2.2 hydration 行为

- `161/history.rs:330-340` 先验证 `thread.id`。仅当 `history_mode==Legacy` 且 turns 为空时，使用 `thread/read(includeTurns:true)`；paginated 不走此无界读取。
- `161/history.rs:342-350` 首次调用 `thread/turns/list`：`limit=5`，`sortDirection=desc`，`itemsView=notLoaded`（builder在243-263），把结果逆序转为内存时间顺序。**响应 thread.turns 会被这批 metadata 覆盖**。
- `161/history.rs:357-365` 调 `thread/items/list`，`turnId=None` 跨线程 turn 读取，cursor 来自 resume 的 item cut 或上一页 `nextCursor`，方向 desc。
- `161/history.rs:272-314` 合并每个 entry：先按 `entry.turn_id` 找 turn；找不到时沿 turn cursor 再取 metadata；找到后以 `item.id()` 去重，把 item 插到 turn.items 的头部并标记 Summary，返回渲染 items 时再反转。
- **ID / cut 对齐是实际语义要求**：若 entry 的 turn 在已取 metadata 和后续 turn pages 中均不存在，代码最终不会插入该 item，且未必报错。服务端须让两个 cursor 指向一致视图；不能用“两个响应都可反序列化”代替关联正确。
- `161/history.rs:117-126,272-275,295-298` 用已见 cursor 集合抑制重复 continuation；`366-368` 空 item 页会清空 next cursor并结束；`397-403` cursor 无后续或预算耗尽时退出并保留分页状态。服务端的重复 cursor / 提前空页可能造成无报错但截断。
- `161/app_history_pagination.rs:21-58` 的滚动/overlay加载复用同一个 `thread_items_page_params`；请求 `turnId=None`、字符串 continuation、limit=100。`62-100` 先检查请求仍pending及线程仍活动，再应用响应。

### 2.3 与0.155的变化

- `155/rollout_history.rs:119-185` 已有 excludeTurns → resume typed response → 两个 backwards cursor → hydration 链。
- `155/history.rs:198-226` 已有 UUID校验、legacy/分页分流、初次turn=5和反序；`154-191` 已按turn关联和item去重。不要把这些共同约束算作0.161新需求。
- 0.161 的新点在预算与动态 metadata limit，详见第7节；object cursor类型虽然变化，普通请求builder仍是字符串分支。

## 3. ID：字符串 schema 与真实消费者不能混同

### thread IDs

- `161/thread_id.rs:11-17,27-44` 内部保存 `Uuid`，`new()`生成v7，但 `from_string()`只调用 `Uuid::parse_str`，没有 `get_version()==7` 限制；`91-99` serde读入同样解析UUID。
- 0.155和0.161的 `thread_id.rs` **blob完全相同**：`2e0f21561b7bdc8f58f330e8a7132a3f70a95257`。
- 实际硬失败点：`161/history.rs:330-331`、`161/app_server_session.rs:2479-2485`。后者同时验证可选 `forked_from_id`。
- 通知路由也验证：`161/app_app_server_event_targets.rs:48-188,227-232` 从对应通知提取thread ID并解析；`161/app_app_server_events.rs:485-490` 对无效ID记录warn并丢弃。即便JSON schema通过，任意 `thread-1` 也不等价于可用的stock TUI线程。
- 这些证据允许保留既有有效UUID（包括合法v5），不能要求无依据迁移成v7，也不能把原生Registry内部标识直接当wire thread ID。

### turn / item IDs

- `161/v2_thread_data.rs:386-405` 的 `Turn.id` 是 `String`；注释称官方生成v7，但没有ThreadId类型或自定义UUID反序列化。
- `161/v2_item.rs:239-262` 的UserMessage/AgentMessage `id`是String；`451-472` 的 `ThreadItem::id()`只返回字符串引用。
- 真实consumer在 `161/history.rs:277-310` 做字符串相等、HashSet、去重；`161/chatwidget_protocol.rs:89-128,430-457,541-573` 把turn/item字符串用于生命周期、realtime归属和去重，无UUID转换。
- 因而本审查不能推出“所有turn/item ID都必须UUID”；但要求非碰撞、跨页/跨响应稳定，entry.turnId严格等于对应Turn.id，live通知各阶段保持同一turnId/itemId。object anchor的非空/成员归属限制属于服务端语义，应另按server审计执行。

## 4. 新 object item cursor 是否被 stock TUI 发出

- 类型定义 `161/v2_thread.rs:1755-1792` 为 untagged `Opaque(String)` 或 `Anchor(Item { item_id })`；object anchor文档要求带非空turnId，并声明排除anchor。
- **正常初始/滚动历史链只发Opaque**：`161/history.rs:102-114` 参数入口仍为 `Option<String>`，唯一变换是 `cursor.map(ThreadItemsListCursor::Opaque)`；内部状态 `131-134`、begin-older `180-187` 都保存字符串。
- `161/app_history_pagination.rs:42-49` 复用该builder，没有构造object；`161/dynamic_tools.rs:879-913` 的另一条TUI内置任务快照读取，在指定turn下使用 `cursor=None`、limit20，也没有object。
- default-branch code search只用于发现潜在文件；搜索命中的TUI真实请求路径已回到tag逐文件核验。搜索没有找到TUI Anchor构造不能当成对整个tag的形式化穷尽证明，因此准确结论是“已核验的stock resume/scroll/任务快照消费链不发object”，不是“所有未来客户端都不会发”。
- 设计结果：无需为恢复既有stock路径强改其字符串cursor；若新增object入口，应做native anchor适配并最终继续返回opaque string，不能让object格式泄露成下一页响应cursor。

## 5. 三类新增字段的实际默认路径

### 5.1 ThreadItemEntry startedAtMs / completedAtMs

- `161/v2_thread.rs:1797-1806` 为两个 `Option<i64>`，单位毫秒；没有自定义deserialize逻辑。serde的Option缺失分支可读为None。
- `161/history.rs:277-314` 只读entry.turn_id和entry.item，并未把时间戳复制到Turn或渲染输入；正常历史顺序仍来自服务端返回顺序/cursor，不由这些时间排序。
- 旁证：`161/dynamic_tools.rs:906-911` 自己构造legacy fallback entry时明确使用两个None；因此“历史未记录时间”的场景被stock代码实际容忍。
- 当前产品 `codex_app_server.py:731-733` 历史entry仅输出turnId/item，故上述Rust消费链不因缺失这两个字段失败。但TS响应字段声明、schema完整输出及其他消费者是另一个验收维度；适配可以显式输出null，不能把未知时间编成当前时间或从别的时钟猜出。
- **不得延伸为live通知可以省略时间戳**，见第6节。

### 5.2 resume collaborationMode

- `161/v2_thread.rs:465-467` 是 `Option<CollaborationMode>`，注释明确“older server缺失”。
- 真正新增的消费在 `161/app_server_session.rs:2390`：response.collaboration_mode → session.collaboration_mode；0.155对照 `155/app_server_session.rs:2318-2358,2471` 未从resume恢复此字段，通用session初始值为None。
- `161/chatwidget_session_flow.rs:160-183` 先更新model/effort，再按该值分流：Some调用set_effective_collaboration_mode；None初始化默认mask并更新effort。该UI分流在 `155/chatwidget_session_flow.rs:97-119` 早已存在，0.161新增的是resume把值接入。
- `161/chatwidget_settings.rs:405-421` 明确默认mask与默认ModeKind；`563-579` 的Some分支实际设置mode/model/reasoningEffort/developerInstructions。`161/app_server_session.rs:1333-1377` 后续turn/start接受并传递collaboration_mode。
- 官方源内回归例（只读，未运行）：`161/app_server_session.rs:4114-4171` 构造Plan恢复，并断言第一次提交保留该mode。足以证明忽略字段会丢掉真实状态，不能以optional一笔带过。
- 当前产品 `codex_app_server.py:1409-1422` 未返回该字段；`:1210-1215` 明确只支持default collaboration mode，`:1376` 的collaborationMode/list为无可选模式的边界。结论：在严格default-only范围可沿兼容旧服务端分支工作；不可据此承诺Plan恢复或保存的developerInstructions恢复。设计应选定显式default对象、null/兼容缺失中的一种策略，并确保model/effort不被歧义覆盖；任何扩大模式范围须另设计。

### 5.3 resume disabledPluginIds

- `161/v2_thread.rs:441-443` 为 `#[serde(default)] Vec<String>`，缺失变为空数组；文档称保存的禁用列表“does not yet filter plugin capabilities”。
- `161/app_server_session.rs:2276-2294,2359-2392` 的完整resume→session映射没有读它，`161/session_state.rs:28-57` 的session状态也无该字段。已审常规turn/start builder在 `161/app_server_session.rs:1355` 设置disabled_plugin_ids=None。
- 当前产品插件目录为空（`codex_app_server.py:1376-1382`），故本范围missing→[]不会丢掉当前存在的非空插件禁用状态。不能把这一结论泛化到第三方客户端、未来真实插件或用户已有禁用列表。完整协议输出可显式[]，不应编造非空列表。

## 6. Live通知与历史分页严格分开

### 6.1 不变的wire硬要求

- `161/v2_item.rs:1337-1343` 的ItemStarted为item/threadId/turnId/startedAtMs，其中时间为无default的i64；`:1415-1421` 的ItemCompleted对应completedAtMs也是必需i64。
- 对照 `155/v2_item.rs:1322-1328,1400-1406` 完全同义：**不是0.161新增字段**。
- `161/v2_item.rs:1437-1442` 的AgentMessageDelta需要threadId/turnId/itemId/delta；`161/v2_turn.rs:532-535,549-552` 的turn started/completed需要threadId和完整Turn。
- `161/v2_thread_data.rs:386-405` Turn包含id、items、status，itemsView缺失默认Full，error和时间为Option；turn时间单位秒，duration毫秒。不得把turn.startedAt秒误填进item.startedAtMs。

### 6.2 真实消费

- `161/chatwidget_protocol.rs:89-116` 以turn started/completed和item started/completed驱动生命周期；`:118-127` 使用turn/item关联并消费delta；`:428-524` 按Completed/Interrupted/Failed/InProgress分支处理turn完成。
- `:430-457` 在completed turn内检查最后agent item及ID去重；`:631-670` 消费item完成内容；payload必须与live ID/最终items一致，不能只发一条“已完成”文本。
- `:576-589` 的ContextCompaction在ThreadSnapshot replay下用startedAtMs计算耗时，故连“某种item未直接使用该时间戳”也不能当成全体可省略依据。
- `155/chatwidget_protocol.rs` 与161对照显示普通agent delta/turn完成主链仍在；新增realtime锚定、async questions、DynamicToolCall start等行为位于161源中，但RPNH纯文本default范围不因此获得或必须伪装这些功能。

### 6.3 当前冻结产品检查结果

- `codex_app_server.py:1011-1022` 构造一致的started/complete agent item与turn；`:1037-1057` 已发required item时间戳、AgentMessageDelta IDs/content、turn完成对象。
- `:1059-1062` 发线程idle状态；`:108-123` 的turn文档给出秒级startedAt/completedAt和durationMs。
- 结论限于**静态形状及既有实现存在**。没有在本次触发模型/进程去验证通知时序，也没有假称0.161客户端实际接收。应保留离线wire/生命周期回归验收，再在明确授权的native阶段验证；无须为这两个已有时间字段新增产品逻辑。

## 7. 0.161分页预算与native gate修订建议

- 两版常量仍为首turn5、每item请求最大100、扫描基准400：`155/history.rs:26-28`、`161/history.rs:30-32`。
- 新增owned transcript初次预算：`161/history.rs:54-65` 在Initial+owned模式设置rows=max(height,1)×3、items=400，即便无限scrollback也如此；一般/Complete/ThroughTurn预算在`:67-78`。
- `161/history.rs:81-98` 首个item请求可受剩余行数限制；后续只按剩余item预算/100取limit（不再让已扫描隐藏项持续把请求缩成极小页）。因此普通24行终端的owned初包可能请求72，而不是100；实际渲染高度也会让初次hydration停止早于全部历史。
- metadata补取在`:277-303` 用本item页尚缺turn数量、上限100决定limit；0.155 `history.rs:133-148,168-180` 每次补取固定5。0.161补取不要求每个turn页等于5，也不能将“服务端收到limit>5”判错。
- 原冻结gate文档 `native-gate/NATIVE_GATE_ZH.md:39` 说60个turn/120个文本items足够跨stock5-turn/100-item页。fixture规模仍能跨最大100的item页；但需要用户继续向旧历史滚动，或日志证明启动确实已读完，不能只凭初次返回未满100判断失败。
- 推荐新gate证据：固定记录客户端版本、transcript模式、终端宽高；确认cold resume后先有desc/notLoaded turn request(limit5)，记录每次请求实际limit；响应count≤request.limit且item request limit≤100；记录相同cut、continuation推进、ID关联无漏重；滚到最旧目标并校验完整120个合成items。若初次未读完，明确仍有nextCursor并继续人工滚动；不强求每页100或所有metadata补页5。
- request_older明确limit100（`161/app_history_pagination.rs:42-49`），可单独验证“滚动请求上限常量仍100”；这和“cold resume第一包必100”是两个不同断言。
- `161/history.rs:139-172` 的revert_thread是独立写操作入口；正常 `rollout_history.rs:238-246`→`history.rs:321-404` 的调用链不触发它。新增ThroughTurn scope也只多了一种读取停止条件。此次history适配不应为“消除unsupported”擅自实现revert/fork/设置/执行RPC；保留只读gate写操作拒绝边界。

## 8. 启动调用差异：可以确认与仍不能确认的边界

- 已直接对照的 `AppServerSession::bootstrap` 核心RPC集合无新增：0.161 `app_server_session.rs:595-649` 为account/read之后并行model/list、configRequirements/read、collaborationMode/list；0.155相应实现`:566-620`同链。0.161模型为空的错误文案变化，不是新增RPC。
- 0.161 session构造可见remote workspace roots省略、本地/显式provider选择、daybreak状态和权限投影调整；例如`:351-360,784-791,2162-2183,2359-2392`。默认cold history resume并未在这些位置发revert或新执行调用。
- 这不是整个TUI启动RPC穷尽审计：外围startup、配置读取、插件/技能、平台专用路径、Windows沙箱、客户端初始化协议、feature flags、版本warn等仍可能影响native启动。本报告没有把“bootstrap这四种RPC不变”扩大为“0.161全启动已通过”。
- 旧gate `run_native_gate.py:37-40,62-64,88-93` 的读取allowlist与TaskControl.start禁止边界是应保留的安全条件。新native gate如果出现未知方法，应记录方法和调用条件后分类，不自动扩大allowlist，更不伪实现有副作用的方法。

## 9. 留待后续明确授权的验证

1. 用现有离线数据设计/运行0.161 typed反序列化和consumer链fixture（本次未运行），覆盖optional字段missing/null/具体值，而非只用JSON schema validator。
2. 真实已安装stock0.161客户端的native resume/scroll/cold restart；记录client版本、viewport、transcript模式与body-free RPC证据。本次没有此运行结论。
3. 需要主张完整API兼容时，对object anchor入口、方向反转、空页、未知/跨turn/非法item ID、稳定cut、旧string cursor继续有效分别验证；不能用stock不主动发object代替API契约。
4. 如未来支持非default collaboration mode、真实plugin disabled状态、非纯文本items或实时工具流，扩充相应语义验证；当前缺失兼容结论严格限于本文已有产品范围。
5. 本次下载是消费链定向取证，不是整仓clone；未穷尽所有TUI feature branches。所有已下结论均在本文具体路径/行号给出，未核验内容保留缺口，不作全量认证。
