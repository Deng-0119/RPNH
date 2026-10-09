# Codex 0.161 最小增量：独立设计审查

日期：2026-10-08。结论：**最小 adapter 实现可 Go；默认支持与 stock 原生认证不在此结论内。**

## 已核事实

- 原设计 `INPUT_LOCK.json` 的 31 个冻结文件全部与实际字节一致。旧 owner-history 最终补丁确为 `df0c3090e84f532d158dd02d7b65489323563a470843e4bd4e2688ffcc25900b`。
- 原上游 provenance 的 53 项，SHA256、Git blob SHA1 与字节数均一致。本次只证明本地证据一致性，未将此表述成新网络获取或 Rust 执行。
- 原 evidence 缺少 `COMPATIBILITY_MATRIX.json`；另有 `absolute_path.rs`、`app_server_version_notice.rs`、`history_cell_session.rs` 三个上游文件未列 provenance。作者已收到明确补全要求，后续应在新目录补充，不重写旧冻结包。
- `v2_thread.rs:1755–1792` 的 object cursor 是新请求入口；`thread_processor.rs:3432–3478` 要求非空 turnId；`segment_paging.rs:207–294` 按指定 visible turn 查找 itemId 并设置 `include_anchor=false`。设计中的 exact committed turn / safe-slot 归一与其方向一致。
- `history.rs:102–114` 已核 stock 历史 builder 继续发 opaque string。两个 safe slots + 同 turn 限制下，object 的 exclusive 起点最多返回一个 item，nextCursor 必为空；不能造第三个生产 slot 来制造多页证据。
- 原 `codex_history.py` 的 UUID wire thread identity、native ordinal 的 public ID、固定 cut、turn filter 和 v1 string continuation 已有实现；本增量无需改变 Registry 权威、body renderer、cursor storage 或执行能力。

## 进入实现的必要条件

1. 现兼容 manifest 是唯一运行时版本能力表。默认 `pinned-0.155.0` 保持原精确版本行为；0.161 仅经本地显式 `candidate-0.161.0` 参数选择，source-qualified/native-pending 与默认支持分离。
2. binary resolver、launcher 与 server 必须绑定同一 immutable profile；CLI 版本与 initialize 的 codex-tui/name + version 精确匹配。不能因 initialize 自报新版本自动选 candidate，也不能搜索另一份 binary、安装、降级或放宽未知版本。
3. object 严格 type/itemId 与非空 bounded turnId；只解析同 cut 内 committed exact turn 的两个既有 safe slots，构造 native exclusive anchor；拒绝 failed/pending/nonmember/cross-turn 与其他字段。
4. 新 object 查询仅捕一次当前 cut；它不能继承较早 resume cut。现 string continuation 不 recapture head。所有页仍经同源 RO reader、safe renderer、原 send-lock 重验。
5. 新版历史时间只能填真实未知值 null；resume collaborationMode=null、disabledPluginIds=[] 限于现 default-only/empty-discovery 契约。live item 时间戳必需约束不放宽。
6. 新代码基于明确的 frozen history + native reader + effort codec + d92 两文件 overlay；8dd 文档 HEAD 不当成完整新 checkout 的产品认证。
7. 新 native gate 仅为待执行交接：动态 item limits、metadata missing-count 补页，逐版/逐 root/首开重开分 lane 留证。不能以纯 JSON 或 mocked version process 结果记为 stock 通过。

## 本轮验证范围

代码就绪后，独立探针只运行 synthetic Registry + fake JSON transport，不启动 stock Codex、不创建 socket、不安装、不登录、不调用模型、不运行 Actions 或 push。分别记录作者差异测试与独立新增断言，不把重叠数相加成总通过。

后续代码审查和实际结果另行补充；当前不声称代码通过、原生通过或已入主线。
