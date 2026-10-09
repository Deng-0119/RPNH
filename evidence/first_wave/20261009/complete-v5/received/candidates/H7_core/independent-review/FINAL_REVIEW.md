# H7 Registry 窄核心独立实现审查

## 裁定

**APPROVE_NARROW_REGISTRY_CORE_D0_ONLY。** 本裁定仅适用于下述最终冻结字节和所列真实 Registry / PN 的窄 D0 边界。它不是 H7 整体 D0 通过，不是 native parent-child 功能上线许可，也不是 merge / push 结论。

本轮沿用并完成原独审工作，保留原失败、修正、未完成窗口；没有改作者产品、S1 输入或冻结设计。旧 `61421bd9…a3ca9d` 候选在收尾时发现新的 prospective lifetime 缺口，因此不能仅凭其 38 PASS 接纳。作者修补后，本审另建不可混用的新源码副本并完整复跑。

## 1. 最终源码身份

- 项目：`Deng-0119/RPNH`，候选 `rpnh-parent-child-core-implementation`。
- 固定基线：`1f191645c4d60c8b190d42e9fad99c85e8981c03`；原 product commit：`d92ff3704b6002bf5ecbccb3e6a3d1489809a805`。
- 接纳的 S1 输入：979 文件；patch SHA-256 `bd0e2a8d362179fd68bd5451a932449f059b7038db16ca627eb84843f809bd4d`；source-identity 文件 SHA-256 `b65249d3771c306773c6930d39323d019450f7c668e0f7d778c798b70278f753`。没有把 S1 原来的测试数算成本轮 H7 测试。
- 最终 H7 源：**992 文件**，聚合 SHA-256 **`ac68327e442b7bda8a6aa2ba93c0cd20ad72181a661b0497627b08713334907e`**。
- 独审副本：`final-source-v2/`；冻结依据、逐文件 SHA-256 和复制核验分别在 `FINAL_V2_SOURCE_MANIFEST.json`、`FINAL_V2_SOURCE_VERIFICATION.json`。
- 相对上一冻结 `61421bd9fd2f389a164c54fbe6fc92086e3a9ff571367c888174782a51a3ca9d`，仅 `cpn/rpnh/registry/parent_bound.py` 改变。前后清单、独审副本、作者当前源逐字节一致。没有把旧版本的运行结果直接迁移到新版本。
- 这是 curated、未合并候选；没有核验当前远端 HEAD，也不声称 full-repository coverage。

## 2. 最终测试计数与独立性

最终本审运行 `FINAL_V2_RESULTS.json` / `final-v2.xml` / `final-v2.log` 三者相符，source-before 与 source-after 相等：**85 PASS，0 FAIL / ERROR / SKIP，sentinel 命中 0**。

组成如下：

1. 独审原创 **40**：原 reviewer 的 38 个参数化 testcase，加收尾新增 lifetime 与额外 capability 边两例。
2. 对作者 H7 testcase 的独立复跑 **31**。
3. 既有通用 S1 对照 **5**：exact static reference 生命周期 2、同 transition 并发与 successor reconstruction 1、普通 data/control read 2。
4. 既有普通 Module recovery 对照 **9**。

合计 40 原创 + 45 复跑 = **85 个去重 pytest 节点**。路径前缀规范化后再按 `filename::testcase[param]` 去重；同用例在多个版本或进程重复执行不增加用例数。不同测试节点可能覆盖相似语义，因此不把 85 解释为 85 个互不重叠的合同窗口。全部规范化节点名单、JUnit 交叉核验和文件 hash 在 `FINAL_AUDIT.json`。

作者 clean `final21` 的 JUnit / 日志 / result / 前后源码清单也经本审核验：**99 PASS，3 个 subprocess 用例明确 deselected、未运行**，组成是作者核心 31 + 既有邻接 68。两方最终组共 **184 次成功执行，45 个重复节点，139 个去重参数化 testcase**，即 31 作者核心 + 68 既有邻接 + 40 独审原创。独立重算名单和原始证据 hash 见 `JOINT_FINAL_COUNTS.json`；不把 184 次执行写成 184 个新用例。

这些是有真实临时 Registry/SQLite、原 admission/Start/products/Success 的窄 D0 测试，加必要的纯校验和负例；不是全部用例都代表完整父子执行。未运行新进程 cold-reader 例，未启动 socket、PTY、Popen、native worker、模型或业务 API。进程内重建不能冒称真实跨进程验收。

## 3. 实际确认的边界

- 原 EventStore `BEGIN IMMEDIATE` 路径在 exact transaction replay 的早返回之前，重核 parent-child 和 bound 条件。正向权限仍来自原 invocation/FiringView、ordinary operation Start、current run authority、writer fence 与 exact PN claim。
- intent → dispatch → worker → acceptance 保持原 producer 的 PROVISIONAL scope。全 run 单 slot 的已用记录只排除第二次执行；不会把历史记录变成新的 dispatch / receipt / retry 权限。
- marker、required protocol、native genesis、task/branch、原 writer 和同事务对象闭合；缺失或冲突时不退回 standalone。
- protected origin 是原 Registry 资源和原 PN/M0 中的一枚 S1 non-consuming reference。精确 provenance、原本库 catalog/schema source、task/root membership、initial declaration、lease identity 和每个 transition 的 read arc 连同 admission/Start/I/O/Success 边界一起受查。
- bound writable reopen 在 Core 的 EventStore / ObjectStore 创建前拦截，底层 acquire_writer 另有保护；bound resume/reopen/refence、successor adoption 与 retained/direct transaction 旁路被拒绝。只读诊断保留。
- 普通 Module / generic S1 的运行和 recovery 对照仍通过。未将 bound 限制推广为全局禁止 Module 动态组合；本轮也没有新增第二套 scheduler、ledger、marking 或 authority。
- memory stop → drain 正向用例使用真实 Harness 标记、pending/completion 逻辑，但 wake 仅 socket-free shim；它不验证真实信号、OwnerEventLoop transport 或 pending-stop/receipt 的全部竞态。

## 4. 发现、修补及复审

### F1：同 writer 的 stopped → running raw authority revival

原 `ROUND2_CHILD_RESULTS.json` 有真实失败。只挡 wrapper 不够，原 transaction 层必须拒绝 closed bound authority 的复活。最终独审原始负例、作者对应例均通过。

### F2：durable owner stop 后第一次 business Start

原 `ROUND2_STOP_START_RESULTS.json` 有真实失败。最终 admission/Start/I/O 与原低层 transaction replay 都重新确认 running/fresh bound authority；stop 后无新提交。没有把仅内存 pending stop 与 durable stopped 混为同一状态。

### F3：protected weak edges 先提交、后 integrity 拒绝

原 `ROUND3_WEAK_EDGE_RESULTS.json` 与 corrected 结果保留 bootstrap/origin 两个失败。最终必须在 prospective 同事务闭合中拒绝 weak 或错误 producer/system-owned 关系，不能把“最终抛异常”误记为“没有提交”。最终两组原创负例都确认未提交。

### F4：protected capability 的 lifetime 只在历史读取时核验

收尾原创 `test_prospective_protected_lifetime_fails_before_commit` 在旧 61421 源上真实失败：用本库合法 task_ref 替换 bootstrap lifetime，原普通 private-system publication 可提交 origin；随后才被 bound integrity 拒绝。`FINAL_PROSPECTIVE_RESULTS.json` / `final-prospective.xml` / `final-prospective.log` 保留已提交 `parent_bound_origin/v1` 的断言证据。

最终修补在 `validate_bound_publication` 同事务确认 `origin_kind=private_system`、producer / lifetime 均为 exact bootstrap、task 正确，并保留原 catalog/schema/bytes 校验。最终同一个原创负例 PASS 且无 protected origin 提交。

同次补丁还使 marker self-ref 与六个同事务 native 对象 exact refs、自身 ID、task/branch/catalog 闭合显式化，并要求 origin 四条边加 capability produced_by 构成完整强/system-owned关系集合。这些是同闭合的防御性补齐。本审新增的额外 capability→task 边反例在旧源已被既有下层拒绝，故**不把它称为新发现的旧版漏洞**；它作为最终回归保留。

## 5. 历史失败与无效窗口

所有 root 日志 / JSON / XML 及四份旧产品源码快照继续保留。便携包用 `historical-source-deltas/` 的 overlay 从最终源逐字节还原这四份快照，不携带临时 Registry 数据库。原 reviewer 的外置测试曾在早期迭代中修改，并非每轮都有完整测试源快照；旧日志保留失败断言，最终测试源完整保留。`FINAL_AUDIT.json` 逐窗记录原退出码、失败节点、snapshot hash 和是否可计最终证据。

- ROUND1 的 products 即终态假设与二容量 PN fixture 初始 token 错误属于测试问题；corrected 轮的 closed 文案 regex 错误不等于产品未拒绝。
- ROUND3 主运行的两个失败分别是正确的 StaleInvocationContext 未被原断言接纳，以及 native parent observation/completion 被正确标为 UNSUPPORTED 后，旧正向 Success 假设失效。最终测试验证相应真实边界，没有为让测试通过而放开产品。
- 旧 `FINAL_REVIEWER_RESULTS.json` 的 38 PASS 在旧冻结源成立；随后完整补跑 31+14 得 45 PASS，记录在 `FINAL_RERUN_RESULTS.json`。两者均只算历史，不能覆盖 F4 或最终新字节。
- 旧 `final-rerun.log` 实际只有 **18 字节 / 18 个点**，无 JSON/JUnit/终态；计数为 0，不能用进度点补足结果。
- 作者的探索失败、moving-source invalid 轮、被中断的 regressions02（实为 38 字节：37 个点、1 个 F，无终态），以及 core20 / regressions04 wrapper 收尾错误由作者包保留。wrapper 或部分进度日志不能冒充 clean terminal run。最终产品裁定只引用可核的最终版本证据。

## 6. 仍然不支持或未验证的范围

生产 native evidence issuer 仍不存在。`tests/parent_child_fixtures.py` 用 `object.__new__` 构造 private test witness；它是离线注入，不是 OS peer、receipt、路径 reservation 或 hostile-Python sandbox 的证明。公共 JSON、DTO、ready Boolean 或 private_system 标签均不能因此获得 production bound-create 权限。

以下继续 **UNSUPPORTED / NOT_RUN**，不得据本报告声称 H7 已完成：

- 原 OS transport 的双向 peer/birth tuple 验证、fresh-only Popen/dispatch ticket、真实 sticky owner stop、严格完整 receipt delivery。
- typed physical target reservation 以及 Core/store/writer 之前的 held handle/device/inode 校验；EEXIST、symlink、partial target、跨 intent 碰撞的物理 zero-touch。
- 实际 AgentTask 与 Module 两条原 wrapper 的 reservation→bootstrap→origin→runner 接线、通用 mechanical lowering、完整 installed material inventory。
- source-qualified 历史 acceptance classifier，parent 同 cut observation / registered completion / Success。当前 native parent completion 明确拒绝并保留 claim，不能用自报 child products 释放 slot。
- v3 的完整 **O66 D0 未闭合**；**N36 D1、R10 H7b 均 NOT_RUN**；H8 / consumer migration 另行处理。

本窄核心可以作为后续原框架 native 接线的已审候选依赖；下一阶段仍必须独立实现、冻结并验收上述缺口。当前不允许用测试 witness 或历史 acceptance 代替它们。
