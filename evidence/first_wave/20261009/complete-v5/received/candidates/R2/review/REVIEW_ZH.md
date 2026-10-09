# R2 单 Registry 确定性示例独立审查

结论：在本次有限范围内，未发现阻断缺陷。R2 可以作为单 Registry、可信纯 HOST、确定性合成条件的 D0 运行证据。独立复跑原示例 15 项与新增边界探针 5 项全部通过。原 Orchestrator/Harness 的 4 项使用明确指定的 test-only pipe；不能据此宣布 native AF_UNIX、真实模型、完整 R2 路线图或 R3 通过。

审查时间：2026-10-08。审查源为独立复制的固定快照，没有修改作者源。

## 源与补丁链

- 固定主线：`Deng-0119/RPNH@715468dab0b1bea07d7e94a7aa0606eaf194365c`。这是相关源文件的 materialization，不是完整 clone 或全仓审计。
- 独立核对 `source-provenance.json` 的 998 个主线文件，加原 `examples/tool_pipeline/tests/pipe_transport.py`，全部 SHA256 一致；R1 后继六文件与 `r1-successor-overlay.json` 一致。
- R1 后继 compiler SHA256：`4c0fb7c2bc93543b037614a3813c50b34172cf1f9942c8c22e20a3772efa3a5a`。
- R1 后继 full patch SHA256：`67466bfc13a685dd79dc26d0c0c2f0a33b7016553ccf53728b57906cc2f10ac3`；旧 R1 到后继 delta：`40da4de0316e5fcb07276fcc159c7277403eef942df4769ad361e5f40804e72d`。两条是替代路径，不应同时应用。
- R2 patch SHA256：`5054c53cc61bb03fdc58a1da6f7e56b41decc0ccb78124478effc38b2bf93e52`。在独立空目录执行 `git apply --check` 与 `git apply` 均通过，生成的五个文件与审阅快照逐字节一致。
- R2 恰好新增 `examples/rsi_workflows/` 下的 `deterministic.py`、两份 README、`tests/conftest.py`、`tests/test_runtime.py`。没有修改主线 Core、Registration、RunOwner、PN、Harness、终态读写器或既有 pipe fixture。
- 原冻结 R1 六源文件仍匹配旧 manifest；原 ZIP 仍为 `aa210453d56af6ae3ed1658b48edb490db9ee61ce0c1566a0633670dbee912cc`。本次 materialization 不包含旧 RRSI 全树，只能确认 R2 补丁未触及 RRSI，不能声称核验了完整旧 RRSI 字节。

## 原执行权威确实被复用

1. `examples/rsi_workflows/deterministic.py:160-188` 使用原 `Registration`、基础 operation lowerer 与 `IterationProfile`。三角色只返回普通 schema-valid 产品字节，没有 Registry handle、运行循环、scheduler、准入逻辑或模型预算计数器。
2. `tests/test_runtime.py:27-73` 以原 `compile_iteration_profile`、`start_run` 和 `RunOwner.admit/start/products/succeed` 建立并推进一份 Registry。`step` 只是点名某个 firing 的测试探针；原 `cpn/rpnh/run.py:472-478` 转交 `admit_module_firing`，后者在 `registry/module_execution.py:122-159` 核当前权威、PN 标记和 active claims。
3. `tests/test_runtime.py:248-277` 进入原 `Orchestrator.run`，它在 `cpn/orchestrator/runner.py:43-44` 直接调用原 `Harness.exact_execute`。原 Harness `schedule_ready`、completion 与终态流程不变。同步 Future 只提供测试 HOST 提交，不证明生产 worker 或跨线程 gateway。
4. 模型 cap 为 1，轮数仍由有限展开 PN 决定；12 次纯 operation 完成时原 returned-model-call 计数为 `(0, 0)`。没有把轮次或 dispatch 次数伪装成模型调用数，也没有解析真实模型 binding。

## 精确谱系与领域状态

- `deterministic.py:96-122`：candidate 绑定实际输入 state 的 resource ID/version；evaluation 绑定实际同轮 candidate、request、父 state，以及当前 evaluator operation binding 的完整引用。
- `deterministic.py:125-153`：selector 消费原 PN 提供的 incumbent/candidate/evaluation，检查轮次、候选引用、父 state 与 incumbent 值，并在输出中保存实际三输入引用、请求和 scorer binding。后轮 proposer 消费前轮 registered next。
- 独立三轮 `unknown → select → retain` 探针逐项核完整 reference 字典、跨轮 state、候选晋升后 retain 仍保留同一个 selected candidate、最终只读结果及 unchanged cut。
- domain `unknown` 只有空分数，正常 retain；known/null 或 unknown/non-null 分数被拒绝。角色异常、缺 evaluation、已发布但未 settled 的 evaluation 不产生正常 selector 输出。
- selector 产品已经发布但尚未 `succeed` 时仍无 terminal，重复 admit 不推进 event/checkpoint；settle 后才可发布终态。上述检查验证的是异常/未settled状态，不是 provider 的正式 `outcome_unknown` 协议实测。
- 本例信任已注册的纯 Python HOST。应用层内容检查与 Registry origin/claim 证明分工明确；它不提供恶意 scorer 沙箱、通用防泄漏或任意评价算法验证。

## 终态没有在 R2 修补

`deterministic.py:186-188` 显式提供 `terminal_outcomes`；R1 后继 `cpn/rpnh/iteration_profile.py:239-248` 把各映射直接编译为原 terminal config。R2 没有编译后 overlay，也没有调用 terminal callback 造结果。

原 `registry/module_terminal.py:242-270` 要求显式 complete/failed、settled checkpoint、无 provisional firing 和 active claims。独立测试确认正常 select、retain、unknown retain、协议 stop 可到 complete，显式 final_retain=failed 原样被 reader 识别为 failed。stop 是正常提前结束协议，不是 owner interruption；它不会启用后轮。

结果来自原 `read_run_execution/read_run_terminal_bytes`，不是 report/sink 文件。D0 reader 使用 `create=False, read_only=True` 的 Core，核结果字节、cut 不变、event/checkpoint 和 writer epoch 不变。修改或删除外部 report 文件不能代替 PN evaluation 或产生完成证据。

## 实际执行结果

- 独立复跑原无 socket D0：11 passed，4 deselected，179.05 秒。日志 `independent-d0.log`，JUnit `independent-d0.xml`。
- 独立新增 Registry 边界探针：5 passed，139.49 秒。源码 `independent_probes/test_reviewer_boundaries.py`，日志 `independent-probes.log`，JUnit `independent-probes.xml`。
- 独立复跑原 Orchestrator/Harness + 明确 pipe transport：4 passed，11 deselected，262.51 秒。日志 `independent-harness-pipe.log`，JUnit `independent-harness-pipe.xml`。

合计 20 次 focused 测试执行通过，其中 15 项为对作者用例的独立复跑，5 项是独立新增探针。没有把它们算作全仓通过，也没有并入作者自己的 165 项计数。

已审阅并保留作者 native 单项原始失败：`OwnerEventLoop` 创建 AF_UNIX socket 时 EPERM，1 failed，1.80 秒，HOST 尚未执行。其余 native 参数项没有因此取得 native 结果。独立审查未重复这一已明确的环境权限失败、未修改权限或 transport 安全设置。

作者首轮 `step-first.log/xml` 的 6 passed / 4 failed 也保留；四个失败是测试 helper 错读不存在的 `VerifiedResourceArtifact.payload`，最终快照使用原 object store 的 registered read。早期 `bind_completion` 派生试验不是最终 R2 方案。

## 验收边界与下一门槛

本次没有模型/API 调用、安装、登录、GitHub Actions、push 或发布。没有跑全仓测试。没有验收 registered-model/scripted port、provider submission uncertainty、owner stop/resume、真实进程或传输竞态、author/CAS、跨 child 编排与预算、旧 RRSI campaign 迁移，也没有模型改善结论。

在已有授权、支持 AF_UNIX 的正常本地环境，仍需按作者 `LOCAL_GATE_ZH.md` 补跑默认四项 native。必须保留当前 native 失败和 pipe 的 D0 标签。后续若变动五个 R2 文件或六个 R1 后继文件，需按新 hash 重验受影响边界。

## 证据文件

- `source-hash-audit.json`：999 个主线/fixture 源和 R1 后继的字节审计及五新增文件 hashes。
- `independent-patch-replay.json`：R2 patch check/apply 与最终字节对比。
- `old-r1-preserved-audit.json`：旧 R1 六文件和 ZIP 的实际核验。
- `r2-freeze-compare.json`：作者五文件与独立审阅快照对比。
- `independent-runtime-provenance.json`：实际 Python 3.12.14、pytest 8.4.2、jsonschema 4.26.0 及 import 路径。
- `author-evidence/`：作者首轮失败、native EPERM 与 R1 后继补丁选择证据，均标明来源，没有冒充独立运行。
