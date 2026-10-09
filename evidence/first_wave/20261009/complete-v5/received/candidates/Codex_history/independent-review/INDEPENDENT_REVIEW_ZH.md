# Codex owner 固定 cut 历史投影：独立代码与离线安全复核

日期：2026-10-08。结论：**冻结增量通过独立离线审查，无尚未修复的阻断发现；仍不是 stock Codex 原生运行认证。**

## 1. 审查对象与基底

- 被测目录：`rpnh-codex-history-projection/source`。
- 实测组合基底：main `715468dab0b1bea07d7e94a7aa0606eaf194365c` + 已冻结 native reader overlay + 已冻结 effort codec overlay。
- reader patch SHA256：`f4726d7c1a230c8935b22310bb1d29f7c8e68342ef9b660cabb6430b8a3d4654`。
- codec patch SHA256：`3be0de65edb5a4f68579f115f5072bacc3e4fe4ffefeba96b226e45cbcab8a8d`。
- 当前 21 文件增量：`codex-owner-history-projection.patch`。
- 增量 SHA256：`c1d5665c4955b0c129cc7b0b89b456a55307597bd0febae0ea1f76290b089fcb`。
- 新 main `d92ff370…` / H2a 的另项核对不替换本次实测基底，不将本结果称为新 HEAD 全面回归。

独立验证了全部 21 个 manifest 文件哈希；在临时目录复制组合 baseline 后执行 `git apply --check` 和 `git apply`，应用后的完整树与冻结 source 一致。全局 `ObjectStore` 未变化。

最终测试前后对 source 的 1041 个非缓存文件作哈希比对：完全一致。没有边测试边修改 source 的采样不确定性。

## 2. 审查中发现并修复的问题

### A. 真实读取路径仍使用可写 SQLite 连接

初始组合回归为 87 passed / 1 failed：纯读 fingerprint 观察到 canonical SQLite 文件变化、原非空 WAL 变空。另写连接模式探针确认，一次 `thread/items/list(limit=1)` 打开了 **235 个普通可写 SQLite 连接**。因此不能把问题仅归结为测试噪声，也不能继续声称历史路径物理只读。

字节变化与 SQLite WAL checkpoint 一致；其发生时机可受连接生命周期/GC 影响。该结果本身不证明新增了业务事实，事件/head/epoch 与文件字节必须分别判断。

修复：`MainSession.history_registry` 使用既有 `_RegistryCore(create=False, read_only=True)` 打开同一物理 source；cut、分页、safe hydration 以及 source metadata 重核均经该原生只读句柄。未建立新 Registry、grant、writer epoch、lease 或第二授权体系。fixture 仅在 setup 阶段完成已结束 writer 连接的回收，再建立物理观察窗口；产品读取不调用 GC/checkpoint。

修后独立探针确认所有新连接都以 `uri=True` / `mode=ro` 打开；产品回归另断言 `PRAGMA query_only=1`，并禁止调用 writer 的 `connect`。

原始失败证据保留：
- `../independent-history-regression-initial.log`
- `writable-history-connection-probe.log`

### B. Source binding 漏校实际 event_store.path

初始静态检查发现：固定原路径的 inode 检查并不自动验证 `core.event_store.path` 仍指向该路径。重绑为保留相同 task/branch 的克隆 DB，可能绕过原固定路径检查。

修复在原 `MainSessionSourceBinding` 内完成：同时核 owner/read 两个 handle 的实际 DB/object-store 路径、source identity 和 read-only flags。独立测试使用 SQLite backup 制作同 identity 克隆 DB，确认重绑请求被拒绝；发送锁等待中的 DB/source/lease/ThreadState 更换也均被拒绝。

### C. Lease acquisition 新失败路径的 fd 清理

新增 root identity 捕获若失败，原草稿可能保留已经获得的 fd。现实现先完成 root identity 捕获再设置 `self._fd`，异常明确关闭 fd。此项经静态复核；原 lease 竞争/释放回归通过。

### D. Stock threadId 的 UUID 要求

作者发现并在批准范围内修复：stock Rust consumer 以 UUID 解析 threadId，JSON schema 的 string 类型不能捕获旧 `ses_<hex>` 格式问题。Codex 专用映射保留原 deterministic session ID 的 128 bits，输出规范 UUID；generic frontend ID、Registry 身份和持久事实不变。旧 alias/cursor 明确拒绝。独立组合包含该映射与迁移测试。

## 3. 权限与数据边界结论

1. **复用既有 owner 边界，没有重复授权机制。** 本地私有 Unix socket、预绑定单 MainSession、owner lease、初始化 gate 与 thread 白名单共同约束入口。initialize 本身不认证调用者，lease 本身不成为 bearer grant，cursor/native cut 都只表示来源及位置。
2. **每请求和交付前复核有效。** `session_access.py:117–129,199–256` 复核 lease/source；`codex_app_server.py:596–652` 在取得 send lock 后再检查，关闭或换源不能继续送出已经渲染的正文。
3. **membership 与 body hydration 没有分离成可绕过接口。** `main_thread.py:1509–1531` 要求最多 100 个唯一 exact committed turn refs，重核同 cut lineage，随后显式 `view=cut.view` hydrate。
4. **正文出口只在 MainSession。** `main_session.py:1105–1134` 只返回原用户 text、reply、protocol_valid 提示和同 cut exact-origin launch annotation。private task prompt、stage/node instruction、workflow graph、native_plugins 配置/digest、profile、child body/path 和 receipt 不进入 wire。
5. **固定 cut 与 live 生命周期分开。** 旧页不调用 live TaskControl fallback、不追未来 child link、不打开 child Registry。`thread/resume` 保留既有 active reconnect/tracking；其历史 token 生成独立纯读，不能据此宣称整个组合 resume 永远无后台状态变化。
6. **同 source 原生 read-only 复用成立。** `main_session.py:1136–1149` 创建只读句柄，不创建第二数据库、不获取 writer fence；binding 同时钉住 writer/read handle 的相同实际 source。

## 4. 最终独立离线回归

**128 passed，105.65 秒，0 failed。**

- `tests/test_codex_history.py`：53
- `tests/test_main_thread_history.py`：14
- `tests/test_codex_compat.py`：29，包含原 effort codec / None marker / 配置 roundtrip
- `tests/test_frontend_session_access.py`：9
- `tests/test_frontend_boundary.py`：仅选择 method manifest 与 argv 两项纯静态测试
- 独立 `test_authorization_probes.py`：21

覆盖的关键边界：
- private prompt/plugin/graph sentinels 不泄漏，原中文 text/reply 完整保留；大于 4 MiB 的合法正文无隐式截断；显式预算整请求失败。
- committed membership、future/accepted/错误类型 refs、重复 refs 拒绝；失败 ordinal 间隙不压缩。
- turns/items 双方向、多个 page limit、summary/full/notLoaded、turn filter、next-exclusive 与 backwards-inclusive、分页完整性、空 resume 后追加、old/new cut 与 restart。
- resume 两初始 cursor 共 cut；初始 view-unbound 与后续严格绑定。
- cursor 重复 key、未知顶层/嵌套字段、非规范编码、深嵌套、NaN、布尔伪整数、未来 boundary、错误 event/source/query/order/view/filter 均拒绝。
- 未初始化、未知 thread、close、lease/root/DB/object source 改变、实际 DB path 重绑、send-lock 等待期间撤销/替换拒绝；错误响应不回显私有 cursor。
- live terminal notification 与 cold item 使用一致真实 ordinal ID；原 live resume hook 保留，历史 page/read 不启动该 hook。
- 无 history writer connection、provider、launch、persist、reconcile 或 child read；原 effort codec 无回归。

同一小 fixture 的连接观测为 178 个连接、0 个可写连接，约 0.263 秒。这里只记录一次样本，不作为大历史性能保证；当前每页仍验证 lineage。

## 5. SQLite 物理副作用的准确范围

独立活跃 WAL 探针明确保留 fixture writer 生命周期，在 WAL 中追加已提交 turn 后，历史可读到新 turn；禁 writer.connect 后，canonical DB、immutable objects、非空 WAL 与 head/epoch 在该观察窗口保持不变。SHM 明确排除。

**mode=ro / query_only 不等于整个目录零物理写。** SQLite 首次打开可能建立 SHM、空 WAL 或更新协调信息。其他独立只读验证也观察到无 sidecar 的完成 Registry 建立 32 KiB SHM / 0-byte WAL。本报告不将这类 bookkeeping 描述为新增业务 authority，也不因它使用 `immutable=1` 绕过活跃 WAL。跨平台、所有首次打开情形的目录字节完全不变不是已验证承诺。

## 6. 剩余本地 gate 与明确未做项

- 必须在原生支持 AF_UNIX 的实际环境，以精确 stock **0.155.0** 做 cold resume、双路 hydration、滚动翻页、live terminal/cold 去重和活动任务重连验收。此处仅纯 Registry/JSON/mock transport，**没有运行 native socket/TUI**。
- 当前 pin 仍为 0.155.0。0.161 schema 对照不等于其新 object cursor、consumer 或原生版本认证。
- 若最终落点换为新 main/H2a，需要明确再合成后的字节与对应回归；本报告不替代那个 gate。
- 全局 ObjectStore no-follow/dirfd 加固未实施，也不声称抵抗恶意同 OS owner 的 ABA 路径/内容竞态。继续沿既有可信 local-owner 与 immutable Registry 假设。
- 没有安装、登录、模型调用、Actions、push；没有修改作者冻结的实现或测试文件。

## 7. 证据文件

- `final-independent-tests.log` / `final-independent-tests.xml`
- `test_authorization_probes.py`
- `final-source-before.json` / `final-source-after.json` / `final-source-stability.json`
- `patch-manifest-verification.json`
- `authorization-probes-readonly-fixed.log`
- `workflow-private-fields-probe.log`
- `active-wal-probe.log`

最终判断：**通过离线代码与权限/投影审查，可进入独立授权的 stock 0.155 本地原生 gate；不得提前标记为原生运行认证通过。**
