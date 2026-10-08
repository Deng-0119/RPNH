# 可解析的公开 JUnit 派生副本 / Parseable public JUnit companion

来源为提交 `674252feb836f631c162979f177d1fe91f22559f` 的既有公开证据。扫描了本批全部 21 份 XML（本地回传 7 份、云端交付及历史 14 份），仅原始公开 [A-offline.xml](../local/logs/A-offline.xml) 需要派生；其余 20 份无需改动。

- [可解析的 A-offline.xml](local/logs/A-offline.xml)
- [完整来源、转换、摘要与全批 XML/stdout 核对](PROVENANCE.json)
- [未改动的原始 stdout](../local/logs/A-offline.stdout.log) 与 [原命令记录](../local/logs/A-offline.json)

唯一转换是把 9 处明确的脱敏占位符 `<WORKSPACE>` 精确替换为 `&lt;WORKSPACE&gt;`（失败 message 属性 4 处、失败正文 5 处）。其余字节完全一致，没有重序列化 XML。解析后的占位符仍是相同文本；242 项测试身份与结果逐项对应既有清单，完整失败消息及 traceback 保留。

原运行仍为 **241 passed / 1 failed / 0 errors / 0 skipped，exit code 1**。失败仍是 `tests.test_registry_read_session::test_public_config_opens_real_context_in_second_process`。本地整体 **PARTIAL_ENV**、A **BLOCKED_ENV**、B **PASS** 均不变。此次只核验已有文本，未重跑测试、模型、Actions 或修复运行环境。旧 XML、stdout、验证快照和 MANIFEST 均不覆盖；派生摘要单独记录于 PROVENANCE，旧 MANIFEST 不为新文件背书。

English: This is a derived companion to the existing public evidence, not a new test run. All 21 batch XML files were scanned; only A-offline.xml required escaping nine redaction placeholders. Every other byte, testcase identity and failure is preserved. Both XML parsers accept the companion, and its counts match the original stdout and inventory: 242 tests, 241 passed, 1 failure, 0 errors, 0 skipped, command exit 1. PARTIAL_ENV / BLOCKED_ENV remain unchanged. The source XML, logs, snapshot and historical MANIFEST are retained unchanged; independent provenance records the new artifact.

另见 [验证快照元数据勘误](../derived-metadata/VALIDATION_SNAPSHOT_ERRATA.json)：原快照保留，仅补充命令来源和退出码的准确解释。See the separate metadata errata for process attribution and exit-code interpretation; reported case verdicts and the original snapshot are unchanged.
