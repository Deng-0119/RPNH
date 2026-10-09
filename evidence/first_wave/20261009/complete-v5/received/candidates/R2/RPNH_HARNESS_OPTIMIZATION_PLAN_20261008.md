# RPNH Harness 持续优化实施计划

版本：v3  
更新日期：2026-10-08 17:30 UTC  
用途：用户审阅、本地实施交接与逐包验收。此文件单独维护，不并入 GitHub 技术报告。  
当前主线：[Deng-0119/RPNH d92ff37](https://github.com/Deng-0119/RPNH/commit/d92ff3704b6002bf5ecbccb3e6a3d1489809a805)，父提交为 715468d；H2a 发布及 H1+H2a 组合回传已完成独立复核，166 unique/166 executions 全部通过，有限 gate 已闭合。H1 在 [715468d](https://github.com/Deng-0119/RPNH/commit/715468dab0b1bea07d7e94a7aa0606eaf194365c) 已发布并完成有限原生回传复核。历史研究基线为 [674252f](https://github.com/Deng-0119/RPNH/commit/674252feb836f631c162979f177d1fe91f22559f)，[ec9077e](https://github.com/Deng-0119/RPNH/commit/ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4) 是其证据-only 后继。各未合入包仍保留原实测基线；依赖 overlay 检查不冒称完整新 HEAD 测试。后续每包开工重新核对 main、差异与实际测试源码身份。

## R1 交付修订通知（2026-10-08 17:26 UTC）

**R1 v2 已修复并交付，旧包弃用，勿合入旧补丁。** R2 的真实 Registry 步进发现旧 R1 为 terminal 生成空 config，缺少原生终态发布要求的 `run_outcome`；17:06 UTC 曾通知暂缓。现在 profile 必填 `terminal_outcomes`，`stop/final_select/final_retain` 均显式填写 `complete` 或 `failed`，直接生成原 `TerminalBinding.config.run_outcome`，不在应用侧另补逻辑。

现行 v2 Library identity 为 `libfile_ef0078cd0f088191a87d3c5586d3f2fe`。未应用旧包选 v2 full；已应用且旧六文件 hashes 全匹配时仅选 v1→v2 delta，两条路径不能叠加。原 ZIP、六文件及 127 作者/27 独审的 inert/compiler 历史事实保留，不能外推其 terminal 运行兼容。v2 作者 150 项（141 compiler/schema + 9 真实 Registry 步进）全部通过，独审 33 pure + 3 真实 Registry 通过；均无 socket/模型/campaign，native transport 仍未验。v2 是交付修订号，未发布候选 schema/template 名称仍为 v1；旧 profile 缺 mapping 会明确拒绝，不宣称 wire 向后兼容或静默改写既有实验身份。

v2 实测基线仍为 715468d；新主线 d92ff37 改动了原 terminal reader，必须单列在实际新组合源码上的兼容复验，不能把旧窗口改写成已测新 HEAD。H2a、Codex effort codec 和 MainThread refs-only 原包不受旧 R1 terminal 缺陷影响；R2 只消费修订后的 R1。

## 1 下一步与持续推进方式

**H1 与 H2a 的有限组合原生 gate 已闭合，继续 Codex history 与 R2 接续包。** tool_pipeline 与 AgentTask 已复用原 `cpn/orchestrator/runner.py::Orchestrator`，七文件发布字节与冻结交付一致；本地 39 个唯一用例通过，真实 AF_UNIX、pipeline 因果链和另进程只读重建已核。此结论不新增通用 OS 崩溃恢复承诺。

H2a 五文件已发布至 d92ff37，与 H1 七文件均匹配冻结源码；实测为 `715468d + 冻结 H2a`，独立复核接受 166 unique/166 executions 的有限组合窗口及真实 native CLI/另进程回读。15 个 shared-reader 用例在汇总中漏写路径前缀，依原命令和源码规范后仍为 166，不需为该记录勘误重跑；旧 C3 `PARTIAL_ENV` 不被覆盖。AgentTask、tool_pipeline 等其他 reader consumer，以及 CLI/help/net/slash 继续逐包推进。

两个独立方向无需等待本地回传：一是 Codex 安全 history 投影及分页接线，二是 RSI R2 单 Registry 确定性 propose/evaluate/select 与 authoring 闭环。Codex effort codec 的十二文件窄修已冻结、独审并交付用户，仍 pin 0.155.0；MainThread 七文件 trusted-host refs-only frozen reader 前置包已交付用户，尚未成为公开正文/RPC。R1 v2 六文件修订已交付，替代有 terminal 契约遗漏的旧包；增加真实 Registry 步进验证仍不等于 native transport/campaign。R2 有限 D0/pipe 实现与独审已通过，交付包待完成，author/CAS 与 native gate 仍未闭合。Codex 安全 history 实现正在审查，并修复 stock 0.155 要求的 wire UUID threadId 映射，不改变 Registry 身份或 pin。

DSH/OpenCode 新版源码审计、RPNH 命令审计与 RSI 总体接口设计继续作为后续分包依据；新版 native 认证仍待验。完整 RRSI campaign 仍依赖 H7/H8a。仅覆盖 RSI on RPNH / RRSI，不混入 AATU。

持续节奏：

1. 每包确认基线、边界、受影响 consumer 和验收用例，完成最小实现与独立复核。
2. 交付冻结补丁、源码清单、精确命令与验收任务书给本地执行；回传前继续无依赖的下一包，回传后按真实证据复核。
3. 经授权上传或推送后，核对远端 commit 与文件字节。立即选择下一个依赖已满足的小包继续，不等用户再次催促。
4. 环境阻塞仅挂起对应测试或依赖该测试结论的发布范围。无依赖的开发、研究、离线验收继续推进。不得把未通过门槛标成完成。
5. 每次只更新新增完成项、重要阻塞和下一包。计划持续更新；对外技术报告只记已实现且有明确证据的能力和边界。

本文件是实施安排，不代表列出的未来验收已执行，也不单独授权模型、付费服务、远端写入或部署。

## 2 状态总览

状态含义：`completed` 为标明范围内完成；`partial_env` 为实现已交付但必需环境验收仍未闭合；`in_progress` 为实施、研究或回传复核正在推进，是否合入以该项明示为准；`planned` 为尚待实施。

| 编号 | 项目 | 状态 | 当前边界或下一交付 |
|---|---|---|---|
| C1 | ERP unknown 最小闭环 | completed | 已有原生证据；保留 unknown 阻断，不重新列为待开发 |
| C2 | 显式 tool_pipeline | completed | pure HOST、真实 PN、原生 owner 与只读重建已验证；不外推动态调用或外部写恢复 |
| C3 | Registry 原生 exact reader 与 HA、environment consumer | partial_env | 代码已推；有限原生验收通过；A 完整 targeted 仍为 241 passed / 1 failed |
| C4 | 共享 write_file 提示对齐 | completed | 本包有限契约范围完成；B focused 36 项及 text、structured 原生链通过 |
| E1 | XML 与快照证据勘误 | completed | ec9077e 证据-only 发布；历史测试事实与 PARTIAL_ENV 不变 |
| H1 | 薄化现有 owner 执行入口 | completed | 715468d 七文件身份吻合；39 unique native-window cases 通过，有限 PASS_NATIVE_FINITE |
| H2a | TaskControl result/status 读取 | completed | d92ff37 五文件及 H1 七文件冻结匹配；166 unique/166 executions 有限组合 native 窗口通过，旧 C3 PARTIAL_ENV 保留 |
| H2b | 其余 reader consumer 与 MainThread 前置 | in_progress | MainThread 七文件 refs-only fixed-cut 前置已交付、未合入；owner 安全文本/adapter 审查中，其他 consumer 待迁 |
| H3 | HA heuristic stop 收敛 | planned | 隐式语义停止改为诊断或明确注册策略；保留显式 deadline 与安全监督 |
| H4 | tool catalog 与模型可见契约一致性 | planned | 由实际声明派生 outcome/port、schema 指导；不加任务规则 |
| H5 | provider physical attempt 与 probe/retry 准入 | planned | 原 Registry 的完整谱系、预算与恢复；不可删恢复后宣布完成 |
| H6 | 动态有限 pure tool batch 的逐 call PN 准入 | planned | 补 typed scope、correlation、共享 capacity/capability 契约 |
| H7 | parent firing 到 child launch/observe 接缝 | planned | 通用 Registry 契约与真实进程证明，随后才迁移 outer controller |
| H8a | RRSI campaign 与嵌套 child 控制迁 PN | planned | 只迁依赖/准入，保留研究方法和纯计算 |
| H8b | SCB checkpoint outer controller 迁 PN | planned | 保留 Session、源码/snapshot 继承、原 PassPolicy/evaluator |
| H8c | ERP trial outer controller 迁 PN | planned | 保留真实 world、bridge、quiescence/freeze/grader 安全边界 |
| A1 | 新版 Codex、DSH、OpenCode 适配 | in_progress | Codex effort 已交付；history/UUID wire threadId 最小修审查中，pin 0.155.0；三端 native 认证未闭合 |
| A2 | RPNH 本体命令更新 | in_progress | 静态审计完成；现有读取收口由 H2a 承担，slash/help/net/UI 后包 |
| A3 | RSI on RPNH / RRSI 便利接口和配置 | in_progress | R1 v2 已交付，150 author 与 33+3 independent 通过；R2 15+5 有限 D0/pipe 通过待包；native/author-CAS/R3/R4 未闭合 |
| H9 | 其余前端重复胶水与组合回归 | planned | 按实际重复逐文件收敛；不另建 session 状态库 |

### 当前证据边界

- C1 集成于 [e92b05c](https://github.com/Deng-0119/RPNH/commit/e92b05c9afe324ebb675f2d67b73c02efe7b9536)。[ERP 验收出处](https://github.com/Deng-0119/RPNH/tree/e92b05c9afe324ebb675f2d67b73c02efe7b9536/evidence/first_wave/20261008/erp-unknown)支持离线 65 个唯一用例、installed-owner 生命周期与原生 managed receipt 的指定范围。真实 Odoo 业务成功不由这些合成 backend 验收推出。
- C2 集成于 [80a17c3](https://github.com/Deng-0119/RPNH/commit/80a17c3ce45ec3c7a1b39c170c36bbb922276de1)。[原生测试清单](https://github.com/Deng-0119/RPNH/blob/80a17c3ce45ec3c7a1b39c170c36bbb922276de1/evidence/first_wave/20261008/tool-pipeline/local/native-test-inventory.json)按唯一用例记录 22 项默认 native 与 10 项补充 native；镜像重跑不另加成新用例。
- C3/C4 集成于 [674252f](https://github.com/Deng-0119/RPNH/commit/674252feb836f631c162979f177d1fe91f22559f)。17 个最终源码、测试和文档文件与冻结 A14+B3 逐字一致。发布 commit 与实测的 `c545621 + 冻结 A → B` 身份分别保留。[共享包证据](https://github.com/Deng-0119/RPNH/tree/674252feb836f631c162979f177d1fe91f22559f/evidence/first_wave/20261008/shared-harness)包含 A targeted 241/1、B focused 36/0，另有共 6 个不重复 native testcase，其中 A 两项、B 四项；不把不同窗口累加为“全仓库通过”。
- A 的失败停在未改的 `read_host_config.py` 安全前置，未进入新 reader；后续 WSL 重试在启动阶段失败，pytest 没有开始。整体保留 `PARTIAL_ENV`。只在正常受支持、原有安全条件自然满足的本地环境补验，不能放宽守卫、改 owner/权限或换 transport 绕过。
- 历史 reproject/resume、OS 并发 writer 压力、真实 provider、Docker、Actions 和业务 benchmark/grader不在 C3/C4 已完成范围。E1 只修证据呈现和归属，不新增测试通过结论。
- H1 已发布于 [715468d](https://github.com/Deng-0119/RPNH/commit/715468dab0b1bea07d7e94a7aa0606eaf194365c)。实测身份为 `674252f + 冻结 H1`；经 evidence-only 的 ec9077e 到发布 tree 的源码身份链已复核。七文件字节一致，36 focused + 3 经典 AgentTask = 39 distinct/39 executions，无失败/错误/跳过；39 内含纯单元检查，不是 39 个 socket integration。原生窗口与早期 pipe/setup/collection 失败分开保留。[H1 发布证据](https://github.com/Deng-0119/RPNH/tree/715468dab0b1bea07d7e94a7aa0606eaf194365c/evidence/first_wave/20261009/owner-entry)含原始窗口与来源记录；目录的 20261009 对应本地 +08:00 日期，UTC 为 2026-10-08。
- H1 实际导出独立核得：1 complete terminal、12 个业务产物、2 个 source、10 个 PUBLISHED firing，exact input/output/claim/provenance 与因果次序闭合；最大 active 为 2，最后排空。原进程结束后另进程 readback 的 11 个稳定顶层字段相等，读前后均为 1,001 events、10 dispatch reservations、10 execution starts、model `[0,0]`。两个 transport/live-only 字段不参与等值。[CLI evidence](https://github.com/Deng-0119/RPNH/blob/715468dab0b1bea07d7e94a7aa0606eaf194365c/evidence/first_wave/20261009/owner-entry/native/cli/evidence.json)与 [readback evidence](https://github.com/Deng-0119/RPNH/blob/715468dab0b1bea07d7e94a7aa0606eaf194365c/evidence/first_wave/20261009/owner-entry/native/readback/evidence.json)支持该范围。
- H1 的三项经典 AgentTask case 覆盖真实 OS SIGINT 和显式 resume；early-stop 是回调注入。没有新增任意 OS 崩溃、所有 close 异常路径、构造前真实 SIGINT 或最终 OS handler identity 的全面证明。全 evidence whitespace 检查保留 exit 2；产品七文件与入口索引检查 exit 0。H1 gate 闭合不覆盖这些额外范围，也不改写 C3 `PARTIAL_ENV`。
- H2a 交付包为 `RPNH_H2a_TaskControl_Reader_Convergence_20261008.zip`，Library identity `libfile_f78fa191aac88191839ad3b69129ea73`。离线窗口分别为 focused 47、既有兼容 44、共享 reader 15 通过；独审窗口另列，批次有重叠，不累加为唯一用例。云端 native-status 的 6 pass/1 EPERM 和 native-resume 的 socket 前置阻塞保留。早先 715468d 上的五条路径仍为原 preimage，新测试不存在，这一历史检查保留；现在 H2a 已发布于 d92ff37，源码身份与组合原生回传已独立复核通过。实测为 `715468d + 冻结 H2a`，发布提交另列，5 个 H2a + 7 个 H1 文件字节吻合；166 unique/166 executions 全部通过，含 H1 39 个原用例。15 个 shared-reader ID 的汇总漏写 `examples/harnessaudit_office/` 前缀，按原命令与 exact tree 规范后总数不变，无需重跑。该非阻断记录勘误和早期环境失败保留，不覆盖旧 C3 `PARTIAL_ENV`。[H2a 发布证据](https://github.com/Deng-0119/RPNH/tree/d92ff3704b6002bf5ecbccb3e6a3d1489809a805/evidence/first_wave/20261009/taskcontrol-reader)支持有限组合结论。
- H1+H2a 组合的真实 native CLI/readback 已再核：1,001 events、10 firing、12 业务产物、最大 active=2，终态 publication 在 settle 后；原进程退出后另进程回读 11 个稳定字段相同，前后 1,001 events/10 dispatch reservations/10 execution starts/model `[0,0]` 不变。166 个窗口用例中包含 D0 检查，不等于 166 个 socket integration，也不推出任意 OS 恢复、全部 provider/业务 runtime 或全仓通过。
- Codex effort codec 十二文件冻结且独审：43 passed/1 deselected，另 4 个独立 probes 通过。仅一条生产文件修改，`historyMode=paginated` 及两条缺失 RPC 未改，pin/initialize gate 保持 0.155.0；0.161 schema/source 核验不等于新版 native 兼容。冻结包已于 17:00 UTC 交付用户，尚无本地安排/验收完成回传。
- MainThread 七文件前置包：独审 32 passed；作者 57 passed/9 deselected；715468d 的受影响 cpn 依赖 overlay 重跑同 57/9。重复窗口不加成 114 个用例，独审与作者集合也不直接相加。该前置包已交付，Library identity `libfile_73c6570eb84c8191a9dd1ea1d00850e0`，未合入；只证明 trusted-host refs-only fixed-cut/anchor 读取。独立的 owner 安全文本/Codex RPC 增量正在实现审查，stock cold resume 尚未取得 native 通过。
- R1 原包历史：44 profile + 83 compiler = 127 author passed；fresh patch replay 及 715468d 依赖 overlay 重跑同 127，另独审 27 static/compiler passed。17:00 UTC 曾交付；R2 随后发现 terminal 空 config 缺陷，原包已弃用，旧通过仅保留其实际静态范围。原 guard 失败、原 ZIP/source/hash 均不覆盖；不能把重跑计为 381 个唯一用例。
- R1 v2 当前：六文件显式 `terminal_outcomes` 修订已冻结并交付，full/delta 二选一；作者 150 passed = 141 pure compiler/schema + 9 real-Registry step，独审另 33 pure + 3 Registry passed。九项覆盖三种 mapping 各 complete/failed、旧空 config 控制组、未 settled 无 terminal、owner stop 无 terminal；独审还验证 selector settled 后 owner stop 不能被 complete mapping 覆盖。实测基础 715468d，新 d92ff37 reader 组合需另核；不宣称 AF_UNIX、物理 `outcome_unknown`、恢复或 campaign 通过。
- R2 当前：五个 example-local 新增文件，直接消费 R1 v2，不补 terminal overlay。作者 15 项 = 11 无 socket 真实 Registry D0 + 4 原 Orchestrator/Harness test-only pipe，独审复跑同 15 并新增 5 个 Registry probes 均通过。独审合计 20 次执行，其中 15 是复跑，不能与作者累加作 35 个用例。原 AF_UNIX 单项 EPERM 失败和初轮 helper 失败保留；native 四项仍需正常本地环境。交付包待完成，author→run/CAS、registered-model、owner stop/resume、物理 unknown、跨 child 与完整 campaign 均未完成。
- Codex history 当前：独立增量实现审核中。发现原 `ses_<hex>` threadId 不能被 stock 0.155 按 UUID 解析，正在 transport 边界增加合法 UUID wire 映射；原 Registry/thread/session 身份、授权来源和 0.155.0 pin 不变。修复、独审、stock native 验收未闭合，不预记 PASS。

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

- 当前：H1+H2a 的有限组合 gate 已闭合；d92ff37 作为后续包新的主线参照，R1/R2/Codex 各自新组合兼容性仍单列。Codex effort、MainThread refs-only 与 R1 v2 已交付，旧 R1 停用；各包接收、实测、合入分别记账。
- 第一批接续：Codex 安全 fixed-cut history/RPC/UUID wire 映射审查与 R2 有限 D0/pipe 包收口并行，不停在上一包验收完成。R2 authoring/CAS 和 native 仍为独立后续 gate。其他 H2b consumer、A2 slash 拒绝、H3/H4 按文件占用和 gate 单独排入；R2 不需等待 H7。
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

下文 D0、D1、D2、D3 的定义和统一交付门槛见第 6 节。测试条目描述各包验收要求；只有本节“当前状态”及第 2 节明确记录的窗口算已有证据，其余仍为待运行门槛，不能预记 PASS。

### H1 复用现有 owner runner

**当前状态。** 已合入 715468d，冻结七文件身份与发布源码相同，39 个唯一用例的本地原生窗口、真实 AF_UNIX 与另进程 readback 复核通过，有限 gate 为 `PASS_NATIVE_FINITE`。下列边界继续作为后续组合回归要求；未覆盖范围见第 2 节，不扩展为新的通用 OS crash-recovery 保证。

**已实现落点。** 以原 [Orchestrator](https://github.com/Deng-0119/RPNH/blob/715468dab0b1bea07d7e94a7aa0606eaf194365c/cpn/orchestrator/runner.py)、`cpn/rpnh/harness.py`、`cpn/components/execution_services.py` 为基础，已将 `examples/tool_pipeline/run.py::make_harness/run_pipeline` 与 `cpn/rpnh/agent_tasks.py::_execute_agent_task` 的机械驱动接到同一入口；stop 仅转交原 Harness。后续仅补已证实缺口，不新增第二 runner。

- 共享入口借用调用者现有 owner、event_loop、prepare_dispatcher、worker submit 与 max_in_flight。不得新开 Registry/writer，不包含 task-specific next-step callback。
- provider/profile/plugin 工厂、业务 module 选择、signal 安装和私有输出格式留在原 composition root；不复制整段 `_execute_agent_task` 成另一工厂。
- event_loop、pool、input port 的创建者负责关闭。辅助入口不关闭借用资源；cleanup 失败不覆盖 primary error；显式停止继续交给原 Harness/owner。
- D0：参数/注入边界、借用资源不被关闭、异常保真、重复 stop 不创建新执行、无隐式 semantic timeout。凡构造真实 OwnerEventLoop 的 case 归 D1。
- D1：真实 AF_UNIX owner，pure HOST 与 scripted AgentTask 各一条；顺序、双 worker barrier、reject/异常、stop 与 terminal 竞态、unfinished worker drain。验证相同输入条件下的已声明结果/终态语义，不要求两个 fresh run 的随机 ID 相同。
- 完成定义：两类 consumer 真正调用同一现有入口，依旧只有原 Harness 准入；focused 回归与 native gate 有精确证据。若只是新增一层 wrapper 而原重复仍在，不算完成。
- 现有测试锚点：`examples/tool_pipeline/tests/test_pipeline.py`、`tests/test_harness_quiescence.py`、`tests/test_harness_resource_continuation.py`、`tests/test_task_frontend.py`；按实际受影响 node ID 选取。
- 额外授权：不需要真实模型；实际 provider、安装/更改运行环境或远端发布另按既有授权处理。

### H2 扩展共享 exact reader consumer

**当前状态与落点。** H2a 五文件已冻结、离线独审通过并交付本地，现已发布于 d92ff37；五个 H2a 文件与七个 H1 文件冻结匹配，166 unique/166 executions 及实际 native CLI/readback 的有限组合 gate 已通过独立复核。复用 [run_authority.py](https://github.com/Deng-0119/RPNH/blob/674252feb836f631c162979f177d1fe91f22559f/cpn/rpnh/registry/run_authority.py) 的 `RunReadCut/read_run_execution`、`read_run_terminal_bytes` 与 ObjectStore 有界读取，已迁 `TaskControl.result`、`TaskControl._read_terminal_status`，另补 `_read_run_descriptor` 的 registered-size 默认物理边界、focused tests 和双语说明。H1+H2a 完成的是实际同组合原生窗口，不是继承 H1 单包 PASS；更广泛范围及旧 C3 环境缺口仍按原边界保留。

H2b 以后另迁 `agent_tasks._terminal_output`、tool_pipeline 当前 terminal 选择、AB `native.py`/drivers、RRSI `formal_execution.py` 与其他结果投影。MainThread fixed-cut 前置包单列如下，不以它的 refs-only 完成代替这些 run terminal consumer 迁移。

- consumer 保留自己的格式、bytes limit、JSON 语义和旧字段。expected ref 只作断言；禁止自己扫描“最后一条 terminal”补上结果。
- 同一读取使用同 core 的 cut；JSON decode、public dict 和 counts 构造后再核 H/E 与切面。status 保持 nonterminal/stopped 可读，历史 terminal/index 累计计数不能改成当前代的 0/1；`task_ref` 字符串和 result 的既有 public keys/output shape 保留。
- 冻结实现默认以每个对象登记的 size 作为 descriptor/body 的物理读取边界，不加入隐式 4 MiB 产品 cap；合法大于 4 MiB 对象仍可读。显式 caller descriptor budget 保留优先级，膨胀/缩短的 backing file 明确失败，不截断伪成功。这不承诺合法大对象或已材料化 metadata 的整体 CPU/RSS 上界。status 之后若读 live owner socket，不宣称跨通道原子快照。覆盖 `_read_terminal_status` 被 resume 使用的兼容路径。
- AgentTask 的历史 `None` 交付语义与主动 current-state 查询另包明确，不混入 H2a。
- 仅读取路径不得通过 TaskControl 初始化触发 pending launch recovery；受限远程读继续经已有 read session/grant，不把 cut 当授权。
- D0：wrong full ref、错误 run/task/net、superseded generation、nonterminal、坏 index/result/provenance、bytes bound、cut/head/epoch 漂移、跨 core/kernel、失败无部分输出；改/删 sidecar 不改变当前结果和许可。
- D1：真实 owner + 另进程 read-only，resume/reopen 多代；并发 writer 时得到完整切面或明确读取失败，不混代；读前后 Registry 及 provider/launch 计数不变。历史材料缺失时记录该历史场景 NOT_RUN，可先用合成合法多代 fixture。
- 完成定义：按 consumer 单独完成，不以迁了一个声称全仓库统一；删除迁移 consumer 的重复终态 identity 链，保留只读呈现代码。H7 所需读取 consumer 在实际接入前各自达到 gate；H2a 不顺带重写 resume/reopen 或 UI 错误协议。
- 测试锚点：现有 reader/consumer 测试、`tests/test_main_thread_registry.py`、`tests/test_task_frontend.py`、tool_pipeline tests；不复刻 HA-only reader。
- 额外授权：无真实模型需求。C3 的环境补验独立保留，不改安全前置来消除历史失败。

### H2b 前置 MainThread fixed-cut refs-only history

**当前状态。** 七文件冻结、独审并交付用户，未合入。生产修改仅 `registry/main_thread.py` 与新增 `registry/main_thread_history.py`，另含 focused tests 和四个双语参考文档。独审 32、作者 57/9、715468d 受影响 cpn 依赖 overlay 57/9 的口径见第 2 节；不是完整当前 HEAD 或 native frontend 验收。

- 已有接口 `capture_read_cut`、`project_thread_at`、`page_turns_at`、`page_items_at` 返回 source-bound 固定切面、committed turn/字段 exact refs 与 immutable child-link facts。cut 绑定 task/branch/ordinal/boundary event/exact thread，不以裸 CanonicalView 或 ambient positive memo 代替历史可见性验证。
- 每页 anchor 绑定 cut/query/order/filter/turn/slot，inclusive 首锚与 exclusive continuation 分明；未来/provisional 数据不进入旧 cut。旧 `project_current_thread/recover_thread` 字典形状和原 display 语义保留。
- 这是 trusted-host 接口。cut/anchor 不授予正文读取、用户权限或远程 capability；当前 catalog 没有可直接公开的 main-thread body reader。raw user_input 可含 native_plugins 配置，answer 可含 task.prompt；不能把 JSON 原样送给客户端。
- 登记 size 是每对象默认物理边界，可选 `max_object_bytes` 只可整体拒绝，不能截断或扩大边界。每页仍校验整个 lineage；返回最多 100 entries 不等于扫描、CPU/RSS 有界。既有 child path/symlink 安全检查仍对当前 filesystem 生效，但不打开 child Registry。
- 后继包正沿既有本地 owner MainSession/display 权限建立安全 fixed-cut 文本投影与 Codex wire cursor/RPC。缺通用 TypedReaderCatalog 的 main-turn 类型不等于本地 owner 无权显示自己的会话；不新增 grant/token/第二授权通道。私有 socket 与单一预绑定 session 是既有访问边界，cut 仍不是授权。逐请求及 send lock 内复核原 root/source/lease；实时 TaskControl annotation 与 immutable snapshot 分开。权限确实失效时拒绝，不以旧 cut 豁免。

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

实施顺序：A1a 已冻结 effort codec 已交付；紧接 MainThread 前置包完成 Codex 安全 history 投影及完整分页契约，再做 A1c OpenCode 认证、A1d Codex 新版协商/诊断，A1b DSH 格式迁移与原生认证独立准备。不存在 native 证据前不改支持版本或降低 history 声明掩盖缺口。

来源：[Codex 0.161.0 release](https://github.com/openai/codex/releases/tag/rust-v0.161.0)、[DSH 0.2.1-alpha.1 release](https://github.com/deepseek-ai/deepseek-harness/releases/tag/dsh-v0.2.1-alpha.1)、[OpenCode 1.18.35 release](https://github.com/anomalyco/opencode/releases/tag/v1.18.35)。

**A1a1 已冻结 Codex effort codec。** 十二文件窄修与独审已完成，43 passed/1 deselected，另 4 个独立 probes 通过；17:00 UTC 已交付用户，本地 native 验收回传未到。仅 canonical effort/default 都为 None 且 choices 为空的 selection 出站使用 wire `none`，入站恢复同一原 profile/config/canonical None；真实已配置 string `none` 仍通过原 physical variant 选择。model/list、config/read、thread/start/resume 与入站 thread/turn start、settings/config write 复用窄 codec，保留 exact selection/provider/model/adapter identity。不硬改 medium，不伪造 choices，不写 catalog、不替换模型。

0.155.0 binary/initialize pin 和 history 声明均未改。0.161 schema 能接受新投影只是静态合同证据，0.161 initialize 仍拒绝。D1 待使用已授权、已预置的 stock 0.155.0 与真实 AF_UNIX：三类 selection（canonical None、真实 none 默认、真实 none 非默认）互切、config round-trip、空 Registry 冷重开；建立 fail-closed guard/counters，provider/model/probe/worker/补偿为零。已 deselect 的 transport 单项须单独补验；它使用替代前端进程，不能单独证明 stock TUI 接收。不得为了此 gate 安装、登录或放宽 pin。

**A1a2 Codex 安全 history 投影与分页实现审核中。** 主线的 `historyMode=paginated` 缺 `thread/turns/list` 和 `thread/items/list`；独立增量正在补齐并审查，0.155/0.161 resume 均涉及该路径。H2b MainThread refs-only reader 与 effort codec 是明确 overlay 前置，未混称主线已发布。另发现 stock 0.155 无法按 UUID 解析原 `ses_<hex>` threadId，当前做 transport-only UUID 映射最小修，不改 Registry/session exact identity、不换 pin。以下要求仍须按最终字节验收：

- 复用原本地 owner 的私有 socket/单 MainSession/display 权限，仅呈现原 user text、reply 与 protocol_valid 所需提示。不得暴露 plugin config、private task prompt/instructions、child answer/decision/receipt。通用 reader catalog 不变；不新增 grant。原 source/root/lease 在读取前与持有 send lock 的实际发送前重核，close/换源/lease 失效拒绝；不宣称能抵御同 OS 权限的任意 ABA/TOCTOU 攻击。
- `thread/resume` 共用一次 cut，turns/items 的初始 cursor 为该 cut 的 inclusive anchor，后续为 exclusive；不能让两个 cursor-null 请求分别捕获不同 head。transport cursor 严格有界解析并绑定 source、会话权限、query、order、itemsView 与 turn filter，不含任意 filesystem path、不建 cursorstore。native 默认 asc 与 Codex turns 默认 desc 分开处理。
- 按两版官方 schema 精确实现 notLoaded 的空 items、summary/full、filter、limit、nextCursor 和空页。稳定 exact turn/item identity 需兼容 live accepted/running 通知与 committed history，terminal 不重复显示；实时 TaskControl annotation 不混入 frozen page。
- D0 使用至少两个 committed turns 的冻结 Registry 验证完整 hydration、追加后的旧页稳定、错 cut/source/filter、权限变更拒绝、空页/重复读、notification/history correlation、私有字段过滤；原 Registry bytes/head/epoch 不变，零 launch/reconcile/provider/恢复补偿。
- UUID wire threadId 需在 thread/start/read/resume、通知、cursor 和 live/cold history 一致映射；原 `ses_<hex>` 与 exact Registry ref 不改写，不接受客户端随意选择 filesystem source。验证合法 UUID parse、稳定 round-trip、跨 session 拒绝和 notification/history 去重；通过 JSON schema 不等于 stock consumer 已接收。
- 纯 history 读取不进入 active-turn observation/tracking/reconcile。`thread/resume` 原本还组合 live reconnect/tracking，保留其既有生命周期并分别记账，不把整个 resume 宣称为永久零状态变化。
- D1 再以 stock 0.155.0 做有历史 cold resume 与只读分页；0.161 认证归 A1d，不能继承。legacy/full-history 如需临时兼容，须证明实际客户端契约与 itemsView 一致并明确范围，不用改标签代替缺失 RPC/真实验收。

**A1b DSH 版本化 DTO 与历史只读转换。** 具体范围：`integrations/dsh/src/capabilities.ts`、`projection.ts`、bridge/version metadata、`cpn/dsh/backend.py`、原 prepare/verify gates。新工具结果为 `role:tool`、top-level `toolCallId/isError/content`；旧格式是 user/tool-source 与嵌套 tool-result。使用按 exact revision 选择的显式 codec，保留 call correlation、grant、size bounds 与 no-replay；不能在 authority 边界笼统放宽成“任意两种都收”。

原 Registry 的 V3 header/events 保持不可变，只对 detached 读取投影转换为 V4。旧 active ticket/执行身份不能直接重标成新 revision；旧 interrupted run 使用其原受支持版本，或另行验证显式迁移。上游 factory seam 仍可沿用，现有锚点和纯变换检查并未显示需要重写整个 lifecycle。D0 验证 V3/V4 completed/denied/cancel history、错/缺/重复 call、版本混用、转换后 immutable bytes；D1 对两个支持版本 typecheck，并进行 genuine Cordis/headless 的零模型 lifecycle、cold read 和独立 scripted execution smoke。

DSH 来源：[发布与 exact commit](https://github.com/deepseek-ai/deepseek-harness/commit/5badb15009ae1756c3afe0ae0cef1faafc290ccc)、[新消息契约](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/llm/llm/src/message.ts)、[Session V4 契约](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/core/session/src/types.ts)。

**A1c OpenCode 兼容认证。** 在 1.18.35 exact tag 版本上跑已有 protocol/SDK/plugin/attach、TUI、cancel/reconnect、历史恢复回归；记录被核验文件集合与安装来源。源码关键部分相同只支持“小范围认证优先”的判断，不能代替实际测试。版本发布的 target_commitish 不等于 tag SHA，manifest 使用已核 tag commit。仅在实际差异被证明时修改 adapter。

**A1d Codex 0.161 协商与 subscription 诊断。** CLI probe 与 initialize 共同绑定 exact-version compatibility profile，明确 capability、notification opt-out 和 history mode；unsupported OAuth/process/tool 请求拒绝，不因新版 UI 增权限或要求登录。新可选字段不统称 breaking change。subscription `codex exec` 是独立模型传输边界，前端 native 通过不代表它通过；先只补无 secret preflight 分类、fake auth-store fixtures 与 JSONL fail-closed。保留现有 file auth 模式；keyring 支持另设计官方授权通道，不复制 token、更改 credential store 或 global CODEX_HOME 绕过限制。

**共同完成定义。** 每个适配器独立记录支持版本、旧版范围、源码核验、D0、D1、NOT_RUN；旧 PASS 不自动继承。复用原 Registry/PN/owner，不新增 backend。真实软件安装/升级、登录、持久凭证、真实订阅/API 模型、费用及外部请求分别核准；默认 fixture 和 zero-model native 先行。

### A2 RPNH 本体命令更新

**静态审计已完成。** 已梳理 installed `rpnh`、`cpn/rpnh_cli.py`、`cpn/cli.py`、现有 task/workflow/config/inspection、docs/help 与副作用矩阵。审计发现的 `TaskControl.result/_read_terminal_status` 旧读取链已由 H2a 修复并发布至 d92ff37，有限组合原生 gate 已通过；net/result-evidence 的 mode/options 契约另包处理。优先闭合现有读取一致性，同时推进无冲突的 help、flag 或 parser 小包。

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

**当前状态。** 总体设计完成；R1 v2 六新增文件已修正 terminal 契约、冻结、独审并交付用户，旧包弃用，尚未合入。profile API 仍仅产生既有 Module/compiler/预算构造材料；真实 Registry 步进新增证据不把 API 变为 runner。R2 单 Registry 确定性示例的有限 D0/pipe 已通过作者与独审，包待完成；author revision→run/CAS 和 native 尚未闭合，R3 另补 scripted native；完整 RRSI 的 R4 才依赖 H7/H8a。不建 RSI runner、评分 DB 或新调度权威。

**现有能力与边界。** rrsi_v06 是 frozen fixture，`_validate_execution/_validate_method` 固定轮数/角色限制，不能直接作为通用参数配置。已有 role `init/model/act` 与 policy `prepare/model/grade` PN、用户自有 LLMExecutionSelection、独立 child Registry 可复用；campaign 与 nested Digester dispatch 仍是 imperative。当前 split 校验只保证 task ID 分离，calibration-positive 和 heldout-positive 都有 `raw=15`，不能宣传为样本内容完全不重叠。保留 heldout/export 角色可见性，新增通用 profile 要明确内容重叠检查与限制。

**已有 API。** `ModuleDeclaration.from_dict`、`compile_module`、`ClosedModuleAuthor.publish`、适用其契约时的 `GraphModuleAuthor.publish`、`RegistryRegistrationGateway.create_author_branch/advance_author_branch`、`PlainModuleMergeAnalyzer/Author`、`start_run`、`Orchestrator`、`read_run_execution/read_run_terminal_bytes`。branch advancement 复用 expected branch version、head revision、stream head 三个 exact CAS；它不是 Git 分支，也不等于 runtime adoption 或 score 晋升。当前没有 `start_run(author_ref=...)`，author→实际 run 的 exact linkage 需明确补齐，不能虚构已有参数。

**R1 v2 冻结实现。** 实际落点为 `cpn/rpnh/iteration_profile.py`、optional schema `cpn/schemas/rpnh/iteration_profile.v1.schema.json`、`cpn/examples/iteration_profile/` 的例子及双语 README、`tests/test_iteration_profile.py`，共六新增文件，既有产品文件零修改。实际 API 为 `load_iteration_profile(path)` 与 `compile_iteration_profile(profile, *, registration)`；R1 未采用早期提案的 template_catalog 参数；后续 `examples/rsi_workflows/` 路径已由独立 R2 五文件示例实现，不混入 R1 六文件范围。

- 固定可信 `propose_evaluate_select/v1` 模板，1–32 轮编译期展开，走原 `ModuleDeclaration/compile_module` 与 caller 的 Registration。只允许既有 basic lowerer 和合法 schema/HOST keys，不接受 arbitrary import 或远程 schema refs。
- `PreparedIteration` 包含 Module、原 compiler 输出、预算材料、诊断 source map、entry schema 与未解析 model-profile binding 要求；无 writer/run/publish/status/ready queue/runtime round/best_model。source map 不是 registered author provenance；profile JSON 冻结不改变原 Module/compiled 容器的浅可变契约。
- 预算语义已独审纠正：`native_model_call_budget` 只用 `registered_model_call` 单位和显式正整数，保留原 returned-model-call accounting，独立于 rounds；拒绝旧 `attempt_budget/operation_attempt`。role bucket `max_attempts=null`，轮数/operation 进度上界来自有限 PN 拓扑，不冒充任意 firing/physical attempt/美元预算。没有 request_port/provider operation，model_profile_ref 仍是未解决的 HOST binding 义务。
- v2 必填 `terminal_outcomes={stop,final_select,final_retain}`，值各为原生 `complete|failed`；原样写入既有 `TerminalBinding.config.run_outcome`，terminal HOST schema 必须接受这一契约。stop 映射适用于每轮，包括末轮；中间 select/retain 仍产生 next，不提前终态。旧 profile 缺字段 fail closed，不静默猜测成功或兼容。
- 原包 127 author/27 independent 及其重复 replay/overlay 是历史 inert/compiler 证据，未覆盖 terminal publication；现行 v2 作者 150（141 pure + 9 Registry）、独审 33 pure + 3 Registry 单列。真实 Registry 步进验证 mapping、settlement、read-only exact terminal 与 owner stop，但没有 socket/Orchestrator/模型/campaign，物理 persisted `outcome_unknown` 未测。
- full patch 与旧包 delta 只能二选一；两路径产生相同六文件 final hashes。实测基线 715468d，最新 d92ff37 reader 组合须单列复验；caller schemas/scorer/data/model 业务有效性、split 隔离/无泄漏及完整运行准入仍待后续包。

R1 没有 `rpnh rsi` CLI，也未修改 rrsi_v06 frozen fixture。以后至少用两种真实 consumer 证明便利层复用，再考虑 A2 的根命令薄路由。

| 阶段 | 实施与依赖 | 离线和 native 完成定义 |
|---|---|---|
| R1 inert profile | v2 六文件冻结已交付，旧包弃用；显式 terminal mapping，未合入；不依赖 H7 | compiler 与无 socket Registry terminal 接缝已验；不证明 native transport、split 权限或全 runtime |
| R2 单 Registry PN 与 authoring | 有限 deterministic 示例 D0/pipe 已验，包待；author→run/branch CAS 未完成 | 缺 settled evaluation 不 select、exact 配对、report 无权威已验；native/CAS 门槛及未知成本比较仍后续 |
| R3 native scripted | H1 共享入口 + 原 registered model boundary，真实 Registry/owner/socket、scripted input port | admission/request identity、stop/terminal、丢 reply 不自动重放、缓存丢失仍可 exact read；零真实 provider；不冒称模型改善 |
| R4 完整 RRSI | 依赖 H7/H8a，迁 campaign 与 nested Digester launch/collect、跨 child budget | child identity/receipt/crash window 可核，步骤准入来自 PN；不把整个旧 campaign 放进一个 HOST operation |
| R5 可选训练和真实模型 | 单独批准的最小实验，非 R1–R4 前置 | 固定模型/profile/data/scorer、范围/费用/环境；D2 training runtime 与 D3 provider 分列 |

**R2 当前实施的闭环边界。** 在同一 Registry 内运行有限 deterministic proposer/evaluator/selector，沿 R1 声明与既有 owner/author/branch API；不需要 H7，不启动真实模型或独立 child。已实现原 `RunOwner.admit/start/products/succeed` 的无 socket Registry 步进；11 项 D0 与原 H1/Orchestrator+test-only pipe 的 4 项通过，独审复跑并新增 5 probes 通过。pipe 仍归 D0；真实 AF_UNIX 单项 EPERM 原样保留，本地默认四项 D1 尚未通过。五新增文件只在 `examples/rsi_workflows/`，复用 R1 v2，不添加 terminal overlay。后续完整路线仍为：明确 candidate/evaluation request/result/selection decision 的 typed 契约与 exact resource binding；用原 admitted operation 发布事实；由 marking/outcome 驱动有限反馈与终态；最后用同条件 registered decision 核验与三个 exact CAS 推进获准的 author branch。当前有限示例尚未实现/验收最后的 author→run linkage 与 branch CAS，不能以 15/5 通过宣称整个 R2 路线图完成。

缺 settled evaluation、不配对的候选/data/scorer/condition、未知可比成本、stale CAS 必须拒绝候选晋升；领域 evaluation `unknown` 可按本示例显式协议 retain 有效 incumbent，不能升候选，也不是物理执行 unknown 的恢复许可。删改 report/source map/cache 不改变 enablement，也不触发补执行。`select/retain/stop` 的分数函数保留普通代码，授权下一步的是 Registry 中该函数的 typed outcome。author revision、candidate artifact、runtime adoption 分开；branch advancement 不自动改当前 run。author→validated Module→实际 run 的 exact linkage 必须补齐证据，不虚构 `start_run(author_ref=...)`。

R2 如某 case 构造真实 OwnerEventLoop，按 D1 记录，不因属于 R2 就标成无 socket 的 D0。纯 deterministic operation 内可用普通循环，incumbent/cursor/evaluation/decision 进入原 PN typed products；后继是否启动由 marking 决定。R3 仍单列 registered-model/scripted input port、stop/terminal、丢 reply、缓存丢失与只读重建的原生范围，R2 通过不自动覆盖它。

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

**H1+H2a 有限组合验收已完成。** 当前 main 为 d92ff37（父 715468d）。五个 H2a 和七个 H1 文件与冻结交付相同，实测工作树 `715468d + H2a` 的前后源码身份一致；166 unique/166 executions 全部通过，实际 native CLI/独立进程 readback 已核。shared-reader 十五个汇总 ID 的路径前缀勘误不改变测试集合/数量，不要求重跑。继续保留原失败、旧 C3 `PARTIAL_ENV` 和未覆盖范围；不把有限完成外推为全仓或新恢复能力。

**交付与下一包并行。** Codex effort 与旧 R1 曾于 17:00 UTC 交付；旧 R1 随后因 terminal 缺陷暂缓，现已由交付的 R1 v2 替换，full/delta 二选一。MainThread refs-only 包亦已交付。接收、验收、合入分别记录，不把旧发送重复当新进度；R2 有限 D0/pipe 交付包收口与 Codex history/UUID 修复审查继续进行。d92ff37 变更原 reader 后的组合兼容性另验，不改写 R1/R2 原 715468d 实测身份。

接续重点：

1. **Codex 安全 history 实现与 UUID wire 映射审查。** 沿既有 owner fixed-cut safe renderer、双分页 RPC/cursor 与通知 identity 复核最终字节，修正 stock 0.155 的合法 UUID threadId；保持原 Registry 身份和 pin。有历史的 stock native cold resume 单列，0.161 不继承。
2. **R2 有限 D0/pipe 收口与后续门槛。** 交付以 R1 v2 为前置的五文件包，保留 11+4 和独审新增 5 的口径、native EPERM 原始失败；本地正常 AF_UNIX 四项与 author→run/branch CAS 继续单列。不能以 report 或外部 Python loop 决定后继，不等待 H7。
3. **其余无冲突小包。** H2b 单 consumer 迁移、Basic malformed slash 本地拒绝、H3/H4 依优先级推进；DSH codec、OpenCode exact-version 认证和 H5/H6/H7 core 契约分别准备。H8 全部依赖原 gate，不把多个大范围改动合成一包。

每包冻结、独审、交付后立即开始下一个依赖已满足的小包，不停在“等待本地”。回传一到即复核实际源码与证据，发现产品问题则修复、重新冻结并补相应 gate；环境补验保持局部 `partial_env`。真实模型、付费、安装/凭证、远端写入和发布仍按实际授权处理。本次仅更新独立实施计划，不修改公开技术报告、不上传、不 push。

## 8 固定源码索引

以下入口索引固定到已完成有限 H1+H2a 复核的 d92ff37。前文的 674252f/ec9077e/715468d 继续标识对应历史研究与实际测试来源，不因新发布改写旧窗口。实施前应核后续 main 差异；未合入包新增 API 不以主线链接冒充已发布。

- [现有 owner runner](https://github.com/Deng-0119/RPNH/blob/d92ff3704b6002bf5ecbccb3e6a3d1489809a805/cpn/orchestrator/runner.py)
- [Harness](https://github.com/Deng-0119/RPNH/blob/d92ff3704b6002bf5ecbccb3e6a3d1489809a805/cpn/rpnh/harness.py)
- [AgentTask 执行](https://github.com/Deng-0119/RPNH/blob/d92ff3704b6002bf5ecbccb3e6a3d1489809a805/cpn/rpnh/agent_tasks.py)
- [TaskControl](https://github.com/Deng-0119/RPNH/blob/d92ff3704b6002bf5ecbccb3e6a3d1489809a805/cpn/rpnh/task_control.py)
- [原生 execution reader](https://github.com/Deng-0119/RPNH/blob/d92ff3704b6002bf5ecbccb3e6a3d1489809a805/cpn/rpnh/registry/run_authority.py)
- [显式工具 PN 示例](https://github.com/Deng-0119/RPNH/tree/d92ff3704b6002bf5ecbccb3e6a3d1489809a805/examples/tool_pipeline)
- [provider ledger 实现](https://github.com/Deng-0119/RPNH/tree/d92ff3704b6002bf5ecbccb3e6a3d1489809a805/cpn/rpnh/registry/_provider_calls)
- [managed scheduler](https://github.com/Deng-0119/RPNH/blob/d92ff3704b6002bf5ecbccb3e6a3d1489809a805/cpn/plugins/managed_scheduler.py)
- [execution net Registry](https://github.com/Deng-0119/RPNH/blob/d92ff3704b6002bf5ecbccb3e6a3d1489809a805/cpn/rpnh/registry/execution_net.py)
- [RPNH CLI](https://github.com/Deng-0119/RPNH/blob/d92ff3704b6002bf5ecbccb3e6a3d1489809a805/cpn/rpnh_cli.py)
- [RRSI 当前实现](https://github.com/Deng-0119/RPNH/tree/d92ff3704b6002bf5ecbccb3e6a3d1489809a805/examples/rrsi_v06)
- [DSH 当前集成](https://github.com/Deng-0119/RPNH/tree/d92ff3704b6002bf5ecbccb3e6a3d1489809a805/integrations/dsh)
