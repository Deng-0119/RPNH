# 最终文档 metadata 一行修正：独立补充确认

2026-10-08。

结论：**doc-only 增量确认通过，可以封包。**

- 原已审补丁：`c1d5665c4955b0c129cc7b0b89b456a55307597bd0febae0ea1f76290b089fcb`。
- 最终补丁：`df0c3090e84f532d158dd02d7b65489323563a470843e4bd4e2688ffcc25900b`。
- 与原 128 项测试结束时记录的完整 source 哈希逐文件对比，1041 个文件仅 `docs/reference/codex-history_ZH.md` 改变，其他 1040 个文件完全相同。
- 将新页唯一的 `language: zh-CN` 逆改为 `language: zh` 后，字节 SHA256 精确等于旧文件哈希；确认只有这一行 metadata 修正。
- 新旧 21 文件 manifest 仅该页条目改变；新 manifest 全部哈希与 source 一致。所有产品 Python、测试和 schema 文件不变。
- 独立重新执行最终补丁的 `git apply --check` / `git apply`；应用后的完整树与最终 source 相同。
- 使用已固定 blob 的仓库文档 parser，重新检查两新增页、双语 metadata 与本地链接：通过。没有做全站构建。

原 **128/128 通过**准确对应旧全文哈希；最终补丁的产品代码/测试字节与该次测试一致，本次只补做文档差异及 parser/补丁检查，**没有重跑产品 runtime 矩阵**。既有 stock 0.155 原生 AF_UNIX/TUI gate 未跑、0.161 未认证等限制保持不变。

证据：`doc-only-addendum-verification.json`、`doc-only-parser-confirmation.log`；原完整报告与全部测试/失败日志保留。
