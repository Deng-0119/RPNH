# RPNH Harness 持续优化实施计划

版本：v2  
更新日期：2026-10-08 16:10 UTC  
用途：用户审阅、本地实施交接与逐包验收。此文件单独维护，不并入 GitHub 技术报告。  
代码基线：[Deng-0119/RPNH 674252f](https://github.com/Deng-0119/RPNH/commit/674252feb836f631c162979f177d1fe91f22559f)。当前后继 [ec9077e](https://github.com/Deng-0119/RPNH/commit/ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4) 仅发布证据勘误，产品代码基线不变。后续每包开工重新核对 main、差异与实际测试源码身份。

## 1 下一步与持续推进方式

**现有 owner 执行入口收敛与 TaskControl reader 迁移已同时推进。** 仓库已有 `cpn/orchestrator/runner.py::Orchestrator`，可复用它把 tool_pipeline 和 AgentTask 的机械驱动接到同一入口。只有实际缺口才扩展这个入口，不再并列新增另一套 runner。之后建立 parent firing 到 child launch/observation 的 Registry 接缝，再分包迁移跨阶段控制。

H1 已有 7 文件候选，最终测试和独立审阅进行中，尚未据此宣称合入或验收完成。H2a 已开始迁移 `TaskControl.result/_read_terminal_status`；AgentTask、其他 reader consumer 与 CLI/help/net/slash 另包。

Codex、DSH、OpenCode 新版适配已得到源码核验结论，待原生兼容认证；RPNH 命令审计与 RSI 接口设计已完成。RSI 可先做 inert profile 和单 Registry 的确定性 propose/evaluate/select，不等待完整 parent/child 接缝；RRSI 全 campaign 仍依赖 H7/H8a。仅覆盖 RSI on RPNH / RRSI，不混入 AATU。

持续节奏：

1. 每包确认基线、边界、受影响 consumer 和验收用例，完成最小实现与独立复核。
2. 交付冻结补丁、源码清单、精确命令与验收任务书给本地执行；回传前继续无依赖的下一包，回传后按真实证据复核。
3. 经授权上传或推送后，核对远端 commit 与文件字节。立即选择下一个依赖已满足的小包继续，不等用户再次催促。
4. 环境阻塞仅挂起对应测试或依赖该测试结论的发布范围。无依赖的开发、研究、离线验收继续推进。不得把未通过门槛标成完成。
5. 每次只更新新增完成项、重要阻塞和下一包。计划持续更新；对外技术报告只记已实现且有明确证据的能力和边界。

本文件是实施安排，不代表列出的未来验收已执行，也不单独授权模型、付费服务、远端写入或部署。

## 2 状态总览

状态含义：`completed` 为标明范围内完成；`partial_env` 为实现已交付但必需环境验收仍未闭合；`in_progress` 为当前实施或研究已在推进，未声称代码已合入；`planned` 为尚待实施。

| 编号 | 项目 | 状态 | 当前边界或下一交付 |
|---|---|---|---|
| C1 | ERP unknown 最小闭环 | completed | 已有原生证据；保留 unknown 阻断，不重新列为待开发 |
| C2 | 显式 tool_pipeline | completed | pure HOST、真实 PN、原生 owner 与只读重建已验证；不外推动态调用或外部写恢复 |
| C3 | Registry 原生 exact reader 与 HA、environment consumer | partial_env | 代码已推；有限原生验收通过；A 完整 targeted 仍为 241 passed / 1 failed |
| C4 | 共享 write_file 提示对齐 | completed | 本包有限契约范围完成；B focused 36 项及 text、structured 原生链通过 |
| E1 | XML 与快照证据勘误 | completed | ec9077e 证据-only 发布；历史测试事实与 PARTIAL_ENV 不变 |
| H1 | 薄化现有 owner 执行入口 | in_progress | 7 文件候选采用原 Orchestrator，最终测试与独立审阅中 |
| H2a | TaskControl result/status 读取 | in_progress | 作者已启动；仅 result/_read_terminal_status 与必要测试/说明 |
| H2b | 其余 exact reader consumer | planned | AgentTask、tool_pipeline、MainThread 与其他投影逐个迁移 |
| H3 | HA heuristic stop 收敛 | planned | 隐式语义停止改为诊断或明确注册策略；保留显式 deadline 与安全监督 |
| H4 | tool catalog 与模型可见契约一致性 | planned | 由实际声明派生 outcome/port、schema 指导；不加任务规则 |
| H5 | provider physical attempt 与 probe/retry 准入 | planned | 原 Registry 的完整谱系、预算与恢复；不可删恢复后宣布完成 |
| H6 | 动态有限 pure tool batch 的逐 call PN 准入 | planned | 补 typed scope、correlation、共享 capacity/capability 契约 |
| H7 | parent firing 到 child launch/observe 接缝 | planned | 通用 Registry 契约与真实进程证明，随后才迁移 outer controller |
| H8a | RRSI campaign 与嵌套 child 控制迁 PN | planned | 只迁依赖/准入，保留研究方法和纯计算 |
| H8b | SCB checkpoint outer controller 迁 PN | planned | 保留 Session、源码/snapshot 继承、原 PassPolicy/evaluator |
| H8c | ERP trial outer controller 迁 PN | planned | 保留真实 world、bridge、quiescence/freeze/grader 安全边界 |
| A1 | 新版 Codex、DSH、OpenCode 适配 | in_progress | 源码结论已核；Codex 契约修复、DSH codec 迁移、OpenCode 认证分包，native 待验 |
| A2 | RPNH 本体命令更新 | in_progress | 静态审计完成；现有读取收口由 H2a 承担，slash/help/net/UI 后包 |
| A3 | RSI on RPNH / RRSI 便利接口和配置 | in_progress | 设计完成待实现；R1 inert、R2 单 Registry、R3 native scripted 可先做，R4 campaign 依赖 H7/H8a |
| H9 | 其余前端重复胶水与组合回归 | planned | 按实际重复逐文件收敛；不另建 session 状态库 |

### 当前证据边界

- C1 集成于 [e92b05c](https://github.com/Deng-0119/RPNH/commit/e92b05c9afe324ebb675f2d67b73c02efe7b9536)。[ERP 验收出处](https://github.com/Deng-0119/RPNH/tree/e92b05c9afe324ebb675f2d67b73c02efe7b9536/evidence/first_wave/20261008/erp-unknown)支持离线 65 个唯一用例、installed-owner 生命周期与原生 managed receipt 的指定范围。真实 Odoo 业务成功不由这些合成 backend 验收推出。
- C2 集成于 [80a17c3](https://github.com/Deng-0119/RPNH/commit/80a17c3ce45ec3c7a1b39c170c36bbb922276de1)。[原生测试清单](https://github.com/Deng-0119/RPNH/blob/80a17c3ce45ec3c7a1b39c170c36bbb922276de1/evidence/first_wave/20261008/tool-pipeline/local/native-test-inventory.json)按唯一用例记录 22 项默认 native 与 10 项补充 native；镜像重跑不另加成新用例。
- C3/C4 集成于 [674252f](https://github.com/Deng-0119/RPNH/commit/674252feb836f631c162979f177d1fe91f22559f)。17 个最终源码、测试和文档文件与冻结 A14+B3 逐字一致。发布 commit 与实测的 `c545621 + 冻结 A → B` 身份分别保留。[共享包证据](https://github.com/Deng-0119/RPNH/tree/674252feb836f631c162979f177d1fe91f22559f/evidence/first_wave/20261008/shared-harness)包含 A targeted 241/1、B focused 36/0，另有共 6 个不重复 native testcase，其中 A 两项、B 四项；不把不同窗口累加为“全仓库通过”。
- A 的失败停在未改的 `read_host_config.py` 安全前置，未进入新 reader；后续 WSL 重试在启动阶段失败，pytest 没有开始。整体保留 `PARTIAL_ENV`。只在正常受支持、原有安全条件自然满足的本地环境补验，不能放宽守卫、改 owner/权限或换 transport 绕过。
- 历史 reproject/resume、OS 并发 writer 压力、真实 provider、Docker、Actions 和业务 benchmark/grader不在 C3/C4 已完成范围。E1 只修证据呈现和归属，不新增测试通过结论。

## 3 始终保持的架构边界

1. **Registry 负责 exact matching 与事实。** run/task/net/generation、工具注册、参数材料、claim、attempt、receipt、结果与终态一律按原生精确引用匹配。相同名字、相同内容、最新一条、stdout 或导出 JSON 不能替代身份。
2. **PN 负责可行性、依赖和语义准入。** 跨 operation/child 的 next step、join、资源争用、语义 stop/retry 应由当前 adopted net、marking、claims 与 registered policy 决定。能力不足时扩展原 RPNH 契约，不新增并行 scheduler、ready table、sidecar ledger 或第二 marking。
3. **纯计算继续使用普通代码。** 解析、算法、评分和领域 predicate 可在 atomic operation 内执行，生成具有 exact 输入谱系的 typed facts；PN 消费这些事实决定后继能否执行。布尔字段或显示上的“通过”本身不能发许可。
4. **保留合法 transport 与 physical pool。** HTTP/socket、进程、线程池、Future、取消与物理容量、外部安全 veto 可继续存在。它们不能创建 PN 许可，也不能以物理完成伪造业务 Success。
5. **外部 unknown 保持 unknown。** 断连、超时、丢 receipt 不证明外部动作没发生；probe 健康不洗白先前 unknown。保留 reconciliation、durable block 与必要安全关闭。
6. **不改 ERP/SCB 任务求解策略。** 不塞入 Odoo 字段特例、shell 修补、算法提示、评分捷径或自动参数修复。原业务 grader、来源 pin、实验 condition 与历史得分分开保留。
7. **读取不触发执行。** reader、inspect、reconnect、导出和 viewer 不创建 owner/grant，不补启 child，不重发 provider；受限 reader 仍遵守原授权入口。
8. **当前计划不承诺新恢复能力。** fresh、resume、reopen、unknown reconciliation 分开验收。只完成 fresh-run 不能声明 crash-recoverable 或 physical exactly-once。

## 4 优先序与依赖图

### 默认选包顺序

- 当前：H1 最终测试/独审与 H2a TaskControl 读取实现并行；E1 已发布；A1 源码结论和 A2/A3 完整设计用于下一小包。
- 第一批接续：完成 H2a，其他 reader consumer 逐个迁移；A1 的 Codex history/NoEffort 小修和 A2 的 slash 拒绝可独立排入；H3/H4 在不冲突时推进。A3 R1/R2 不需等待 H7。
- 第二批核心：H7 通用 parent/child 接缝；H5 provider 全谱系与 H6 pure batch typed contract 可并行设计、分别实施和验收。
- 第三批 consumer：H8a/H8b/H8c 依赖 H7 后逐包迁移。A1/A2/A3 中不依赖新增 core 能力的兼容性、只读检查与配置便利项提前做；依赖执行新能力的部分等对应 gate。
- 最后：H9 其余重复胶水、跨包组合边界与文档同步。回归随每包增量建设，不等最后才补。

### 必要依赖 DAG

```text
C2 显式工具 PN ──> H1 现有 owner runner ───────────────┐
C3 已实现 reader ─> H2a TaskControl result/status ────┼─> H7 parent/child 接缝
                  └> H2b AgentTask/tool_pipeline等   │       ├─> H8a RRSI
                                                     │       ├─> H8b SCB
                                                     └───────└─> H8c ERP
C1 ERP unknown ─────────────────────────────────────────────> H8c

H5 provider 契约 ─> v2/v3 transport 接线 ─> 注册 probe/retry 恢复闭环
H6 pure call typed contract ─> 逐 call PN/capacity ─> 重建与取消证明
H3 HA stop 与 H4 catalog 投影独立推进

A1 版本/协议审计 ─> 各 adapter 兼容包 ─> 必要的 H1/H2/H9 复用
A2 CLI 审计 ─────> 只读/配置命令小包；新执行命令依赖相应 core gate
A3 RSI 设计 ─────> R1 inert profile ─> R2 单 Registry PN/authoring ─> R3 native scripted
                  └> R4 完整 RRSI campaign 与 nested child 依赖 H7/H8a
```

H6 的 same-parent execution-child 与 H7 的独立 task/run child 是两种边界，不能用一个成功案例互相代替。H5 也不因 H7 通过而自动完成。任何包只有在需要某项能力时才依赖它，不人为要求全项目先重写。

## 5 分包实施卡

下文 D0、D1、D2、D3 的定义和统一交付门槛见第 6 节。每项测试均为待运行门槛，非已有 PASS。

### H1 复用现有 owner runner

**当前状态。** 已有 7 文件候选，最终测试和独立审阅进行中；本计划尚未将其记为合入或 native gate 通过。

**目标与落点。** 以 [Orchestrator](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/orchestrator/runner.py)、`cpn/rpnh/harness.py`、`cpn/components/execution_services.py` 为既有基础；迁移 `examples/tool_pipeline/run.py::make_harness/run_pipeline` 与 `cpn/rpnh/agent_tasks.py::_execute_agent_task` 中的机械驱动重复。先审清已有类已覆盖什么，仅补明确的 stop/结果或调用契约缺口。

- 共享入口借用调用者现有 owner、event_loop、prepare_dispatcher、worker submit 与 max_in_flight。不得新开 Registry/writer，不包含 task-specific next-step callback。
- provider/profile/plugin 工厂、业务 module 选择、signal 安装和私有输出格式留在原 composition root；不复制整段 `_execute_agent_task` 成另一工厂。
- event_loop、pool、input port 的创建者负责关闭。辅助入口不关闭借用资源；cleanup 失败不覆盖 primary error；显式停止继续交给原 Harness/owner。
- D0：参数/注入边界、借用资源不被关闭、异常保真、重复 stop 不创建新执行、无隐式 semantic timeout。凡构造真实 OwnerEventLoop 的 case 归 D1。
- D1：真实 AF_UNIX owner，pure HOST 与 scripted AgentTask 各一条；顺序、双 worker barrier、reject/异常、stop 与 terminal 竞态、unfinished worker drain。验证相同输入条件下的已声明结果/终态语义，不要求两个 fresh run 的随机 ID 相同。
- 完成定义：两类 consumer 真正调用同一现有入口，依旧只有原 Harness 准入；focused 回归与 native gate 有精确证据。若只是新增一层 wrapper 而原重复仍在，不算完成。
- 现有测试锚点：`examples/tool_pipeline/tests/test_pipeline.py`、`tests/test_harness_quiescence.py`、`tests/test_harness_resource_continuation.py`、`tests/test_task_frontend.py`；按实际受影响 node ID 选取。
- 额外授权：不需要真实模型；实际 provider、安装/更改运行环境或远端发布另按既有授权处理。

### H2 扩展共享 exact reader consumer

**落点。** 复用 [run_authority.py](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/rpnh/registry/run_authority.py) 的 `RunReadCut/read_run_execution` 与 ObjectStore 有界读取。H2a 已启动，首包只迁 `TaskControl.result`、`TaskControl._read_terminal_status` 与必要 imports/consumer limits、focused tests 和说明；H2b 以后另迁 `agent_tasks._terminal_output`、tool_pipeline 当前 terminal 选择，并逐个检查 `registry/main_thread.py`、AB `native.py`/drivers、RRSI `formal_execution.py` 与其他结果投影。

- consumer 保留自己的格式、bytes limit、JSON 语义和旧字段。expected ref 只作断言；禁止自己扫描“最后一条 terminal”补上结果。
- 同一读取使用同 core 的 cut；JSON decode、public dict 和 counts 构造后再核 H/E 与切面。status 保持 nonterminal/stopped 可读，历史 terminal/index 累计计数不能改成当前代的 0/1；`task_ref` 字符串和 result 的既有 public keys/output shape 保留。
- descriptor/body 显式设置本地 consumer bound（审计建议各 4 MiB，最终由本包确认），超限明确拒绝；不是默认调用 reader 就自动 bounded。status 之后若读 live owner socket，不宣称跨通道原子快照。覆盖 `_read_terminal_status` 被 resume 使用的兼容路径。
- AgentTask 的历史 `None` 交付语义与主动 current-state 查询另包明确，不混入 H2a。
- 仅读取路径不得通过 TaskControl 初始化触发 pending launch recovery；受限远程读继续经已有 read session/grant，不把 cut 当授权。
- D0：wrong full ref、错误 run/task/net、superseded generation、nonterminal、坏 index/result/provenance、bytes bound、cut/head/epoch 漂移、跨 core/kernel、失败无部分输出；改/删 sidecar 不改变当前结果和许可。
- D1：真实 owner + 另进程 read-only，resume/reopen 多代；并发 writer 时得到完整切面或明确读取失败，不混代；读前后 Registry 及 provider/launch 计数不变。历史材料缺失时记录该历史场景 NOT_RUN，可先用合成合法多代 fixture。
- 完成定义：按 consumer 单独完成，不以迁了一个声称全仓库统一；删除迁移 consumer 的重复终态 identity 链，保留只读呈现代码。H7 所需读取 consumer 在实际接入前各自达到 gate；H2a 不顺带重写 resume/reopen 或 UI 错误协议。
- 测试锚点：现有 reader/consumer 测试、`tests/test_main_thread_registry.py`、`tests/test_task_frontend.py`、tool_pipeline tests；不复刻 HA-only reader。
- 额外授权：无真实模型需求。C3 的环境补验独立保留，不改安全前置来消除历史失败。

### H3 HA heuristic stop 收敛

**落点。** [local_driver.py](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/examples/harnessaudit_office/src/rpnh_ha/local_driver.py) 的 `_Progress/_registry_progress/_wait_for_exit`、run condition 和相关 tests。

- 默认 replay 次数、无活动计数等只作诊断，不暗中终止合法长 operation 或 resource wait。
- 用户 stop、显式 max_seconds/deadline、进程所有权监督与物理安全 veto 保留，并记录来源。确需语义 stop 时形成版本化、正式注册的 policy/observation 与 owner 决策。
- D0：fake clock/process、重复 receipt、长运行、资源等待、deadline 只请求一次停止；terminal/stop 竞态不伪造成功。
- D1：真实进程树与 socket；leader 退出而 child 仍写时不能 freeze；startup 失败不能启动替代 run；SIGTERM fallback 不生成业务 terminal。
- 完成定义：没有隐藏的默认语义 stop，condition 版本和兼容行为清楚；quiescence 安全门槛未退化。
- 额外授权：真实 HA backend/freeze 属 D2，真实模型属 D3，均单列批准；无需这些动作完成纯 policy 改动的 D0/D1。

### H4 tool catalog 与可见契约对齐

**落点。** `cpn/components/agent_loop/optional_host_bindings.py`、`action_execution.py`、`workspace.py`、`request_envelope_materialization.py` 以及既有 schema validation。C4 已完成的直接 text 提示不重做。

- 从当前 compiled operation 的 outcomes/products/ports 派生模型可见 write 参数约束；合法 outcome/port 应联合表达。catalog、实际 request materialization 与 runtime 参数验证消费同一登记契约。
- 保留已支持的唯一 outcome/port 省略、symbolic name/internal ID、text/structured、exact source_ref 兼容；不凭提示替代硬边界，不自动修 JSON，不加 ERP/SCB 字段规则。
- 若统一 ManagedToolSelector override 与 plugin schema 方言/local-ref 初始化规则，另作小提交，明确旧合法字节/digest及不支持契约的拒绝范围。
- D0：合法/非法 pair、多产品、无产品 outcome、同 schema 多 role、跨节点隔离、全局 schema 不被修改、重工新声明、write 前预检、bad JSON 继续拒绝。
- D1：scripted provider 捕获真实 Registry recipe/catalog/request；比对材料化与重建字节，text 与 structured 各一条；历史 resume 保持历史契约。
- 完成定义：声明到 catalog 到 request 到 runtime 的同一契约证据完整，提示可解释但不授权。provider 对新 JSON Schema 组合关键字的兼容性单独标明。
- 额外授权：跨真实 provider smoke 需 D3 批准；不能为宣称 catalog 一致而自动调用模型。

### H5 provider 所有物理请求进入原 Registry 谱系

**落点。** `cpn/llm_adapters/external_provider.py`、`_external_provider_recovery.py`、既有 PrivateAttemptAudit；`cpn/rpnh/registry/_provider_calls/`、`provider_execution.py`、既有 ledger/gateway/schema；`registered_host_llm.py` 与 execution services。

分两个可复核子包：先设计并落地最小 typed contract/反例；再接 v2/v3 formal、probe、retry 的执行链。v3 现有 prior-attempt 限制和 one-use capability 必须显式处理，不能直接复用一个不支持该操作的 façade。

- 每次物理正式请求或 probe 均有独立 reservation/materialization/dispatch/permit/observed result，绑定 exact call、route/model、parent/predecessor 和 budget。一次获准 attempt 至多发一次。
- probe 是独立诊断 call，不能冒充 formal response；健康恢复不能清除 prior unknown。Registry 决定能否正式 retry，adapter 私有 counter 不发正向许可。
- 保留物理 duplicate safety 和日志诊断；删日志不能创造重发许可。预算区分 reservation、permit 与实际 send observation，不能从 permit 反推断电前必然已发送。
- 临时 single-physical 模式如确有必要，须显式 condition 和清楚的能力限制。旧恢复配置不能静默忽略；只有完整恢复被 Registry 准入并验收，才称 H5 完成。
- D0：v2/v3 predecessor、same-key 变参、stale owner/lease、route/model 漂移、预算、未许可 probe、wrong-attempt response、not_submitted/unknown/cancel 分类；复用 provider ledger 和 registered HOST tests。
- D1：本地 HTTP/process server 的 formal fail → 独立注册 probe → 获准 formal retry；partial send、半 body、429/503、丢 reply、重启、日志删除与 stop；每个实际请求对应先前唯一 permit，unknown 不自动重发。
- 完成定义：恢复功能仍可在获准条件下工作，全部 physical request 可追溯到 Registry；没有 adapter 隐式乘法预算或额外 probe。
- 额外授权：真实模型、外部 endpoint、费用/usage 对照为 D3；默认只使用本地 scripted transport。

### H6 动态有限 pure tool batch 的逐 call PN 准入

**落点。** `cpn/components/agent_loop/program_runner.py`、`managed_execution.py`，`cpn/plugins/managed_scheduler.py`、managed tool receipt/service，`cpn/rpnh/registry/execution_net.py` 及现有 PN/claim/resource 契约。

首包只支持一批已经登记完整 call 列表的有限 pure calls。保留既有工具身份、参数材料、single-dispatch、replay、observer 与 receipt 语义。现有 ExecutionNetDefinition 的 weighted 控制和 CAS 能力不等于现成的 typed tool/resource admission。

- 以 exact parent execution、call ID/ordinal、tool registration、argument material 绑定每个 child occurrence；A 的许可不能用于 B，旧 generation 不能重新领许可。
- run-wide/batch capacity 必须共享同一权威 scope。禁止每个 child 初始化一套“全 run slots”；physical pool 只执行 PN 已准入的工作。
- typed capability/resource/receipt 桥在原 Registry 和 PN 补齐，不能另加 policy_ok wrapper 或可独立修改的 parallel policy table。
- D0：缺依赖无 Start、错 parent/call/material、容量 1/2、跨 call correlation、重复 observer、terminal replay、取消未开始、成功 sibling 保留、清缓存重建不增加许可。
- D1：真实 managed workers 与 owner，sibling barrier 证明并行/串行上限，重启后的 child/receipt 不重复 dispatch，parent seal/closure 只接受同 scope 的既有事实。
- 完成定义：每个声称 PN-controlled 的 pure call，在 dispatch 前都有可重建的 exact child PN 准入证据，receipt 与 parent closure 精确闭合。
- 后续扩展另包：external effect-domain identity、read/read 与 edit/write 冲突、unknown-domain scope、公平性/顺序。不得伪造 resource slot 表示外部账号域；未知写阻断是否跨 operation 必须作为显式新契约。此扩展不与 pure 首包混称完成。
- 测试锚点：`test_registered_tool_program.py`、`test_managed_tool_scheduler.py`、`test_mixed_managed_admission.py`、`test_execution_net_registry.py`。
- 额外授权：本包纯工具、零模型；外部服务 effect 或真实 provider 不自动包含。

### H7 通用 parent firing 到 child launch 和 observation

**落点。** `cpn/rpnh/task_control.py`、`run.py`、原 task worker/launch authority 与 Registry facts；复用 H1/H2。MainThread 现有 main-turn link 是参考，不冒充任意 parent PN。

- parent 已准入 execution 登记 exact launch intent，绑定 firing/lease/start、child spec/definition/config digest、目标 run identity；原 TaskControl 执行获准的物理启动。
- 当前 `TaskControl.start` 接受 AgentTaskSpec，非通用 Module 重载，也无任意 caller idempotency key。任意 Module 支持与可重入 key 如需要，应明确扩展原入口和 versioned contract。
- 首批用一个 AgentTaskSpec child 与一个显式 Module child 证明接缝。没有 durable 幂等 launch 契约前，启动后 receipt 故障窗必须 unresolved，不能自动重试创建第二 child。
- child reader 输出 exact observation，经 registered HOST 发布 typed product，再由 parent PN 消费。exit 0、报告 complete、native terminal 与 world quiescent、business passed 分开。
- D0：无前置 token 不 launch、stale firing、same key 变 spec、错 child/generation、重复消费、伪报告/exit0、无真实 child terminal、parent 失败不改 child 终态。
- D1：真实 installed worker/owner socket；launch 前后 crash、receipt 丢失、parent 死/child 完、锁竞争、PID 复用、长 socket path、writer 断开、stop/terminal 竞态；核实际 child 启动和执行次数。
- 完成定义：通用接缝 native gate 通过，声明 fresh/recovery 支持范围，至少两类 child 使用同一 authority；再开放 H8 的生产迁移。不能把整个 imperative controller 塞入一个 HOST callback 作为完成。
- 额外授权：scripted 本地 child 足够 D1。真实 provider、远端 child、外部 runtime 分别批准。

### H8a RRSI campaign 与嵌套 child 迁 PN

**落点。** `examples/rrsi_v06/src/rpnh_rrsi/formal_campaign.py`、`formal_role.py`、`formal_policy.py`、`formal_execution.py`，沿用现有 Module/role loop 和 H7。

- 跨 calibration/evolve、analyst/proposer/gate/critic/trial、round/heldout 的依赖与停止由 parent PN 决定；候选排序、分数计算、apply_response 等纯算法留在 operation 内。
- analyst 下的 digester 等真实 child dispatch 同样经 H7；可用有限 request list 与注册 feedback/cursor 产品表达 more/done，不能仍在 callback 中同步启动整个列表。
- report、result sink、campaign_complete 变为只读结论或缓存；丢失它们不能补跑 provider。保留原任务、轮次、公式、模型配置、fresh child 和失败口径。
- D0：gate 拒绝无下游、缺 digester 不 complete、预算/重复消费/错 refs 拒绝、篡改报告不改准入；现有 formal campaign/role/policy/reporting/execution tests。
- D1：synthetic campaign、真实 parent-child/socket/scripted provider；nested child 启动后断开、children 之间 stop、完成与 stop 竞态，读结果零额外调用。
- 完成定义：跨 child 准入从可重建 PN 事实决定，研究方法未变，A3 的 campaign 便利入口使用同一执行链。
- 额外授权：真实研究模型、远端实验、扩大 task/round/repetition 或费用预算另行批准。

### H8b SCB checkpoint outer controller 迁 PN

**落点。** `examples/slopcodebench/pilot.py`、`contracts.py`、`run.py`、broker/plugin，使用 H7。

- 图覆盖 request/prepare → Session spawn/solver child → exact observation → quiescence → finish/snapshot → 原 evaluator → PassPolicy outcome → 下一 checkpoint/停止。
- CheckpointLedger 作为投影，不能按 JSON 数量/current_request 状态准入。Session 仍拥有源码继承和真实 snapshot，不能将其重命名为 native workspace revision。
- D0：跳 checkpoint、错 predecessor/digest、未来 prompt 泄漏、重复 finish、基础设施失败、原 policy continue/stop、删改 ledger 不改 enablement。
- D1：真实 broker/childworker + synthetic Session，丢 reply/receipt、未 quiescent、child stop；副作用最多一次，未满足门槛不 finish。
- D2：受批准的 upstream Session/container/snapshot/original evaluator scripted run，核验实际文件选择和继承；未运行保留 partial。
- 完成定义：outer 依赖真实由 PN 决定，Session/evaluator 安全与业务语义保持；finish 已发生但 receipt 丢失不被改写成安全重试。
- 额外授权：D2 环境启动及 D3 真实 checkpoint/model 前单独确认；不修改求解策略获取更高成绩。

### H8c ERP trial outer controller 迁 PN

**落点。** `examples/erp_bench/src/rpnh_erp_bench/driver.py`、`native.py`、`environment.py`、`sandbox.py` 与 bridge，保留 C1 成果。

- 图覆盖 prepare → solver child → 关闭 bridge 准入 → owner/backend quiescence join → freeze → 原 grade → cleanup 观测。OfficialWorld 保留真实资源状态和安全 veto，不能靠可编辑 phase 决定 next step。
- unknown 或 Registry 不可用时仍可进行必要物理安全关闭；不能伪造 Success token 解锁 cleanup，也不能以 finally 跑过等同清理成功。
- D0：缺 exact child/quiescent/freeze 不 grade，phase 篡改不越级、known failure 与 unknown 分列、导出失败不重做 world 动作；沿现有 driver/native/environment tests。
- D1：真实 bridge/managedworker，lost reply、partial send、late RPC、service 重建、进程强杀；freeze 前无活跃 writer，unknown 后无新增业务写。
- D2：受批准 Harbor/Docker/Odoo scripted run，验证 delayed Compose、background writer、freeze/grade/cleanup；保留原 grader 和安全测试。
- 完成定义：跨阶段依赖由 PN 决定，真实 world 安全门槛未弱化；C1 unknown 防线继续通过。模型业务得分不能代替架构 gate。
- 额外授权：真实世界/容器/模型各按实际作用范围批准，不默认整 trial retry 或回滚。

### A1 新版 Codex DSH OpenCode 适配

**核验状态。** 以下是 2026-10-08 的官方源码/协议与版本核验结论，未完成新版本安装态/native 认证。开工再次确认 exact pin，不浮动跟随 latest。来源已核，以下链接固定到本轮参照版本。

| 适配器 | 当前 RPNH 对照 | 已核目标 | 当前判断 |
|---|---|---|---|
| Codex | 0.155.0 | 0.161.0，2026-10-07；commit `979011409de0a60b52f179721948e65531d26144` | 先修既有 history/NoEffort 契约；抽查核心响应未见 Model/Thread/Turn required 集合变化，不能据此声称全协议兼容 |
| DSH | 0.1.6-alpha.2 | 0.2.1-alpha.1，2026-10-03；commit `5badb15009ae1756c3afe0ae0cef1faafc290ccc` | Session V3→V4 和 tool message shape 要实际迁移，不能只改 pin |
| OpenCode | 1.18.32 | 1.18.35，2026-10-06；tag commit `53d1eabb61e21162157817bf677da0a4ad3332e3` | SDK、plugin hooks、attach 和选定 TUI 关键文件 bytes 相同，优先认证窗口，不复制 backend |

实施顺序：先 A1a 的 NoEffort codec 和 truthful legacy history，再 A1c OpenCode 认证、A1d Codex 新版协商/诊断，最后 A1b DSH 格式迁移与原生认证。

来源：[Codex 0.161.0 release](https://github.com/openai/codex/releases/tag/rust-v0.161.0)、[DSH 0.2.1-alpha.1 release](https://github.com/deepseek-ai/deepseek-harness/releases/tag/dsh-v0.2.1-alpha.1)、[OpenCode 1.18.35 release](https://github.com/anomalyco/opencode/releases/tag/v1.18.35)。

**A1a Codex history 与 NoEffort。** `cpn/frontend/codex_app_server.py` 当前宣称 `historyMode=paginated`，却缺 `thread/turns/list` 和 `thread/items/list`。0.155/0.161 resume 都会使用这一路径，因此是既有缺口，不能统称新版 breaking change。首包保留全量 Registry-derived 投影，诚实声明 legacy 与相符 itemsView；原生 cold resume 使用至少两个已完成 turn 的冻结 fixture，零 provider/补偿操作。真正有界分页另包实现并测试稳定 cursor/correlation、空页和重复读，不先声称具备分页能力。

同文件的 `_model` 把 canonical NoEffort 投影为 `defaultReasoningEffort=null`，针对两个版本 schema 的纯投影探针均拒绝。添加按 selection 的窄 wire codec：仅 canonical effort=None 且 choices 为空的 profile 使用 wire `none`，回传恢复同一原 profile/config/canonical None；真正已配置 string `none` 的模型保持原 string，不能混同。model/list、thread/turn start、settings/config write 共用这一边界。保留 provider 参数与 exact effort，不硬改 medium，不写 catalog 或替换模型。D0 包含 schema、history、effort round-trip 与拒绝反例；D1 用真实本地 Codex resume/read-only，检查零额外执行。

**A1b DSH 版本化 DTO 与历史只读转换。** 具体范围：`integrations/dsh/src/capabilities.ts`、`projection.ts`、bridge/version metadata、`cpn/dsh/backend.py`、原 prepare/verify gates。新工具结果为 `role:tool`、top-level `toolCallId/isError/content`；旧格式是 user/tool-source 与嵌套 tool-result。使用按 exact revision 选择的显式 codec，保留 call correlation、grant、size bounds 与 no-replay；不能在 authority 边界笼统放宽成“任意两种都收”。

原 Registry 的 V3 header/events 保持不可变，只对 detached 读取投影转换为 V4。旧 active ticket/执行身份不能直接重标成新 revision；旧 interrupted run 使用其原受支持版本，或另行验证显式迁移。上游 factory seam 仍可沿用，现有锚点和纯变换检查并未显示需要重写整个 lifecycle。D0 验证 V3/V4 completed/denied/cancel history、错/缺/重复 call、版本混用、转换后 immutable bytes；D1 对两个支持版本 typecheck，并进行 genuine Cordis/headless 的零模型 lifecycle、cold read 和独立 scripted execution smoke。

DSH 来源：[发布与 exact commit](https://github.com/deepseek-ai/deepseek-harness/commit/5badb15009ae1756c3afe0ae0cef1faafc290ccc)、[新消息契约](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/llm/llm/src/message.ts)、[Session V4 契约](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/core/session/src/types.ts)。

**A1c OpenCode 兼容认证。** 在 1.18.35 exact tag 版本上跑已有 protocol/SDK/plugin/attach、TUI、cancel/reconnect、历史恢复回归；记录被核验文件集合与安装来源。源码关键部分相同只支持“小范围认证优先”的判断，不能代替实际测试。版本发布的 target_commitish 不等于 tag SHA，manifest 使用已核 tag commit。仅在实际差异被证明时修改 adapter。

**A1d Codex 0.161 协商与 subscription 诊断。** CLI probe 与 initialize 共同绑定 exact-version compatibility profile，明确 capability、notification opt-out 和 history mode；unsupported OAuth/process/tool 请求拒绝，不因新版 UI 增权限或要求登录。新可选字段不统称 breaking change。subscription `codex exec` 是独立模型传输边界，前端 native 通过不代表它通过；先只补无 secret preflight 分类、fake auth-store fixtures 与 JSONL fail-closed。保留现有 file auth 模式；keyring 支持另设计官方授权通道，不复制 token、更改 credential store 或 global CODEX_HOME 绕过限制。

**共同完成定义。** 每个适配器独立记录支持版本、旧版范围、源码核验、D0、D1、NOT_RUN；旧 PASS 不自动继承。复用原 Registry/PN/owner，不新增 backend。真实软件安装/升级、登录、持久凭证、真实订阅/API 模型、费用及外部请求分别核准；默认 fixture 和 zero-model native 先行。

### A2 RPNH 本体命令更新

**静态审计已完成。** 已梳理 installed `rpnh`、`cpn/rpnh_cli.py`、`cpn/cli.py`、现有 task/workflow/config/inspection、docs/help 与副作用矩阵。审计已确认 `TaskControl.result` 仍使用旧 authority/metadata 读取链，`_read_terminal_status` 同样缺共享 cut；net/result-evidence 的 mode/options 契约另包处理。优先完成这些现有入口的原生读取一致性，再新增 help、flag 或 feature。

后续具体小包：

- Basic 未知或 malformed slash 目前可落入模型/工具执行输入；增加本地 usage 拒绝，测试 `/task`、`/tasks extra`、`/resume extra` 与 typo，禁止 `_run_turn/message/launch`。普通文本语义保持，字面 `/` 如需支持须明确转义。
- help 与 net mode/options：已存在的 plugins、package environment/run、net preflight/result-evidence 应准确展示；无效的 no-open/host/port/max-count 组合要生效或在打开 Registry/listener 前拒绝，不能静默丢弃。examples verify 说明是 adapter_task batch-summary 检查，不冒称通用业务 verifier。
- UI 状态错误：另包给出无敏感路径的稳定 observation/error code 与 generation；不能将 EXITED 呈现为 terminal success，也不直接暴露内部 exception/path。
- 现有 `/tasks` 会 reconciliation main child links，可能写主 Registry 索引，不能称严格零写 observer；它不补启 child。新只读命令保证与原 reconciliation 功能分别验证。

源码依据：[Basic dispatch](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/rpnh_cli.py)、[TaskControl](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/rpnh/task_control.py)、[共享 UI application](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/rpnh/frontend_application.py)。

- 优先配合 H2 收口现有 result/status/net-result 的读取契约，再修 help/参数/错误码/输出契约；不能将 nonterminal status/net projection 强行转成 terminal-only 读取。配置模板、preflight/dry-run 应说明是否真正零执行。任何新增命令都绑定已存在且验收过的 core 能力。
- 统一命令侧 profile、exact model、run/task/refs 和显式 resume 语义；不擅自填凭证、默认模型、恢复 retry 或扩大预算。
- D0：解析、非法组合、help、稳定 JSON schema、退出码、路径/权限、无 secrets 回显、只读命令零 writer/provider/child launch。
- D1：非 editable 安装的实际 `rpnh` 入口，PATH/模块来源可核，native start/status/result/stop 在 scripted 条件下端到端验证；新执行命令依赖 H1/H2/H7 对应 gate。
- 完成定义：docs/help/实际行为一致，旧命令兼容或显式迁移说明；便利入口不成为新 authority。
- 额外授权：配置写入、安装、远端操作、真实模型与费用按具体命令另核，不因“CLI 更新”自动执行。

### A3 RSI on RPNH 与 RRSI 便利接口和配置

**设计状态。** 已完成当前代码/既有研究对照，拟新增 API/schema/CLI 尚未实现。先让便利层产生既有 Module、author revision 和 exact resource bindings，不建 RSI runner、评分 DB 或新调度权威。R1–R3 可独立推进，完整 RRSI 的 R4 才依赖 H7/H8a。

**现有能力与边界。** rrsi_v06 是 frozen fixture，`_validate_execution/_validate_method` 固定轮数/角色限制，不能直接作为通用参数配置。已有 role `init/model/act` 与 policy `prepare/model/grade` PN、用户自有 LLMExecutionSelection、独立 child Registry 可复用；campaign 与 nested Digester dispatch 仍是 imperative。当前 split 校验只保证 task ID 分离，calibration-positive 和 heldout-positive 都有 `raw=15`，不能宣传为样本内容完全不重叠。保留 heldout/export 角色可见性，新增通用 profile 要明确内容重叠检查与限制。

**已有 API。** `ModuleDeclaration.from_dict`、`compile_module`、`ClosedModuleAuthor.publish`、适用其契约时的 `GraphModuleAuthor.publish`、`RegistryRegistrationGateway.create_author_branch/advance_author_branch`、`PlainModuleMergeAnalyzer/Author`、`start_run`、`Orchestrator`、`read_run_execution/read_run_terminal_bytes`。branch advancement 复用 expected branch version、head revision、stream head 三个 exact CAS；它不是 Git 分支，也不等于 runtime adoption 或 score 晋升。当前没有 `start_run(author_ref=...)`，author→实际 run 的 exact linkage 需明确补齐，不能虚构已有参数。

**拟新增最小便利层。** 先在 example-local 实现 `load_iteration_profile(path, *, template_catalog)` 和 `compile_iteration_profile(profile, *, registration)`，返回 inert 构造材料；两个名字均为提案。模板只选择 trusted executor/schema keys，不允许 arbitrary import，不能带 ready queue、best_model 或 DB connection。使用原 compiler，不自动执行或选赢家。拟议独立 `examples/rsi_workflows/` 与原 RRSI frozen fixture 分开；`rpnh rsi` 尚不存在，先证明两种消费者复用后再考虑 A2 的根命令薄路由。

| 阶段 | 实施与依赖 | 离线和 native 完成定义 |
|---|---|---|
| R1 inert profile | 封闭 schema、role/resource/unit/split 预检，生成现有 Module；不依赖 H7 | 无 owner/socket/provider；经原 compiler；extra 字段、任意 import、无界循环、错误引用拒绝 |
| R2 单 Registry PN 与 authoring | deterministic `propose → evaluate → select`，有限候选与 feedback；复用现有 author/branch API | 缺 registered evaluation 不 select；改/删 report 不改 enablement；typed selection decision 精确绑定候选/数据/scorer；stale CAS 不覆盖分支；未知结果不晋升 |
| R3 native scripted | H1 共享入口 + 原 registered model boundary，真实 Registry/owner/socket、scripted input port | admission/request identity、stop/terminal、丢 reply 不自动重放、缓存丢失仍可 exact read；零真实 provider；不冒称模型改善 |
| R4 完整 RRSI | 依赖 H7/H8a，迁 campaign 与 nested Digester launch/collect、跨 child budget | child identity/receipt/crash window 可核，步骤准入来自 PN；不把整个旧 campaign 放进一个 HOST operation |
| R5 可选训练和真实模型 | 单独批准的最小实验，非 R1–R4 前置 | 固定模型/profile/data/scorer、范围/费用/环境；D2 training runtime 与 D3 provider 分列 |

R2 如某 case 构造真实 OwnerEventLoop，按 D1 记录，不因属于 R2 就标成无 socket 的 D0。纯 deterministic operation 内可用普通循环，incumbent/cursor/evaluation/decision 进入原 PN typed products；后继是否启动由 marking 决定。

teacher–student 仅是后续可选模板，训练不是所有 RSI 的前提。候选可为 prompt、代码、workflow、检索配置或模型工件。合并结构正确不代表候选性能更好，合并后未重新评估不能晋升；纯分数比较若决定运行，必须经过 registered decision/outcome。

**测试与交付。** 覆盖 profile round-trip、source/dataset/scorer/model exact 配对、预算单位、heldout body/trace 不进入 proposer可见输入、unknown成本不可比较、result 只读重建；沿原 formal protocol/method/role/policy/campaign/execution/reporting 与 author/merge tests 按边界选取。保留已有 `test_full_campaign_uses_real_child_runners_with_scripted_input_port` 作为 D1 测试起点，其 structural complete 不等于 production evidence complete。

来源：[冻结协议与 split](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/examples/rrsi_v06/src/rpnh_rrsi/formal_protocol.py)、[author publication](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/rpnh/collaboration/materials.py)、[branch gateway](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/rpnh/registry/registration_gateway.py)、[原 Module run API](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/rpnh/run.py)。

**额外授权。** 真实模型/benchmark、训练/GPU/远端服务、费用、扩大轮次/样本、候选代码自动应用或对外发表分别确认。当前先做零模型构造与本地验证，不混入 AATU。

### H9 其余胶水与组合验收

**落点。** Basic/Codex/OpenCode 主 session 入口、DSH backend/UI、AB/HA/RRSI 投影中尚存的重复 prepare/start/reconcile/abort。按真实重复证据逐一抽取，保留 MainThread conversation lineage、TaskControl manifest、UI inbox/RPC map、one-use ticket 和原 physical capacity。

- D0：篡改/删除 sidecar、wrong ticket/ref、只读 reconnect、terminal-stop 竞态、旧协议兼容。
- D1：本地实际 reconnect/abort、owner lease 互斥、无 token 不执行、零额外 provider call；各前端安装态覆盖分别记录。
- 完成定义：consumer 共用已验收接缝，没有新增 CommonSessionStateStore；按变更后的组合边界回归，不能只继承各旧包 PASS。
- 额外授权：真实外部 adapter/model 测试分别批准；不为代码整洁扩大业务范围。

## 6 验收与交付规则

### 分层 gate

| 层 | 验证范围 | 不能推出的结论 |
|---|---|---|
| D0 | 确定性离线：真实临时 Registry/schema、直接命令、可控 executor/clock/transport double | 不证明真实 socket、进程竞态或外部副作用 |
| D1 | 本地 native：实际 AF_UNIX/进程/worker/HTTP，scripted provider，零真实模型 | 不证明真实业务 backend、原 grader 或模型成绩 |
| D2 | 明确批准的真实 runtime：Session/容器/Odoo/snapshot/grader，仍可 scripted | 不等于模型求解成功，不回填旧实验成绩 |
| D3 | 明确批准的真实 provider/模型/付费集成，固定 profile/model/scope/budget | 不代替 D0/D1 故障注入，也不证明唯一 authority |

构造真实 OwnerEventLoop 的 case 属 D1，即使名称含 offline 或 provider 是 fake。保留既有日志名称，但新增报告按实际依赖分层。pipe/mock 结果不能记成 native；环境失败同时保留 pytest 原始 failed 和环境分类。

### 每包必须交付

1. 基线与实际测试身份：base commit、tested HEAD/tree、完整 working diff/untracked 清单、最终文件 hashes；发布 commit 另列，不倒写测试时身份。
2. 最小改动说明：唯一 authority、删掉的重复判定、保留的物理安全、schema/condition/协议兼容和明确未覆盖项。
3. 可复核测试：精确命令、完整 node ID、UTC 起止、exit code、stdout/stderr、合法 JUnit、实际解释器/包来源与 transport。
4. 分开记录 case verdict 与 process exit。同一测试多次运行不累加为独立用例；失败、未运行、环境阻塞不能隐藏或改成 PASS。
5. native evidence：真实 dispatch/worker/child 次数、exact refs、停止与未知状态、只读前后 head/epoch/bytes 或相应不变性；并说明证据只证明到哪一层。
6. 冻结交接包与独立复核；本地接收者按支持的文件传递流程取得实际可读字节再校验。无需反复补贴不能定位到源码的口头摘要。
7. 若授权发布，发布后核远端 exact commit/file identity 和相关 checks。未授权的 push、merge、release、deploy、Actions 不由本计划自动触发。

### 环境阻塞处理

C3 的安全前置失败单独保留。只有支持的正常会话满足原安全条件时，才跑原失败单项并保存新窗口；单项通过也不改旧 241/1。如需一个全绿完整 targeted 窗口，另运行原完整命令。没有 pytest 启动证据的 WSL attempt 不能记为测试运行。

后续任一包出现同类阻塞：保留已完成的 D0、准备 D1 fixtures 和交接包，标记 `partial_env`；继续依赖已满足的其他包。禁止修改权限/ownership、安全守卫、socket 实现或采用不获准替代 launcher 只为取得全绿。

## 7 当前执行与下一包选择

**H1 和 H2a 已并行推进。** H1 的 7 文件候选正在最终测试与独立审阅，继续核原 Orchestrator 接线、stop、资源所有权及 native gate。H2a 只收口 `TaskControl.result/_read_terminal_status`，保持 public shape、历史 counts、stopped/resume 和只读语义，consumer limits 与末端 cut recheck 明确验收。

任何一包冻结并交付本地后，立即推进下一个无冲突小包，不停等全部回传。下一选择优先为：Codex history/NoEffort 契约、Basic malformed slash 本地拒绝、H2b 单 consumer 迁移或 A3 R1 inert profile；依据当前文件占用与已准备测试选择，不将这几项混成一个大提交。DSH codec 和 H7 parent/child 作为各自独立核心包准备，OpenCode 先做精确版本认证。

每包继续遵守源码冻结、独立复核、本地 native、授权发布与远端 identity 核对。环境补验保持局部 `partial_env`，不阻塞独立开发；真实模型、付费、远端动作没有额外批准时保持暂停。后续报告仅增加已实现/已验证事实，计划单独更新。

## 8 固定源码索引

以下链接固定到计划基线，便于交接核对；实际实施应先核后续 main 差异。

- [现有 owner runner](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/orchestrator/runner.py)
- [Harness](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/rpnh/harness.py)
- [AgentTask 执行](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/rpnh/agent_tasks.py)
- [TaskControl](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/rpnh/task_control.py)
- [原生 execution reader](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/rpnh/registry/run_authority.py)
- [显式工具 PN 示例](https://github.com/Deng-0119/RPNH/tree/674252feb836f631c162979f177d1fe91f22559f/examples/tool_pipeline)
- [provider ledger 实现](https://github.com/Deng-0119/RPNH/tree/674252feb836f631c162979f177d1fe91f22559f/cpn/rpnh/registry/_provider_calls)
- [managed scheduler](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/plugins/managed_scheduler.py)
- [execution net Registry](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/rpnh/registry/execution_net.py)
- [RPNH CLI](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/rpnh_cli.py)
- [RRSI 当前实现](https://github.com/Deng-0119/RPNH/tree/674252feb836f631c162979f177d1fe91f22559f/examples/rrsi_v06)
- [DSH 当前集成](https://github.com/Deng-0119/RPNH/tree/674252feb836f631c162979f177d1fe91f22559f/integrations/dsh)
