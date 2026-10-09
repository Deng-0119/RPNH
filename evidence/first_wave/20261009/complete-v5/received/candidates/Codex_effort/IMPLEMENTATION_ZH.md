# Codex reasoning-effort wire codec 窄修

## 已完成范围

基于 Deng-0119/RPNH `ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4`。
目标 `cpn/frontend/codex_app_server.py` 的原 Git blob 为
`62b14609aff161a4a031b70f659251880b03e60c`，已对官方仓库读取复核。
基线代码与 `674252feb836f631c162979f177d1fe91f22559f` 相同；两者间仅派生证据文档变化。
隔离目录按该代码树逐 blob 重建，不含 H1/H2 未合入变更。

只有一个生产 Python 文件改变。模型目录、profile、provider、Registry、PN、权限及
Codex 0.155.0 pin 均未改变。既有分页缺口未在本包修复，未降为 legacy，也未假称全适配。

- `reasoning_effort=None`、`default_reasoning_effort=None` 且 choices 为空的 selection，
  其 UI wire effort 统一编码为 `none`。
- 入站必须先查明 selection，再把该 selection 的 `none` 还原为 canonical None。
  真正配置字符串 `none` 的模型仍解析为自己的 exact profile 与字符串 `none`。
- 输出覆盖 model/list、config/read、thread/start、thread/resume；输入共享现有
  `_profile_for_selection`，覆盖 thread/start、turn/start、thread/settings/update、config/batchWrite。
- 不注入 medium，不添加 catalog choices，不变更精确 model，也不增加 provider 参数。
  支持集为空时，两版 upstream picker 的源码均回退默认 effort 并应用唯一选择。
- model/list 官方 0.155.0/0.161.0 schema 通过。Rust `ReasoningEffort::None` 的 serializer/parser
  均使用 `none`；生成的 TypeScript 类型实际为 string alias，不是枚举。未编译 TS/Rust，
  没有以源码阅读冒充原生 typed-client 测试。

## 验证

最终受影响边界：43 passed / 1 deselected，`final-tests.log` 与 `final-tests.xml`。
新增 7 个 case 覆盖双版 schema、None/string-none/low 全流程、相同 exact model 的不同
selection 互切、config 落盘、空 Registry cold reopen、解析错误和 0.161 原生 pin 拒绝。
profile 和 adapter 文件逐字节不变；execution identity（含 registry policy）保持相同。
resume 读取前后 canonical ordinal、writer epoch 和 owner lease 对象均不变。

RPC 范围：内存 FakeWebSocket + 真实配置生成/空 MainSession Registry；不创建物理 socket。
turn/start 测试只走到 prepare_turn 的 test spy，确认所选 canonical profile 后主动终止。
不会启动 worker，也不证明完成真实 turn。Popen 与 TaskControl.start 在新增往返测试中
被 fail-closed guard 禁止；没有 provider/model/API 调用、安装、登录、Actions 或 push。

第一次完整边界尝试为 36 passed / 1 failed，失败为既有
`test_codex_frontend_uses_external_socket_for_absent_long_root` 的 AF_UNIX errno 1，见
`existing-tests.*`。最终重跑明确 deselect 该项，不把它记成通过或用 pipe 替代。
初次新增测试错误地假设 TS 为枚举而有 2 fixture assertion failures，已按真实上游 string
类型修正；原结果保留在 `initial-codec-fixture-failure.*`。最终运行是修正后的结果。

## 原生本地 gate

在已授权且已预置 binary 的独立本地环境执行，不为本包安装/登录/换 pin。

1. 0.155.0 stock TUI 对无 effort profile 执行 bootstrap、picker 选择、thread/start、配置保存，
   重启后应选回同一 physical profile、精确 model 与 canonical None。
2. 同一 catalog 中另设真正支持 `none` 的 exact-model selection，互切并重开，确认它仍为
   字符串 `none`，没有因 UI token 与未配置模型混淆。provider execution 用 fail-closed
   application double 阻断，不进行真实模型调用。
3. 验证模型列表、配置和 start/resume response 被原生 typed client 接收；记录 binary SHA、
   RPNH revision、命令、exit 与截图/终端结果。
4. 重跑受限 AF_UNIX 项。0.161.0 仍被产品 version gate 拒绝；新版 native 认证为独立窗口，
   不能把本包的 0.161 schema 通过当成授权放宽支持版本。
5. 历史 cold resume 有已知缺口，应等待独立分页包后，对至少 2 个 committed turn 冻结
   Registry 验证真实 paginated hydration，并断言零 provider/worker/补偿操作。

## 来源

版本化 fixture 的精确 commit、Git blob、SHA256 与官方永久链接见
`source/tests/fixtures/codex/PROVENANCE.json`；完整 Apache-2.0 license 与 NOTICE 已保留。
上游源码的只读记录见 `upstream-readbacks.json`。完整分页下一包设计见 `HISTORY_NEXT_ZH.md`。
