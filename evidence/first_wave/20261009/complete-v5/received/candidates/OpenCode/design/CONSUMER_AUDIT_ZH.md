# OpenCode 消费边界和真实差异

所有行号对应本包 `upstream/v1.18.35` 与 `rpnh` 中经 Git blob 验证的源码。完整 immutable URL 见 `SOURCE_MATRIX.md` 与 `evidence.json`。没有安装 JavaScript SDK；RPNH 通过自有 Python HTTP/SSE 投影与 stock client 交互。

## 版本和接口矩阵

| 边界 | 当前 RPNH / 旧版本 | 1.18.35 实际消费 | 结论 |
|---|---|---|---|
| 启动探测 | launcher `check_version` 仅接受 1.18.32，先于 owner 创建 | attach CLI 和参数源码相同 | 需要显式测试 profile 才能认证，生产拒绝新版是当前有意行为 |
| Attach 入口 | `opencode attach URL --dir DISPLAY --username rpnh`，resume 加 `--continue` | `attach.ts:92–141` 读取 TuiConfig、ServerAuth、validateSession、legacy plugin host 后运行 TUI | 不是启动上游 OpenCode server；mini/fork 不在 launcher 使用范围 |
| Bridge/启动层 | RPNH 的认证 loopback HTTP/SSE server | `cli/tui/layer.ts` → `@opencode-ai/tui` run；`app.tsx:186–335` 安装 SDKProvider | 3 个 CLI TUI bridge 文件及 2 个 legacy host 文件相同；动态依赖仍须 native 检查 |
| HTTP 请求 | directory 限于隔离 DISPLAY；禁止 workspace 漂移 | `v2/client.ts:20–86` 写入 directory header，GET/HEAD 重写 query；SDK.gen.ts 提供 route/body | 全部 SDK 源码相同；不因升级加宽 location/route allowlist |
| Bootstrap | config/providers、provider、agent、config、path、project/current、command 与现有空能力投影 | `sync.tsx:458–534`、`project.tsx:41–58` | required bootstrap 与 optional catches 逐处区分，不能用任意 404 忽略问题 |
| Model picker | 真实 RPNH profile 映射成 providerID=rpnh + selection；effort 是该 model 的 variant | `dialog-model.tsx`、`context/local.tsx` 读取 provider catalog/variants | 源码相同；必须 native 确认选择往返，不能替换用户 provider/exact model |
| Session create | model `{providerID, id}`，可带 nested variant | prompt/index.tsx:1000–1009 | 与 protocol `_selection(create=True)` 对应 |
| Prompt / command | prompt 是 model object `{providerID, modelID}`；command 是 `rpnh/SELECTION` string | prompt/index.tsx:1083–1118；SDK prompt/command serialization | 不同形状仍存在；不能统一误改为一种形状 |
| History | `_views()` → gateway `snapshot` → Registry-derived committed turns | sync.tsx:602–605 读取 session/get/messages(limit=100)/todo/diff；session route 维护显示 | 不引入第二历史数据库；native 冷恢复/页边界待 G3 |
| Event | envelope `{directory,payload}`，payload 自动加入 `evt_…` id；SSE `id:` 为 frame hash | SDKProvider 订阅 global.event；context/event.ts unwrap payload；sync 更新 session/message/part | 两种 id 不混淆；reconnect 发送稳定 message/part ID 的完整视图，不 replay execution |
| Plugin/tool | RPNH config 与 TUI config 均 plugin=[]，MCP={}；native tool effects 不开放 | hooks 的 37 个源码文件相同，但 attach 仍构建 legacy host | hooks 相同只是外围证据，不代表插件已启用、tool completed 是 Registry success |

### SSE 的真实消费者

`context/sdk.tsx:89–129` 使用 `sdk.global.event({sseMaxRetryAttempts:0})`，由外层循环控制重新连接，延迟 1–30 秒。SDK 的 `serverSentEvents.gen.ts:91–211` 能解析 SSE id/data，并在其内部 retry 时带 Last-Event-ID；不能假定 TUI 外层每次新订阅保留该 cursor。

RPNH HTTP 的 `_stream` 明确不把 Last-Event-ID 当执行恢复游标。每次订阅发当前投影；protocol 的 message/part ID 稳定，事件 payload id 随 epoch/revision 变化。认证要验证客户端 store 的最终去重与显示，不应要求跨进程所有 event id 字节不变。

## 四个 TUI 差异和运行时外围

1. `app.tsx` 的 docs.open、`dialog-retry-action.tsx`、`ui/link.tsx`：从 `open` 换为 `@opencode-ai/core/open.openUrl`。
2. 新 `packages/core/src/open.ts` 只允许 http/https URL。文件、非 HTTP scheme 的 link 将被拒绝，这是具体行为变化，不能只说 helper 改名。RPNH 的 text-only session DTO 无需因此变形；若用户以后要求可点本地文件链接，单独评估，不暗中放开 scheme 或执行 shell。
3. `dialog-status.tsx` 对 file:// plugin 名称使用 `fileURLToPath` 后按 `path.sep` 分隔。原生 Windows 路径显示修复；RPNH 当前禁用用户 plugin，且 launcher 只支持 Linux/WSL2。
4. `packages/opencode/src/session/message-v2.ts` 增加 xAI tool-result 图片 MIME 过滤；包内 xAI/gitlab provider 依赖也升级。RPNH 不通过这个上游模型执行路径，也未开放附件/native ToolPart，因此没有现有 text-only codec 必改证据。
5. `packages/opencode/script/build.ts` 与 release notes 包含 macOS 重新签名/Developer ID 变化。官方 binary、bundler 和依赖行为可能变化，即使 source contract 相同也仍要实际 native gate。

| 环境 | 当前 RPNH 入口支持面 | 本轮结果 |
|---|---|---|
| Linux | launcher 接受 sys.platform.startswith('linux')；须匹配架构/发行版/PTY | 未运行新 binary |
| WSL2 内 Linux Python + Linux binary | 同一 Linux 入口；PTY、HOME 隔离仍要实测 | 可以作为单独认证目标，不把 Linux 结果自动覆盖 WSL2 |
| 原生 Windows Python / opencode.exe | launcher 主动拒绝；session_access 依赖 fcntl，PTY 测试是 POSIX | 不支持，不纳入候选认证 |
| 原生 macOS | launcher 主动拒绝 | 上游签名变化只作外围记录 |

## 原 Registry 和 PetriNet 的复用

不增加 provider adapter、执行 engine、OpenCode DB、权限系统或 recovery owner。

- `OpenCodeProtocol._views` → 原 `FrontendGateway.call('snapshot')` → `RegistryFrontendApplication.snapshot()`。应用 `_turns` 从 MainSession 的 `committed_history` 取权威 answer。OpenCode 回调/插件状态没有提交成功权。
- `submit`、`abort` 与显式 `rpnh-*` 命令继续经原单线程 application boundary；messageID/Idempotency-Key 与 `/rpnh-send` 保留当前 request identity 语义。
- `resume=True` 使用 `inspect_main_session_root`、`MainSessionOwnerLease`、`MainSession.resume(..., task_control=object())` 和 `TaskControl(recover_pending_launches=False)`。不恢复 pending launch，也不以看历史为理由重跑旧 turn。
- `rpnh-net` 调用现有 application `_net` → `project_registry_net` / `_filter_projection`；`test_actual_registry_net_matches_existing_inspection` 已有 equality 对照。沿用资源开关，不造新 PN 模型。
- `FrontendGateway` 会周期性 `app.tick()`，正常 resume 会取得唯一 owner lease。G3 必须选择无待执行工作的已提交 fixture，并记录 tick/lease；不能宣称整个 Application 从未拥有 owner 或目录字节全部不变。
- `/rpnh-tasks` 当前会调用 `reconcile_child_registry_links()`，不是 G3 严格只读 route。不要把它混进零变更断言；只读 PN 用 `/rpnh-net` 并验证对应 Registry 逻辑状态。
