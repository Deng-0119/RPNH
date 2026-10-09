# 本地返回规范

返回一个独立结果包，不改本分发包中的历史证据，不上传真实 Registry DB、凭据、用户配置、完整 HOME 或原始个人会话。

必需内容：

1. REPORT_ZH.md：逐 gate 标记 PASS / FAIL / BLOCKED_ENV / NOTRUN / INCONCLUSIVE / DEFERRED_KNOWN_GAP；说明 blocker、实际完成范围和剩余缺口。禁止用“全兼容/新版已适配”概括本包。
2. identity.json：repo/revision/dirty 状态、H1/H2a 应用情况、codec patch SHA-256、12 最终文件 hash、OS、Python、Codex binary 版本与 SHA-256、已授权预置来源。binary 路径可规范化，不能包含凭据。
3. commands.jsonl + logs/：每项实际 argv、cwd（可脱敏）、UTC 起止、exit、stdout/stderr/JUnit。保留所有失败及重跑，不只保留最后 green。
4. native-evidence/：使用 stock 0.155.0 的 bootstrap/picker/config 保存/空 Registry cold reopen、typed-client 响应接收、三类 selection 互切截图或终端记录；没有原生运行就写原因，不用 Python 假 socket 冒充。
5. safety.json：fail-closed guard/入口来源、文件/hash、阻断验证结果；provider/model/probe/worker/补偿的实际计数；隔离 config 路径；全局用户配置未改的证据（只返回 hash/结论）。0 必须有测量依据，不能默认填 0。
6. roundtrip.json：每次 selection_id、physical profile、exact provider/model、canonical reasoning_effort 的类型与值、wire effort、config 保存/冷重开值，profile/adapter 前后 hash；未配置 None 与 literal none 分开。
7. af-unix.xml + logs：既有 socket 单项结果单列；明确该项用 /bin/true，并不是 stock TUI 验证。原生连接另列。
8. history-status.json：本包不修分页；thread/turns/list、thread/items/list 及至少两 committed turns hydration 留作独立包，当前 DEFERRED_KNOWN_GAP。
9. SHA256SUMS + distribution-provenance.json：如脱敏，对每文件记 original_sha256、distribution_sha256、变换类型。XML 用解析器/serializer escape 属性和文本；JSON 用 json.dumps。保证 JUnit 可解析且 suite counts/case names/outcomes 未变。保留错误类型、errno、版本和关键断言。

只有满足完整 gate 的项目才 PASS。缺 binary、支持入口或权限时，返回最小可复核 blocker 即可，不越权补装、登录、换 pin、真实调用或推送。
