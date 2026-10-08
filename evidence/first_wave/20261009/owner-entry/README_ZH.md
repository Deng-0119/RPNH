# H1：既有 owner 执行入口收敛与原生补验

**PASS_NATIVE_FINITE**。默认原生 focused36项与经典AgentTask中断/恢复3项全部通过，共39个不同repo-relative测试ID、39次执行，零失败/错误/跳过。focused中包含纯单元检查，39不是39个socket integration。

代码基线 `674252feb836f631c162979f177d1fe91f22559f` 加冻结七文件补丁；后继 `ec9077e` 的五个证据修订完整保留。三个生产Python文件仅收敛既有Orchestrator的stop/执行入口，旧make_harness返回形状保留；没有新增scheduler、owner、queue、Registry authority或资源关闭责任。七文件保持包内最终字节，未新增测试或产品修复。

- [完整本地报告](local/REPORT_ZH.md)、[分层结论](local/LAYERED_MANIFEST.json)、[39项精确清单](local/test-inventory.json)、[独立复核](local/review/)。
- [原生命令/stdout/stderr/JUnit](local/logs/)、[CLI/独立回读审计](local/native-cli-readback-audit.json)、[合成Registry JSON导出](native/)。
- [原云端交接与失败历史](cloud-handoff/)、[原AF_UNIX权限失败](cloud-handoff/native-attempt.log)、[逐文件原件/公开副本身份及转换](MANIFEST.json)。

native CLI 的真实结果为 **2.000 kWh / 1.70 CNY**、模型计数 `[0,0]`、一个complete terminal、10个published firing、12个业务资源及2个原始输入。独立进程回读的全部11个稳定字段一致，Registry ordinal/event count1001、dispatch/execution start10前后不变；没有新增执行。

两种worker数的旧/新入口语义等价、受控并行与join/lineage、stop排空且无后继、host-owned loop/pool各close一次、提前stop零dispatch和兼容factory均通过。经典三个用例使用实际OSSIGINT和显式resume，最终逻辑账本分别`[2,0]`、`[3,0]`、`[3,0]`，真实provider请求0；不把8次scripted逻辑调用当成真实模型调用。最终handler identity的独立OS探针未新增，恢复调用与资源责任的精确覆盖范围见报告。

云端pipe语义39 unique/50 executions与原权限/fixture失败仍保留历史结论；不替代本轮原生证据。早先共享harness A的PARTIAL_ENV未在本任务重测。没有H2a、真实provider/Docker/Actions/业务benchmark或历史分数改写。

数据库、private profiles、完整请求/对话、缓存、venv、ZIP仅留本地。公开本地副本仅替换必要工作区前缀，XML涉及替换时使用实体；原件和所有变换摘要在MANIFEST。代码与证据按用户长期GitHub授权一起交付。
