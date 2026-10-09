# DSH V3/V4 codec 与 detached Session 投影：待独审设计

日期：2026-10-08 UTC。状态：独审已准许A段有限候选；纯codec/fixtures与旧pin接线已实现，新版仍未认证。实际验证与接线限制见 IMPLEMENTATION_STATUS.md。本文不是 pin 放宽或本地运行成功声明。

## 1. 经复核的边界

- RPNH 基底 main：`8dd360e4848912a998dbd83220c3f0ce0a1caa86`，冻结时只读复核仍为该 SHA。`evidence/rpnh-tree.json` 为该 commit 的完整未截断树；`evidence/baseline-manifest.json` 的 24 个 DSH 文件在实施前按 Git blob SHA 核对。当前 source 已含候选补丁，不再与 baseline 全部相同；13 个改动文件见 file-manifest.json。
- 旧 DSH 支持身份：`ddefc45fbc7f8e46dd73185e68295696d1297887` / `0.1.6-alpha.2` / Session V3 / `rpnh/dsh/v1`。保留 UPSTREAM.json、prepare.sh、verify.sh、bridge.REVISION、backend.REVISION 和已注册 executor identity，不改 nativeRegistry。
- 认证候选：官方 master 与最新已发布 prerelease 均为 `5badb15009ae1756c3afe0ae0cef1faafc290ccc` / `0.2.1-alpha.1`；release 发布于 2026-10-03 06:42:19Z。只有源码兼容研究，不是支持版本。
- 官方源保存在 upstream/{pin,latest}；逐文件 SHA 见 evidence/upstream-verified.json。禁止用 README、latest release API 的404、版本大小或缓存包替代源码证据。
- 没有安装、登录、模型/API调用、Actions、push；没有读旧运行目录或 raw-private 资料；没有修改任何冻结交付包。

## 2. 真实消息来源链与显示边界

### 2.1 已有路径

1. `agent.ts:drive()` 从 ReactLoopInbox.claim 获得正式 user messages，连同同一 session header、route、policy、data 交给 backend.turn。
2. `backend.py:_validate_turn_request()` 验 session、请求 identity、旧 revision、selected provider/model、user/context 消息外形；`turn()` 使用现有 MainThreadRegistry.accept_turn/attach_attempt 与既有 Harness/PN。
3. model 的 observation.message 来自 `capabilities.ts:configuredResponse()` 或真实 DSH BlockAssembler；configuredResponse 将 response.reasoning_content 编成 reasoning block，response.text 编成 text，合法单个 tool_call 编成 tool-call。不是临时后台日志。
4. tool observation.message：configured 路径由 backend.py1007 构造，offline 路径由 DSH createToolResultMessage 构造。call.id、source.callId、toolCallId、isError 关联已登记 call。
5. `projection.ts:appendTurn()` 只把 committed record.user_input.messages、model observation.message、tool call 指定字段、tool observation.message，投影为 Session event。它不把整个 user_input/arguments/observation 扩散到 Session。
6. `app.ts:readHistory()` 的显式本地 `--history` 导出调用私有 owner 的 history；backend.history 返回完整 Registry-derived envelope。此行为比 Session transcript 宽，但不是本次发现的远程泄漏，不擅自改变其既有导出契约。

### 2.2 保留与不扩张

- 保留正式消息：用户/合法context内容、assistant text、reasoning、tool-call原始参数、工具结果及错误、消息ID/source/关联/顺序。迁移不是删 reasoning 或把工具结果替换为占位符。
- 保留既有 narrow selection。无损克隆的对象限定为已选消息/头/必要事件字段，绝不 `...record`、`...answer`、`...observation` 进 UI message/event。
- 后台 state 的 policy、data、route config、provider_request/chunks/raw_value、selection/private配置路径不新增为 Session 表面字段。它们的存在本身不说明原CLI导出违约。
- `backend.py:_public_execution_profile()` 已用精确字段集合与 shared selection 比对；现有 `test_configured_external_route_keeps_private_policy_out_of_dsh_registry` 验证endpoint、API key标识与header值不进入DSH Registry。复用这个边界，不创建通用脱敏系统。
- 上游 message.source.replayState 在旧版已是合法 adapter-private metadata；本产品构造器未生成它，backend模型source又精确比较kind/provider/model。不得借新converter从其他raw结构补造它，也不得宣称删它才能保证现有消息安全。
- 不读取任意外部JSONL，不遍历provider配置或子运行来“补齐历史”。无法认证的消息形状明确报codec/unsupported错误，不静默删除合法内容。

## 3. 最小接口设计

先新增依赖无关的 codec 候选与 fixtures，独审后接现有 consumer。两种 dialect 由显式 exact revision选择，禁止根据一条message的形状猜版本或配置 `>=` 范围。候选metadata区分 `supported-old` 与 `source-audited-candidate`；新项不被launcher读取为允许项。

### 3.1 Message codec

建议 `integrations/dsh/src/message-codec.ts` 与 `cpn/dsh/message_codec.py`，共享一组JSON fixture；不新建history数据库、运行时、provider adapter或执行服务。

- `dialectForRevision(revision)`：只认上面两个固定SHA；未知revision错误。识别format不授予运行资格。
- `decodeToolResult(message, revision)`：校验指定dialect，输出短生命周期结构 `{id, source, callId, content, isErrorPresence/isError}`。这是纯转换临时值，不是Registry注册schema。
- `encodeToolResult(value, revision)`：V3输出user + 单一tool-result wrapper；V4输出tool + 顶层toolCallId/isError/content。维持原id、content逐字段、source关联，不能重新生成UUID。
- `projectMessage(message, sourceRevision, targetRevision)`：只转换tool-result布局与经认证的source语法；user/assistant合法消息保持内容。未知/混合布局明确失败。
- backend调用另加“managed observation必须显式boolean isError、exact admitted call ID、匹配observation.content/value”的现有权限校验。上游类型中isError可省略，历史codec保留缺省与false的区别，不能为了强执行校验破坏合法历史。
- `providerMessages()`继续保留既有单pending-call状态机：tool名称来自同一registered declaration；顺序、单调用限制、原始参数字符串不变；V3与V4只在decode阶段不同，得到相同registered_llm/v1 DTO。此函数不需要引入另一provider协议。
- invalid/error路径不得把异常变成assistant成功消息；typed codec错误映射为现有pre-admission/explicit recovery失败，不能消费ticket、调用模型或补跑tool。

### 3.2 Detached Session projection

完整历史仅支持 V3→V3、V3→V4、V4→V4，V4→V3 明确unsupported。当前实现的新版路径为依赖无关projectHistory候选；production open/stat/read仍固定旧pin，不宣称完成V4真实cold-reopen接线。以下open/stat/list统一转换是后续native认证门槛，不能把尚未运行的官方Session等同pure事件列表。

- source版本取被Registry正式记录的user_input.upstream_revision与header.version的配对。缺省、冲突、未知版本不猜测为3/4，不重标execution ticket。
- `projectHeader(header, sourceRevision, targetRevision)`：仅复制已验证SessionHeader，将version从3投影成4；id/createdAt/cwd/isSeeded等保持。既有 managed profile对fork/delegate/preset的拒绝不变。
- `appendTurn(session, record, sourceDialect)`：沿现有消息槽位逐个转换，再append原event种类。每条实际tool/result必须匹配其正式tool/call；重复、错ID、并行按managed限制拒绝。read投影不能把所有缺结果当错误：工具被policy拒绝时，已提交denied/blocked turn合法保留assistant tool-call且无result；只在进入下一次provider请求时要求严格配对。
- `project(history, targetHeader)`：必须先验证全部records并转换为detached列表，才构造Session，避免验证到中途留下部分可见会话。preserve ordinal、消息ID、工具callID、turn/step、原始参数、reasoning/result内容和终态；保留现有deterministic `createdAt + seq` 时间投影，不能声称这就是Registry真实执行时间。
- open/stat/list/handle.read统一使用转换后的detached header与events；不能出现stat回V3 header、read回V4 event的混搭。
- flush继续逐语义event深比较Registry的同一目标dialect投影，仅忽略原有inbox/end-seed缓存事件；不能只比assistant text。append仍拒绝持久authority，保留唯一已读replay cut end-seed例外。
- 转换不写Registry、不生成新revision、不改变nativeRegistry identity；不能把显示header4写回旧user_input。

### 3.3 旧会话读取与恢复

- 旧完成/拒绝会话：允许新版候选纯读detached V4视图；旧runtime仍读V3。内容/ID关联一致。
- 旧active/interrupted会话：新版可读已提交历史及已有metadata，但`resume`/新turn必须在dispatch之前拒绝revision错配；只能由原已支持runtime显式恢复。绝不把旧executor/ticket换新SHA。
- 初始有限实现不支持“在旧V3会话上以新revision继续新turn”。那会使backend.turn直接拼接旧answer.messages与新messages，涉及新执行revision和持久化布局选择，必须另认证。需要新revision时先新建会话；不自动迁移。
- 新V4会话的真实执行只有后续exact-revision native gate通过、单独决定正式启用后才允许。当前pin不动，候选offline测试不得绕过production gates。

## 4. 第三个来源语法差异（需要审阅确认）

官方新migration的 `sources.ts` 会将V3 `{kind:'plugin',plugin:X,...metadata}` 转成producer-owned source；V4 physical codec (`message-sources.ts`)拒绝旧plugin wrapper。

重要区别：RPNH目前自定义SessionPersistence不调用官方physical codec；当前Session.create/append只要求非空kind，故不能把它误报成普通user输入的运行硬失败。常规user/model/tool source不受此变化影响。

但RPNH `_validate_turn_request` 允许user/context source为任意Mapping，`agent.inject()`也存在。若声称覆盖其合法历史，就要纳入official V3 source mapping（保留metadata与内容，未知plugin得到`plugin:X`，不是删消息），或明确该候选只认证user/model/tool sources并对旧plugin context给出unsupported提示。独审建议采用官方纯source映射有限移植并用上游映射fixtures固定，避免今后读出的“V4”仍含已退役source语法。绝不移植整个上游JSONL迁移/relationship数据库。

## 5. 受影响文件和consumer

|文件|有限修改/验证点|
|---|---|
|新增 message-codec.ts / message_codec.py / JSON fixtures|exact-dialect布局、关联校验与detached转换|
|integrations/dsh/src/capabilities.ts|providerMessages tool读取、OfflineAdapter最后结果文本读取、BlockAssembler.message source参数；保留grant/consumed/prepared逻辑|
|cpn/dsh/backend.py|configured worst-case frame/tool-result创建和exact tool observation校验调用codec；旧REVISION常量/registration不变。future新revision执行接线本轮不启用|
|integrations/dsh/src/projection.ts|header/open/stat/list/read、消息转换、全量预验证、flush同dialect深比较|
|integrations/dsh/src/agent.ts|appendTurn调用传版本；必要的write/resume拒绝点，不改生命周期结构|
|integrations/dsh/src/bridge.ts|本轮不换REVISION；如需候选纯函数metadata显式导入，无semver宽放|
|UPSTREAM.json/prepare.sh/verify.sh|本轮不改pin；新增manifest若存在必须是观察证据，不能误入allowlist|
|tests/test_dsh_backend.py 与新增tests|旧exact支持回归、两版合成codec fixtures、before-dispatch拒绝、private config既有负例|
|integration.spec.ts/lifecycle.spec.ts|后续已有准备环境内真实DSH生命周期/重开/flush/单owner认证，不能以pure tests替代|
|双语DSH guide/compatibility文档|只准确区分旧支持/新候选/未运行gate，待交付时同步，不顺便编辑Codex history文档|

与Codex history包无代码依赖：不改cpn/frontend/codex_app_server.py、MainThreadRegistry read cursor、MainSession.display_history、NativeRegistry API。可能的共享冲突仅README/双语总体compatibility表；优先本DSH独立报告避免碰撞。

## 6. Gate（不把未运行写成pass）

G0 源身份：24个RPNH DSH文件、14个官方关键文件Git blob一致；旧/新factory transform唯一锚点、幂等、partial patch错误，纯字符串检查；旧pin准备脚本不变。

G1 独立JSON/codec fixtures（Python + Node内建测试，无安装）：V3/V4同义tool成功/失败、isError缺省/false/true、unicode、空合法tool content、稳定id/source；普通user+assistant reasoning/text+call/result保存；错误role、双重布局、source/callId冲突、重复结果/并行、非法raw_arguments、未知revision拒绝。明确DSH的通用ContentBlock与RPNH的text-only provider子集不同。

G2 detached projection：旧完成/denied→V4，V4→V4，混合错误拒绝；原输入deep冻结/序列化前后相同；header版本/ID不一致拒绝；多turn顺序和消息ID保留；额外record/answer/observation字段不进入Session；不删合法reasoning/tool内容。denied/blocked尾部合法未执行tool-call保留，执行provider DTO缺结果则拒绝。old-active新read仅view，write/resume零dispatch拒绝。mock-read不能冒称真实cold reopen。

G3 既有Python focused回归：test_dsh_backend/test_dsh_distribution/test_dsh_long_path及确实受影响reader测试；旧pin行为不变，provider/tools用确定性double。关注工具错误、帧界限、selected route/private配置不落库、cancel/explicit resume与no-replay。

G4 已准备旧exact DSH checkout：typecheck/native integration+lifecycle验证旧支持未退化；新exact源码类型检查另列。未具备依赖时记未运行，不安装。

G5 新exact DSH原生认证（后续本地）：genuine Cordis/Session/LlmRuntime/AgentRegistry，零输入lifecycle（零model/tool）与fake-model offline execution分开；含complete/denied/error、旧history cold reopen、flush篡改负例、owner隔离、取消/关闭竞态。安装或真实模型调用另授权，不能在本轮自动执行。

只有G1/G2或Python绿不能称最新DSH兼容完成。新版启用需要G4/G5以及单独exact支持manifest决策。

## 7. 建议分段交付

A. 当前设计经独审通过后，只实现依赖无关codec、共享fixtures与projection候选；production pin不动，保留旧行为的focused regression。
B. 第二次独审核对具体补丁/测试，无高风险项后生成本地认证包；明确需准备两个exact upstream checkout但不自动安装/运行。
C. 只有本地官方runtime认证返回后，另做exact支持启用决策与匹配manifest/launcher/backend identity，不把旧active会话改名迁移。

## 官方精确来源

- https://github.com/deepseek-ai/deepseek-harness/releases/tag/dsh-v0.2.1-alpha.1
- https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/llm/llm/src/message.ts
- https://github.com/deepseek-ai/deepseek-harness/blob/ddefc45fbc7f8e46dd73185e68295696d1297887/packages/llm/llm/src/message.ts
- https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/core/session/src/index.ts
- https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/session/session-format-v3-to-v4/src/tool-role.ts
- https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/session/session-format-v3-to-v4/src/sources.ts
- https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/session/session-format-v3-to-v4/src/message-sources.ts
- https://github.com/Deng-0119/RPNH/blob/8dd360e4848912a998dbd83220c3f0ce0a1caa86/integrations/dsh/src/projection.ts
- https://github.com/Deng-0119/RPNH/blob/8dd360e4848912a998dbd83220c3f0ce0a1caa86/cpn/dsh/backend.py
