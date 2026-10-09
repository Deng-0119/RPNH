# 状态与证据边界

## 已有执行结果（本轮打包不重跑产品测试）

- 作者最终：43 passed，1 deselected，`final-tests.log/xml`。
- 独立复跑：43 passed，1 deselected，`review/independent-tests.log/xml`。
- 独立补充：4 passed，`review/supplemental-tests.log/xml`；完整评审 `review/REVIEW_ZH.md`。
- 起初 transport 运行：36 passed，1 failed。既有 AF_UNIX case 实际触发 PermissionError/errno 1，`existing-tests.log/xml`；最后明确 deselect，不计为 pass。
- 初次新增 fixture：4 passed，2 failed。错误假设 TypeScript 为枚举，改为真实 string alias 后重新通过；失败记录 `initial-codec-fixture-failure.log/xml` 不被覆盖。
- RPC 使用 FakeWebSocket；turn/start 止于 prepare_turn spy。没有真实 turn 执行、provider/model/API 调用。
- 官方 0.155/0.161 model/list schema 均通过；Rust/TS 仅源码/fixture核查，未编译，也不等同于原生客户端认证。

## 本轮打包已做

12-file source hash、5 old blobs、patch hash；在新临时 old-blob 树执行 git apply --check/实际 apply 后逐字节比对 12 文件；全包哈希、ZIP CRC/内容一致性、所有 JSON/XML 可解析、JUnit case/outcome 和 suite 计数保留。H1 路径及目标 blob 核对，H2a ZIP 目标路径核对。

以上是包装完整性，不能替代产品组合测试。H1+H2a+codec 组合测试：NOTRUN。

## 本地仍需结果

真实 0.155.0 bootstrap/model picker、config/read/batchWrite、thread/start/resume 的 typed-client 接收、None 与字符串 none 跨 selection 精确区分、隔离配置保存/空 Registry 冷重开；零真实模型调用；AF_UNIX transport。详见任务书。

## 保持开放的独立缺口

- 产品声明 paginated history，但 thread/turns/list、thread/items/list 仍缺独立实现。本包不改 historyMode、不降为 legacy，不以空历史冷重开冒充历史 hydration。
- 至少 2 个 committed turn 的真实分页/cold-resume 必须等独立分页包，再单独验证，当前 DEFERRED_KNOWN_GAP。
- 0.161.0 原生支持 NOT_SUPPORTED_BY_PIN；静态 schema pass 不允许放宽 pin、替换 binary 或宣称新版适配。
