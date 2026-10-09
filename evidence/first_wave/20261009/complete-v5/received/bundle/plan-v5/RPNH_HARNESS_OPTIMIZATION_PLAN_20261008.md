# RPNH Harness 持续优化实施计划

版本：v5。更新：2026-10-09 UTC。

用途：通用 harness 的持续实施与逐包验收。独立维护，不并入对外技术报告；公开报告只介绍已实现且证据明确的产品能力与边界。本版替代 v4 的接续安排，保留 v4 原件与所有历史测试窗口。

## 1. 结论与当前基线

计划锁定 [main 1f191645](https://github.com/Deng-0119/RPNH/commit/1f191645c4d60c8b190d42e9fad99c85e8981c03)，父提交 `8dd360e4848912a998dbd83220c3f0ce0a1caa86`；产品仍为 [d92ff37](https://github.com/Deng-0119/RPNH/commit/d92ff3704b6002bf5ecbccb3e6a3d1489809a805)。1f 的变化是两份技术报告，不能据此宣称新增候选已合入。实施开工须重新核实际 HEAD、dirty/untracked 与各 patch preimage；本次文档核查不冒充新一次远端或完整仓库测试。

H1/H2a 已在主线，166 unique/166 executions 的有限组合 native 窗口已复核，包含 H1 的 39 个用例；不重新套补丁、不累加成 205。旧 C3 `PARTIAL_ENV`、241 passed/1 failed 和历史环境失败继续保留，现有有限 gate 不扩张为全仓、任意崩溃恢复或真实业务认证。

新增已交付候选为 S1、H7 core、H7 acceptance-history。它们有确定的离线实现和独审，不是 H7 生产启动已完成。lowering 是它们的顺序后继，最终接包资格只看 [LOWERING_STATUS.md](LOWERING_STATUS.md)。

当前最有价值的下一包：在原 selected-installed HostProfile/Registration 边界增加有限、明确的公开 execution-material inventory 契约，再完成两类 payload 的完整材料准备。该项可继续云端离线实施；peer、receipt、physical reservation、Popen 和实际 wrapper 必须另有实现与真实环境验证，不能笼统记为“只差测试”，也不能把全部后续推给本地。

## 2. 五条实施链与组合规则

| 链 | 顺序 | 现阶段作用 |
|---|---|---|
| 核心 | main → S1 → H7 core → H7 acceptance history → lowering | 通用 static lease 原语、Registry causal core、只读历史证明、机械 lowering；最后一项按独立状态页 |
| RSI | main → R1 v2 → R2 | 有限单 Registry 的配置/声明与确定性示例；不依赖 H7 |
| Codex | main → MainThread reader + effort → Codex history → 0.161 candidate | 逐阶段 exact source gate；默认仍 0.155.0 |
| DSH | main → codec candidate | 旧 pin/REVISION/Persistence 不变；新版 runtime 接线另验 |
| OpenCode | main → 1.18.35 candidate | 默认仍 1.18.32；显式 candidate 与 stock native 分层 |

这里的两个 history 完全不同：H7 acceptance-history 是原 Registry 的历史 acceptance 分类器；Codex history 是 owner 安全文本与前端历史 RPC。不能按文件名简称选包。

各链先在隔离工作树/源码副本完成自己的阶段验收。核心与前端补丁之间虽然没有 touched-path 交集，Codex 0.161 的 1,044-file 目标锁仍会拒绝其中多项改动。全部核心后继合计涉及该锁中 29 个不同路径；DSH 另 6 个，OpenCode 另 5 个。R1/R2 不命中这份锁，不代表其运行组合已认证。

源包是证据和候选材料，不是整仓覆盖源。不能靠复制 `source/`、回退主线报告、改旧 manifest、关闭检查、忽略 mismatch、自动 `--3way/--reject` 来拼装。后继合法修改后旧阶段 hash 不再匹配是正常现象，须保存阶段结果，使用对应后继 gate。

最终组合是另一个工程步骤：明确选入哪些候选，核目标 preimage 和语义影响，建立新的完整组合身份/源码清单，冻结适用的新 gate，经独审后跑受影响的增量回归和真实环境验收。stage PASS 只能保留为该 stage 的证据，不能汇总成 combined PASS。

精确路径交集、锁冲突与补丁 SHA-256 见 [静态核查](PATCH_CONFLICT_AUDIT_ZH.md)；执行顺序与回传字段见 [接包任务书](LOCAL_HANDOFF_ORDER_ZH.md)。

## 3. 唯一权威与不变边界

1. **Registry 唯一负责 exact matching 与事实。** run/task/net/generation、注册材料、claim、attempt、receipt、结果与终态按原生精确引用匹配；名称、digest、stdout、JSON 或“最新一条”不能替代完整身份。
2. **PN 唯一负责依赖与正向准入。** 跨 operation/child 的 next step、join、资源、语义 stop/retry 由原 net/marking/claim/registered policy 决定。扩展原契约，不新增 scheduler、side ledger、ready/allow 表或第二 marking。
3. **普通计算仍用普通代码。** 解析、算法、评分和领域 predicate 可留在 atomic operation 中，产出具有 exact 谱系的 typed facts；布尔结果本身不给执行许可。
4. **真实物理安全保留。** socket/HTTP、进程、pool/Future、取消、容量、锁、路径和外部 veto 可存在，但不能把物理结束伪造成业务 Success。
5. **unknown 不洗白。** 丢 receipt/超时/断连不证明动作没发生，probe 健康不清除先前 unknown；不自动重放、补启或释放仍未解决的 claim/slot。
6. **只读不触发执行。** reader/reconnect/history 不创建 owner/grant，不补启 child/provider。historical VALID、cut、复制的 canonical JSON 都不是新授权或 native receipt。
7. **不改业务策略。** 不添加 ERP/SCB 特例、评分捷径或自动修参数；原模型配置、数据隔离、来源 pin、grader/Session/world 安全继续保留。
8. **fresh/recovery/消费迁移分验。** 一个 fresh slice 不能证明 crash recovery、physical exactly-once、H7b 或 H8。

## 4. 当前证据账本

下表的数字按各包最终 manifest/报告；不同源码、重复运行、相交测试集不得累加。分组通过不是全仓通过，交付不是合入，D0 不是 native。

| 项目 | 已有范围与状态 | 仍未完成 |
|---|---|---|
| H1/H2a | 已合入 d92；166 distinct/166 executions 的有限组合窗口；包括 H1 39，真实 CLI 与另进程只读回读 | 更广泛恢复/业务/全仓范围；旧 C3 环境缺口不改写 |
| S1 | 13 touched paths；979-file candidate；最终作者 71，独审 42 与作者重叠 13，共 100 distinct pytest cases；另 4 probes。已交付 | native owner/并发/stop/drain/reentry gates；不是 H7 origin guard 或物理授权 |
| H7 core | 32 touched paths；992 files；139 distinct D0 = 31 author + 68 adjacent + 40 review；184 executions 含 45 重叠。已交付 | 完整 66 项 D0 合同、36 项 D1、生产 issuer/reservation/wrappers/父端终态闭环 |
| H7 acceptance history | 4 touched paths；994 files；298 distinct = 108 新 history + 139 core 回归 + 51 新独审；最终无重叠。已交付 | producer close、later generation、publish/abandon、native 完整生命周期仍不支持或不可达；VALID 不授权 |
| H7 lowering | 原 compiler 的 origin lowering 与声明冻结；精确状态/身份只见状态页 | 完整 runtime inventory、生产两类 wrapper、native 闭环不由此完成 |
| MainThread reader | 七文件已交付；作者 57/9 deselected，独审 32；依赖 overlay 复跑同 57/9 不另加 | trusted-host refs-only 不是公开正文授权；其余 H2b consumer 和 native frontend 另验 |
| Codex effort | 十二文件已交付；43 pass/1 deselected，另 4 probes | 新版 native 未认证，默认 0.155 不变 |
| Codex history | 最终 21 文件已交付，patch df0c3090…；作者 119+43，独审 128 有重叠分别记 | stock 0.155 TUI/真实 transport 仍 NOT_RUN |
| Codex 0.161 | 六文件 candidate 已交付；作者 122、8、16 各窗口，独审 34 产品/5 合成日志分别记 | 两版 stock、真实 object RPC、Rust/provider 未实测 |
| R1 v2 | 六新增文件，terminal_outcomes 已修正；作者 150 = 141 pure+9 Registry，独审 33 pure+3 Registry。已交付 | native、物理 unknown、完整 runtime；旧 R1 弃用 |
| R2 | 五新增示例文件；作者 15 = 11 Registry+4 test-only pipe；独审复跑同15+5追加。已交付 | 四项 native、author→run linkage/branch CAS、R3与完整 campaign |
| DSH | 13 文件已交付；Node55、Python39、launcher/source9；旧 owner 12 pass/22 AF_UNIX blocked，后次 exit130 不计通过 | 两项安装/打包未跑；两版 typecheck、新 Session/lifecycle/执行接线 |
| OpenCode | 十文件冻结并已交付；G1作者/独审/净应用复跑同274，另33独审，不重复相加 | G2 stock+ApplicationDouble 与 G3 stock+原 Gateway/Application 均 NOT_RUN |

S1→core→history 的 100、139、298 是各自版本的验收集合，不能相加为 537；H7 的 66/36/10 是设计验收条目，也不是 pytest case 数。lowering 的未定计数不拼入任何已交付统计。

核心 source aggregate 已在本次只读核查中逐文件重算：S1 979、core 992、history 994；与包内身份一致。完整摘要和已交付 ZIP 身份见 evidence JSON。小型原报告副本见 `evidence/`，旧失败、排除项与未覆盖边界仍由原完整包保留。

## 5. H7 接续路线：明确实现缺口与环境边界

### 已完成的可复用离线切片

- **S1 原语：** static lease read 不消费 lease，真实 Registry admission/Start/products/Success 与 exact carrier selection 已有窄证据；普通 data/control consume-return 不变。S1 本身不提供 H7 不可移除 origin、安全 bootstrap 或 child 启动。
- **core 因果链：** 原 Registry 的 typed request→canonical materials/target→producer-owned intent→dispatch claim→worker observation→acceptance→bound bootstrap/genesis→origin→initial PN witness，及 protected/replay/reentry/fencing 守卫。D0 注入 `_NativeBoundaryEvidence` 仅用于测 Registry，不是生产 issuer。
- **历史 proof：** `EventStore.classify_child_acceptance_history(assertion)` 在原 source 的单一 SQLite read-only snapshot 中核原 transaction/root/member/producer/Start/epoch-at-cut，给出 VALID/INVALID/UNAVAILABLE。已覆盖 accepted PROVISIONAL 经 durable stop、新 writer、后续 provisional products 的合法历史；不授予新动作、不发布临时正文、不 mint receipt、不释放 unresolved claim。SQLite mode=ro/query_only 不承诺目录零写，可能有 SHM/WAL 协调文件。
- **lowering 接续：** 范围和最终资格见状态页。旧 core `NATIVE_HANDOFF.md` 中第6项 lowering/第7项 history 是当时待办；应按后继状态识别已实现部分，不能机械重复，也不能把剩余 native 部分一并划完成。

### 下一最小离线包：原 Registry 下的公开 execution materials

目标是让已选择的可信 installed HostProfile/Registration 主动声明有限公开材料闭包，而不是反射任意 Python callable、扫描用户目录或把 profile 名字当完备证明。

1. 原 selected-installed entry 明确 implementation identity、支持的 payload kinds、公开 logical material names、不可变 bytes/digests 与依赖。缺契约先拒绝；不接收 caller import/pickle/任意 executable locator。
2. 公开 contract/config/material 用既有 `RegistryRegistrationGateway` 与原 private-system publication/source lineage 登记，复用 declaration/schema exact refs；必要时在原 Registry 扩窄 typed producer/schema，不增加 side ledger。
3. secret 只由不带值的 opaque binding identity 指定现有 HOST credential route/account/policy；不读取秘密配置来脱敏，不记录 secret value/hash/header/private path，不切换账号或权限。凭据缺失可 veto，不能形成新许可。
4. 完整枚举公开 lowerer/executor/tool/schema/catalog/plugin/service/HOST/runtime/model-route policy 依赖；可变文件名不是材料。任意 broad callback 没有 dependency closure 就保持 unsupported。
5. Module 与 AgentTask 走原 builder/Registration/serializer、同一 typed inventory；最终 document_root 确定后一次冻结，保持 K→T/task-ID/document-root→D→I→E 无环，intent 后不改路径。runtime origin ref 在 bootstrap 后产生，不回填 D。
6. 原 Registry 校验完整 contract 后才产生完整 `PreparedChildMaterials`。现有 declaration-only 对象不可用布尔字段、空 HOST profile、标签或 digest 替换升级。

离线验收：实际 registered refs 与 public bytes；未声明依赖/同路径改字节/错 source 拒绝；opaque secret rotation 不读取秘密，改变 account/binding 拒绝；schema 指定 authority refs 与业务 ref-shaped 值区分；两类支持 payload；缺/歧义 installed entry；准备过程无 socket/URL/subprocess/private credential access。完整候选须另冻 source/patch、独审；不是本计划已实现的功能。

### 原生闭环：尚需实现，之后才是 D1

| 次序 | 必须完成的工程 | 真实环境必须证明什么 |
|---|---|---|
| N1 sealed transport | 原 task lock 内 stable bundle；fresh Registry dispatch commit 只 mint 一次不可序列化 ticket；原 Popen observation 的封闭 issuer | replay 不 mint、实际 attempts/lock wins、bundle identity、真实进程来源；不能公开 D0 evidence 构造器 |
| N2 owner gate | 固定 OwnerEventLoop command、SO_PEERCRED/birth tuple 双向认证、严格有界 JSON/duplicate-key parser、NOT_READY、sticky stop veto、immutable receipt | request/处理/commit/编码/入队/完整接收/stop 各窗口；signal只置pending并唤醒，不事务/等锁 |
| N3 physical reservation | receipt消费后原子exclusive leaf reservation，typed process-local root/leaf identity；任何Core/EventStore/SQLite/writer前复核 | 空/partial/standalone/同intent EEXIST、symlink/identity漂移全部zero-touch；失败方构造/写入0，既有bytes/epoch不变，崩溃leaf不接管 |
| N4 两类真实wrapper | 原AgentTask与Module fresh composition消费同一typed reservation，接protected bootstrap/origin与原runner | 两类实际调用路径、安装来源、真实start次数与正确先后；declaration helper或test component不足以证明接线 |
| N5 父端闭环 | exact child binding、same-cut terminal observation、registered completion与ordinary Success | child terminal确实存在、parent准入因果闭合、stop/unknown不伪造success；未解决claim/slot保持 |

上述接口/验证代码可先在云端静态实现和受 sentinel 约束的 D0 中推进；可信 issuer、kernel peer、真实 receipt 交付、物理目录、Popen、worker 和 race 的最终证明必须用获准真实 Linux 环境。当前缺的是实现加验证，不是纯环境补跑。

完整 D0 合同 O01–O66 逐项建 evidence mapping，经独审后再跑获准的 N01–N36。尤其 N29–N36 不能用 fixture/shim/cold-read 替代。保持一 native slot、fixed initial bound net，不扩为递归、多slot、跨host、alternate root、自动重启、强杀或清理。

### H7b 与 H8 后续阶段

H7b 是 observation-only recovery 的独立 R01–R10 合同，不能由 fresh/history/lowering 推出。H8a RRSI、H8b SCB、H8c ERP 的消费迁移另立包；不能把整个旧 imperative controller 放进一个 HOST operation 就称 PN 收敛。生产 native issuer、父端完成等缺项仍 fail closed，不能为迁移提前开放。

## 6. 其余持续实施卡

只选择依赖已满足、文件占用不冲突的一个有界增量；环境阻塞不暂停不相关的离线工作。H5/H6 不由 H7 完成，R2 不等待 H7。

| 卡 | 下一实施范围 | 完成门槛与保留边界 |
|---|---|---|
| H2b reader消费者 | AgentTask `_terminal_output`、tool_pipeline terminal、AB/RRSI等逐个迁原 exact reader；MainThread前置另记 | 同core/cut/full-ref/size边界、decode后复核；不靠“最后terminal”；result旧shape/None语义单列；读不触发TaskControl recovery；实际consumer D1逐个验 |
| H3 HA stop | `local_driver`无活动/replay启发式改诊断或registered policy | 显式deadline/user stop/安全veto保留；长operation/resource wait不误停，真实进程树与quiescence另验 |
| H4 catalog | 从真实compiled outcomes/products/ports/schema派生模型可见契约 | catalog→request→runtime同一声明；合法省略/符号兼容，非法write仍拒绝；不加任务特例/自动修JSON |
| H5 provider谱系 | 原provider ledger下所有formal/probe/retry的typed contract，后接v2/v3 transport | 每physical request独立reservation/permit/observation与budget；unknown不洗白；保留合法恢复，不通过删恢复宣称完成 |
| H6 pure batch | 动态有限逐call typed scope/correlation/capacity/capability | 逐call PN准入、共享physical容量、取消/重建证明；same-parent execution-child不冒充H7独立task/run child |
| H8a RRSI | campaign与nested Digester launch/collect迁parent PN | 保留研究方法/评分/轮次/heldout隔离；report只读，删cache不补跑；依赖H7/H7b实际需要的gate |
| H8b SCB | request→Session/solver→quiescence→snapshot/evaluator→PassPolicy | Session真实源码继承、snapshot、原grader保留；CheckpointLedger仅投影；D2获批runtime另验 |
| H8c ERP | prepare→solver→关闭bridge准入→quiescence→freeze→grade→cleanup观察 | world/bridge安全veto与unknown保持；不把finally等同cleanup成功；D2真实world另批 |
| A1 adapters | 当前候选分链验收，默认版本不变 | stock client/transport/typecheck/真实runtime分开；表面schema兼容不等于新版支持 |
| A2 CLI | malformed slash本地usage拒绝；help/net参数/错误码/输出契约 | 不落模型执行、不丢options；现有`/tasks`和`/rpnh-tasks`会reconcile，不能叫严格零写；新命令绑定已验core |
| A3 RSI | R1v2→R2；后续author→run exact linkage/branch CAS、R3 scripted native、R4完整campaign | 原三重CAS，不虚构`start_run(author_ref=...)`；R2单Registry与R4跨child分开；训练非RSI必需 |
| H9 胶水与组合 | 按真实重复逐文件收口，逐组合回归 | 保留MainThread lineage、TaskControl manifest、inbox/RPC map、one-use ticket与物理容量；不另建session状态库 |

R1 v2 必填 `terminal_outcomes={stop,final_select,final_retain}`，直接生成原 `TerminalBinding.config.run_outcome`。未应用旧R1用full；旧六文件全部匹配才用delta，两条二选一，不用应用侧terminal overlay。R2目前未完成author→run/branch CAS；领域evaluation unknown可按显式协议retain incumbent，不能升候选，也不是physical unknown重试许可。

RRSI只指 RSI on RPNH / RRSI，不混入 AATU。Frozen rrsi_v06 不是任意轮数通用配置，task-ID split 不等于内容无重叠；heldout/export可见性、scorer/data/model exact配对、不可比成本拒绝晋升继续保留。R5真实模型/训练仅在另行批准的范围与预算内。

## 7. 验收与持续推进

- D0：真实临时Registry/schema与确定性脚本、受控double；不证明socket/进程竞态。构造真实OwnerEventLoop的case归D1，test-only pipe仍不是native。
- D1：获准真实socket/进程/worker/HTTP与scripted provider；零真实模型不等于无需相应环境授权。
- D2：获准真实Session/container/Odoo/snapshot/grader；不推出业务模型成绩。
- D3：获准固定profile/model/data/scope/budget的真实provider；不代替低层故障注入。

每个增量先核输入与未提交修改，再冻结最小实现/patch/source identity，独审后交付。已授权本地结果到达即复核实际源码、命令、完整日志/JUnit、process exit、node IDs与前后hash；按影响最小修复，不倒写旧窗口。不把历史失败、未运行、环境阻塞删掉或记PASS。

真实环境需记录actual HEAD/diff/untracked、解释器和module来源、binary provenance/digest/version、UTC窗口、dispatch/Popen/worker/child/model计数、exact refs、stop/unknown、reader不变性与reservation zero-touch。报告case verdict与process exit分开；相同node ID跨重跑只计一次。

缺可信既有binary/依赖/正常socket/PTY/权限时只停止该gate，保留BLOCKED或NOT_RUN，继续已授权离线工作。不修改安全守卫、ownership、权限、transport、PATH来凑通过。本计划不新增安装、登录、真实模型、费用、Actions、远端写入、默认升级或发布授权。

当前交付目标是完整且可复核的小包，随后持续接续：先关闭真实缺口，再同步实施计划；对外技术报告独立更新，不夹带内部工作清单，不以计划条目冒充产品能力。
