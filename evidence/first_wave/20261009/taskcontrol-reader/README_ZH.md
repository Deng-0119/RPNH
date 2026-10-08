# H2a 本地验收证据

H2a + H1 组合源码有限验收通过：166 不同用例 / 166 次执行。仓库内 154，包附独立检查 12；三项经典恢复测试在两项任务之间共享一次执行。没有真实模型/provider 调用。

- [本地验收报告](local/REPORT_ZH.md)
- [分层结果与八个窗口](local/LAYERED_MANIFEST.json)
- [用例清单与去重](local/test-inventory.json)
- [CLI 与独立回读审计](local/native-cli-readback-audit.json)
- [源码前后身份](local/source-before.json) / [测试后](local/source-after.json)
- [原始命令、stdout/stderr 与 JUnit](local/logs/)
- [独立代码与证据复核](local/reviews/)
- [云端原失败及来源说明](cloud-handoff/IMPLEMENTATION.md)
- [云端 native status 原日志](cloud-handoff/independent-review/native-taskcontrol-status.log) / [resume](cloud-handoff/independent-review/native-resume.log)
- [old-red 原日志](cloud-handoff/old-red.log)
- [收到的分发包此前脱敏记录](cloud-handoff/packaging/DISTRIBUTION_PROVENANCE.json)
- [公开文件哈希与每处本地替换](MANIFEST.json)
- [公开文件核验](PUBLICATION_CHECKS.json)

64 个收到的包成员保持原字节；它们此前的云端分发脱敏另有 provenance，不宣称等同于分发前原件。新的本地通过不改写旧 AF_UNIX EPERM 和 fixture 失败。完整本地原件与 Registry 留存，公开材料只替换私有工作区/输入目录前缀，XML 重新解析。

范围为指定 H2a 和必要的 H1 组合门槛，包含纯单元测试，不称 166 项 socket integration 或全仓验收。早先 A1 阻断保持历史、未重测；无 Actions、Docker、业务 benchmark 或新返回 ZIP。

## 用例路径勘误

`local/LAYERED_MANIFEST.json` 与 `local/test-inventory.json` 中，`h2-shared` 的 15 个用例路径 `tests/test_registry_reader_consumers.py` 应按原始执行命令解读为 `examples/harnessaudit_office/tests/test_registry_reader_consumers.py`。用例名称、参数和执行结果不变。以完整仓库相对路径重新去重后仍为 166 不同用例 / 166 次执行（仓库内 154、包附独立检查 12），无需重跑。原始 JUnit、命令记录及历史清单保留不改。
