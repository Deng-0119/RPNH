# RPNH 候选接包、实施与验收任务书 v5

直接按此任务书执行已获批范围，不需要另补提示词。未获批动作先停在相应边界；此文件本身不新增本机访问、安装、登录、native、模型、费用、Actions、commit/push/merge 或发布授权。

## 0. 接收与开工

1. 读取权威 checkout 的 AGENTS.md；确认 origin 为 Deng-0119/RPNH，记录实际 HEAD、origin/main、branch、全部 diff/untracked、已有解释器与依赖来源。计划基线 main 为 `1f191645c4d60c8b190d42e9fad99c85e8981c03`，产品为 `d92ff3704b6002bf5ecbccb3e6a3d1489809a805`，并非强制将后续主线重置到该值。
2. 主线已有 H1/H2a，勿重套。保留现有两份新技术报告；不回退它们匹配历史包。后续 main 或脏文件变化先逐路径核差异，不覆盖用户修改。
3. 按 `evidence/IDENTITY_AND_INTERSECTIONS.json` 核已交付 ZIP 与精确 patch。使用包根的最终 patch；例如 H7 ZIP 里的 `evidence/source-v1/H7-core.patch` 是保留的旧失败版本，不能递归找同名文件后随便选。
4. `source/` 可能是改动文件集或筛选源码子集，既非完整 checkout，也非部署覆盖物。只在授权的隔离工作树应用相应冻结补丁；每阶段先 `git apply --check --whitespace=error`、核 old blob/新增路径，再按范围应用。冲突停止审查，不自动 reset/3way/reject/copy。
5. 分别保留核心、RSI、Codex、DSH、OpenCode 的阶段树与 manifest。完整阶段集合是验收输入，不能把另一 lane 的 `source/` 加进去凑运行依赖。

## 1. 核心链：main → S1 → H7 core → H7 acceptance history → lowering

### S1

- 包：`RPNH_Static_Lease_Reads_Offline_Candidate_20261008.zip`；根：`RPNH_Static_Lease_Reads_Offline_Candidate_20261008/`。
- patch：`static-lease-reads.patch`，`bd0e2a8d362179fd68bd5451a932449f059b7038db16ca627eb84843f809bd4d`。
- 入口：`LOCAL_ACCEPTANCE.md`、`VALIDATION.md`、`tools/verify_changes.py`。13 touched paths，979-file evidence identity。作者71与独审去重规则按包内清单，不借后继通过改写本阶段。
- native owner并发/stop/drain/reentry等是独立门槛；未获批或缺环境只保留该门槛NOT_RUN。S1不是H7 origin guard或child启动许可。

### H7 core

- 包：`rpnh-parent-child-core-candidate-20261008.zip`；根：`rpnh-parent-child-core-implementation/`。
- patch：根下 `H7-core.patch`，`7746881935e0d35e85cb71b2e08bab82ab3f844d52771212b39b3a7728448ed3`。
- 输入必须是精确 S1 979-file identity；输出992 files，aggregate `ac68327e442b7bda8a6aa2ba93c0cd20ad72181a661b0497627b08713334907e`。
- 入口：`REPRODUCE.md`、`tools/verify_identity.py`、`IMPLEMENTATION_SLICE.md`。包内 `inputs/S1-source/` 已包含S1，不能再打一次S1；它可重建该离线快照，不可整目录覆盖权威checkout。
- 已有139 unique D0，完整H7仍缺实现。`tools/*` 中生成/硬化旧候选的历史脚本不是setup命令，不重跑它们改已冻结源。

### H7 acceptance history

- 包：`rpnh-acceptance-history-validator.zip`；同名根目录。
- patch：`acceptance-history.patch`，`b8ad5b5c18346cf9fa71cd17c96d17deb67ecbb336329d83c78c271f521bedfa`。
- 输入992-file core，输出994 files，aggregate `a3af1d85ffbc8acac72e719fed316ed3c848229fce92026c62e571cb77a913a2`。不能应用到S1 alone。
- 入口：`REPRODUCE.md`、`tools/verify_identity.py`、`VALIDATION.md`、`review/REVIEW.md`。原core tests是明确的139-case回归，另108新history与51新独审，共298，不加上旧core形成437。
- 它合法修改core的`event_store.py`和`parent_child.py`。保存core阶段结果后使用history的验证；在后继树上跑旧core整份final-hash检查报不同，不应回滚或改旧manifest。
- 更新后续任务理解：core旧任务书的historical validator待办已由本包在限定范围完成。VALID只是原cut机械证明，非receipt、native evidence或重新执行许可。

### Lowering

先读 [LOWERING_STATUS.md](LOWERING_STATUS.md)。只有该页已核最终独审、精确冻结身份与交付包，才接续应用。若仍未满足，停在精确994-file history阶段，继续已授权的公开material contract离线工作；不提前宣称H7完整。

lowering即使通过，也只解决原compiler的origin结构与声明冻结，不会自动补齐HOST inventory、native issuer、reservation或两类runtime wrapper。

## 2. 下一包的工程任务

### 可继续云端离线

以精确后继身份为输入，在原selected-installed HostProfile/Registration增加有限public-material契约，public bytes与exact refs走原Registry publication；秘密只用现有HOST渠道的opaque binding，不能先读取秘密再脱敏。两类payload保持原builder/serializer，最终document_root确定后冻结，不使用布尔值把declaration-only提升为execution materials。

按实施计划第5节完成实际契约、负向与兼容D0、最小patch/source identity及独审后交付。缺完整contract继续UNSUPPORTED；不扫描任意用户文件，不invoke broad preparation callback来猜闭包。

### 必须补实现并在真实环境验收

原task-lock/bundle/ticket/Popen issuer → 原OwnerEventLoop双向peer/严格receipt/sticky-stop → Core前typed exclusive reservation → AgentTask与Module实际wrapper → exact native child binding/terminal observation/parent completion。

可提前离线编写和审查相关代码，但不得把D0注入冒充真实issuer。待实现与对应D0到位，再在指定且获准的Linux环境跑设计N01–N36。H7b observation-only recovery与H8消费迁移另阶段；未解决claim/slot不能为了测试成功释放。

## 3. RSI独立链：R1 v2 → R2

- R1用 `RPNH_R1_Inert_Iteration_Profile_v2_Terminal_Fix_20261008.zip`，根 `RPNH_R1_Inert_Iteration_Profile_v2/`。未应用旧R1用 `inert-iteration-profile-v2-full.patch`；只有旧六文件全部等于 `superseded_sha256` 时用 `r1-v1-to-v2-terminal.delta.patch`。二选一；已是v2不再应用。
- 必须有 `terminal_outcomes`，直接生成原 `TerminalBinding.config.run_outcome`。旧R1空terminal config包已弃用，不加runtime overlay补洞。
- 再用 `RPNH_R2_Single_Registry_Validation_20261008.zip` 根下 `rsi-runtime-validation.patch`，只新增五个 `examples/rsi_workflows/` 文件。
- 入口分别为 `LOCAL_GATE_ZH.md`、`LOCAL_RETURN_TASK_ZH.md`。R2四项original_orchestrator用真实AF_UNIX；test-only pipe不能替代native。作者15与独审重跑同15+5按清单计，不做35个独立用例。
- author→run/branch CAS、R3 registered-model/scripted native、物理unknown与完整R4 campaign仍未完成。R2有限单Registry范围不依赖H7；S1/H7/lowering组合对原compiler/Registry的影响另验。

## 4. Codex独立链：reader + effort → history → 0.161

1. Reader包 `RPNH_MainThread_History_Local_Validation_20261008.zip`，同名根；入口 `LOCAL_GATE_ZH.md`、`run_local_gate.py`。effort包 `RPNH_Codex_Effort_Local_Validation_20261008.zip`，根 `codex-effort-local-validation-20261008/`；入口 `LOCAL_VALIDATION_ZH.md`、`LOCAL_COMMANDS.md`。两者路径不重叠，分别核最终字节并保存stage结果。
2. 两前置均到位后，用 `RPNH_Codex_History_Native_Gate_20261008.zip` 同名根下最终 `codex-owner-history-projection.patch`，必须为 `df0c3090…`，不是旧 `c1d5665c…`。它改reader的main_thread和effort的app_server/test_codex_compat，不能倒序。最终中文metadata为`zh-CN`。
3. 如验证0.161，再用 `RPNH_Codex_0161_Candidate_Local_Gate_20261008.zip` 同名根下 `codex-0161-candidate.patch`（`720bcf2b…`），核 `BASE_SOURCE_LOCK.json` 完整前置。d92的task_control/run_authority产品改动已在main，不重套overlay。
4. history-only用history `native-gate/NATIVE_GATE_ZH.md`；最终candidate用0.161的同名入口。0.161 gate锁1,044-file subset，先混S1/H7/DSH/OpenCode会按精确身份拒绝。不要关闭锁或改manifest让它通过。
5. 默认0.155.0保持；0.161仅显式candidate。新旧binary共用同一canonical合成Registry根，依次首次/重开，不并发争owner。stock UI、实际object Unix RPC、纯日志分析分别记；日志分析通过不等于stock认证。

旧reader/effort检查在history之后、旧history检查在0.161之后可能正常拒绝。保留原阶段树与结果；最终阶段用对应后继gate，不在后继树上强求所有祖先final hash同时成立。

## 5. DSH与OpenCode各自独立

### DSH

包 `RPNH_DSH_Codec_Offline_Candidate_20261008.zip`；产品包根 `rpnh-dsh-session-codec/`，另有独审根。`scripts/verify_bundle.py`查包；`--checkout`查十三changed paths preimage与十二protected files；入口 `LOCAL_VALIDATION.md`。

保留旧pin/REVISION/Persistence与生产open/stat/read边界。Node55/Python39/launcher9与owner12pass/22blocked分记，exit130不计通过。typecheck、Session cold reopen/lifecycle/执行接线仍待，候选不等于新版runtime支持发布。

### OpenCode

包 `RPNH_OpenCode_11835_Candidate_Local_Gate_20261008.zip`；根 `rpnh-opencode-candidate-gate/`。入口 `README_ZH.md`、`NATIVE_GATE_ZH.md`、`verify_package.py`。默认1.18.32，显式test-only candidate1.18.35。

G1的274与另33独审按原清单，不重复相加。G2 stock+ApplicationDouble、G3 stock+原Gateway/Application均NOT_RUN，只是有限smoke准备。先核既有可信官方binary digest/exact version，不下载、换版或提高默认。fixture prepare、原tick、owner lease和measured effects分别记录；`/rpnh-tasks`会reconcile，不能用作严格零写测点。

## 6. 最终组合任务：不要继承stage PASS

只有选入候选的版本和目标范围确定后才开始组合：

1. 从权威base建立新组合分支/工作树，逐patch应用已验增量，记录完整diff和原stage来源。核心内、Codex内按上述顺序，其他lane不强加不存在的文本冲突依赖。
2. 按实际改动列影响：S1 marking/lease/exact selection；H7 Registry/Start/fencing/read authority；history read-only proofs；lowering compiler/fusion/wire；R1/R2 compiler与terminal；前端owner/read/socket；DSH packaging；OpenCode的新增tests/conftest对测试发现也要审查。
3. 新建**新组合**source identity、full inventory、声明兼容的新gate与fixture身份，独审其来源和覆盖。旧stage manifest保持不变。这是新的组合验证产物，不是给旧gate篡改expected hash。
4. 跑所选增量受影响的D0与获准native窗口；至少回归H1/H2 reader/owner、相关S1/H7/编译/terminal/前端路径，具体node IDs由实际diff和已有测试确定。缺case/环境记缺口，不能填PASS。
5. 组合报告写确切范围、全新manifest、binary与transport、真实命令/exit、去重ID、失败/NOT_RUN、对比stage差异。只有本组合实际通过才写本组合PASS，不以各lane全绿推导。

## 7. 停止条件与回传

缺binary、依赖、授权、正常socket/PTY或出现unexpected hash：停止依赖该条件的步骤，准确记录BLOCKED/NOT_RUN；继续无依赖且已获批的离线工作。不改安全守卫、owner/权限、transport、PATH，不擅自安装或切换环境。unknown、不支持或欠receipt/child terminal均不得伪装stopped/interrupted成功。

回传：actual HEAD/branch/diff/untracked；每stage与组合的source/patch/manifest；解释器/module与binary来源；exact version/hash；UTC起止、完整命令、stdout/stderr/JUnit、case verdict和process exit；原node IDs/排除项；dispatch/Popen/worker/child/model计数、exact refs、stop/unknown与read-only/zero-touch证据。保留失败与旧窗口，不覆盖历史记录。

如已另获准发布，发布后核exact remote commit与文件字节；否则停在已验候选。计划、包交付、代码合入、native认证与生产默认变更分别报告。
