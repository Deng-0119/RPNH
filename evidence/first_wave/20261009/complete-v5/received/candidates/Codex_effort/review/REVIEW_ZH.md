# Codex wire-effort codec 独立审阅

日期：2026-10-08 UTC

## 结论

**本次窄修未发现阻断问题，可进入原生本地验证 gate。**

这只确认 selection-scoped effort codec 的离线正确性，不是 Codex 全量兼容认证，也不是
0.161.0 升级许可。产品仍固定 0.155.0；历史分页方法缺口和 AF_UNIX transport 验证仍未关闭。
审阅未修改候选生产源码、测试、文档或 fixtures。

## 固定对象与范围

- RPNH 基线：`ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4`。
- 基线生产文件的官方 Git blob：`62b14609aff161a4a031b70f659251880b03e60c`，独立只读回查一致。
- 最终补丁：`codex-effort-wire-codec.patch`。
- 补丁 SHA-256：`3be0de65edb5a4f68579f115f5072bacc3e4fe4ffefeba96b226e45cbcab8a8d`。
- 最终生产文件 SHA-256：`6c9beb7fda5d3cf66a79781ff6a83e53c49fc8efa1e770a8a5865c047955804f`。
- 12 个变更文件：仅 `cpn/frontend/codex_app_server.py` 为生产实现；其余是测试、双语模型文档、
  第三方声明、两版 model/list schema、两版 ReasoningEffort TypeScript、provenance、LICENSE 和 NOTICE。
- 独立计算的 12 文件快照与作者最终 manifest 一致；审阅期间候选未变化。
- 对提供的 1015 个基线文件做 manifest hash 自洽检查，全部一致；不是声称重新从远端下载并核验了整仓。
- 独立将补丁在新复制基线上 `git apply --check`、实际 apply，再比较 12 个最终文件的 bytes，全部通过。

详见 `integrity.json`、`reviewed-files.initial.json`。

## 实现边界检查

1. `codex_app_server.py:439–458` 只在 canonical effort、default 都为 None 且 supported 为空时，
   把出站空 effort 表示为 `none`。现有 selection loader 已拒绝 supported/default 不一致的 metadata，
   不会以这个 helper 弥补或掩盖无效 profile。
2. `:465–496` 先按既有 selection ID 找到 logical profile，再条件性解码。实际配置的字符串
   `none` 仍经既有 `(selection_id, reasoning_effort)` variant 查询，保留精确 physical profile。
   `_assert_profile_identity` 仍在所有成功选择后执行。
3. 没有添加第二套 profile/Registry authority，也没有把 exact model 名当作 selection 身份。
   既有 ProviderModelRegistration、ExecutionProfile、MainSession 和 Registry 路径继续权威。
4. 出站覆盖 model/list、config/read、thread/start、thread/resume。入站的 thread/start、turn/start、
   thread/settings/update、config/batchWrite 仍集中经过 `_profile_for_selection`。
   resume 本次只是正确编码现有 canonical 状态，没有增加 profile 切换、worker 启动或恢复行为。
5. 空 supported 列表原样保持；没有伪造 medium 或额外 supported effort。未知 selection 或不支持的
   effort 继续拒绝。保存 config 使用选中 profile 的原 canonical 值，未将显示 token 写为无配置模型的真实 effort。
6. 0.155.0 常量、binary gate、initialize gate 和 compatibility manifest 均未放宽。
   `historyMode="paginated"` 未降级，缺失的 `thread/turns/list` 与 `thread/items/list` 仍需独立处理。

## 上游合同与许可

直接通过官方 `openai/codex` 固定 commit 读取并与 fixture/source provenance 比较：

- 0.155.0 commit：`f0a1b8f0849d90960bc406b848f32e5a129b0457`。
- 0.161.0 commit：`979011409de0a60b52f179721948e65531d26144`。
- 两版 ModelListResponse 的 defaultReasoningEffort 都是必需的非空字符串类型，不能为 JSON null。
  fixture Git blobs 分别为 `657a433f88ae548aa81fb1c2ee44ba4fc5a781f8` 和
  `ee3ca9ae1449457d8338507274d587342780f891`，本地 bytes/hash 一致。
- 两版 Rust ReasoningEffort 都明确包含 None，serializer/parser 对应字符串 `none`；
  两版 generated TypeScript 实际均为 string alias，blob 同为 `d40f5bd6578e63f45f9fd0bee8d8979026561de7`。
  这能支持源码层合同判断，不能替代 Rust/TS 编译或真实客户端解析。
- 两版 picker 源码在 supported 为空时使用 default，唯一普通 choice 直接 apply。候选的
  Python 测试诚实标注为 source-derived projection，没有声称执行真实 Rust picker。
- Apache-2.0 LICENSE 和上游 NOTICE bytes 与官方 blobs 一致；根 THIRD_PARTY_NOTICES 已说明来源。
  补丁只带需要的 4 个 schema/TS 文件、provenance 和许可文件，没有搬入整仓或 Rust/TUI 源文件。
  打包交付无需附带作者外部 `upstream-readbacks.json` 的完整源码快照；链接、哈希及必要 fixtures 已足够。

精确官方链接与 blob 记录见 `official-source-readback.json`。主要来源：

- [0.155 ModelListResponse](https://github.com/openai/codex/blob/f0a1b8f0849d90960bc406b848f32e5a129b0457/codex-rs/app-server-protocol/schema/json/v2/ModelListResponse.json)
- [0.161 ModelListResponse](https://github.com/openai/codex/blob/979011409de0a60b52f179721948e65531d26144/codex-rs/app-server-protocol/schema/json/v2/ModelListResponse.json)
- [0.155 Rust effort](https://github.com/openai/codex/blob/f0a1b8f0849d90960bc406b848f32e5a129b0457/codex-rs/protocol/src/openai_models.rs)
- [0.161 Rust effort](https://github.com/openai/codex/blob/979011409de0a60b52f179721948e65531d26144/codex-rs/protocol/src/openai_models.rs)
- [0.155 picker](https://github.com/openai/codex/blob/f0a1b8f0849d90960bc406b848f32e5a129b0457/codex-rs/tui/src/chatwidget/model_popups.rs)
- [0.161 picker](https://github.com/openai/codex/blob/979011409de0a60b52f179721948e65531d26144/codex-rs/tui/src/chatwidget/model_popups.rs)

## 独立运行及覆盖

使用已有 Python 环境，无安装。从 `source/` 运行：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. [PYTHON_ENV]/bin/python -m pytest -q tests/test_codex_compat.py tests/test_frontend_boundary.py --deselect=tests/test_frontend_boundary.py::test_codex_frontend_uses_external_socket_for_absent_long_root --junitxml=../review/independent-tests.xml
```

结果：**43 passed / 1 deselected，4.48 秒，exit 0**。见 `independent-tests.log/xml`。
作者同一最终候选为 43 passed / 1 deselected，复现一致。

新增 7 case 的证据覆盖 None、真实 `none`、low，跨 selection 后恢复、配置落盘、空 Registry 冷重开、
profile/adapter bytes、完整 execution identity、resume ordinal/writer epoch/lease、0.161 initialize 拒绝。
turn/start 在 prepare_turn spy 截止，仅证明执行交接前选择正确，不证明 worker 或 provider 成功执行。

另独立写入 review 目录的 4 个补充 case：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. [PYTHON_ENV]/bin/python -m pytest -q ../review/test_review_codec_boundaries.py --junitxml=../review/supplemental-tests.xml
```

结果：**4 passed，1.35 秒，exit 0**。见 `supplemental-tests.log/xml`。

- local_process 与 external_provider 两种配置元数据，两个 provider 下相同 exact model。
- 真正支持 `none` 但 default=low 的 selection，确认非默认 none 仍解析成自己的 physical variant。
- 先 effort 后 model 的 config edits、仅 effort 的 settings 更新、default-only thread/start、config 读取、
  跨 selection 后 cold reopen；生成目录的所有文件 bytes 保持相同。
- 两版 schema 都接受新输出，改回旧 JSON null default 会明确失败，验证回归测试确实锚定原缺陷。
- 审阅补充测试在已建事件循环外禁止新的 socket、Popen、TaskControl.start；未调用 provider 或 probe。

未将作者初始失败抹去：`existing-tests.log` 的 36 pass / 1 fail 确为 AF_UNIX 创建 errno 1；
最终明确 deselect，该项不计通过。本审阅没有重试被限制的 socket。
作者初始 TS fixture 假设产生的 2 fail 已记录并修正为实际 string alias，最终双版测试均重跑通过。

## 剩余原生 gate

以下都**未在本次运行**，须在另行授权、已预置相应客户端的本地窗口验证：

1. 保持 stock 0.155.0，使用 fail-closed execution double 禁止真实 provider/worker，做 bootstrap、
   model picker、config 保存、thread/start 和空 Registry cold reopen；确认原生客户端接受这些 wire values。
2. 并列未配置 effort、真实 none 为默认、真实 none 为非默认三类 selection；互切后核对 canonical 值、
   exact provider/model、adapter/profile path 和 bytes，不能因为 wire 同为 none 而串选。
3. 在支持 AF_UNIX 的已授权环境补跑本次 deselected transport test，并记录平台、binary SHA、候选 patch/hash、
   命令和实际结果。离线 FakeWebSocket 通过不能代替此项。
4. 0.161.0 继续被产品 gate 拒绝；如将来申请支持新版，必须单独做 native 认证，不能凭 schema 通过换 pin。
5. 有历史的 cold resume 仍受 paginated methods 缺口影响。等待独立分页实现后，再用至少两个 committed turns
   验证真实 hydration，并断言零 provider/worker/补偿执行。本窄修不承诺该流程已可用。

本次未使用真实 Codex CLI、登录、模型调用、安装、GitHub Actions、push 或部署。
