# Lane 依赖图、应用顺序与冻结白名单

## 依赖图

```
main 1f191645 (产品 d92，H1/H2已在)
 ├─ core: S1 → H7_core → H7_history → H7_lowering → [剩余H7工程UNIMPLEMENTED]
 ├─ rsi: R1_v2_full → R2
 ├─ codex: (Codex_reader + Codex_effort) → Codex_history → Codex_0161
 ├─ dsh: DSH
 └─ opencode: OpenCode

各lane/stage独立记录 → [明确选入范围] → 新组合identity + 新gate + 独审 + 回归
```

并行的lane没有强行添加文本冲突依赖；不代表运行组合已验。core/S1/H7与DSH/OpenCode会触及Codex0.161的锁定文件，必须先各自验阶段。

- R1 从干净main只用v2 full patch。v2包内delta仅是历史迁移备选；此累计包默认不用。如本地已装v1，先按V5核全部6个superseded_sha256，再另做批准的迁移，绝不能full+delta都应用。
- reader与effort无路径交集，均完成后才能history；history最终patch必须df0c3090…，不能用旧c1d5665c…。
- S1→H7_core虽无touched路径交集，仍有精确输入依赖；core→history→lowering不可跳步。
- 原ZIP中的旧patch、生成脚本和历史证据全部保留；以下唯一根patch是默认应用对象。不要递归glob选patch，也不将evidence里的补丁应用到产品。
- 后继合法修改祖先文件时，祖先的final-hash检查可能拒绝后继。保存祖先阶段树/证据，用后继gate核后继；不能重写祖先manifest。

## 五条lane的精确入口

解压原包到独立工作目录后，以下 member 路径相对该解压目录。各ZIP原字节在archives/，无需旧附件。

### S1 / core

- 前置：干净main基线
- 原ZIP：`archives/RPNH_Static_Lease_Reads_Offline_Candidate_20261008.zip`
- ZIP SHA-256：`e3c002ee4d3723de843c66e63f1900c9c5a6bd7174cda250320d92796ff7626d`
- 唯一应用patch：`RPNH_Static_Lease_Reads_Offline_Candidate_20261008/static-lease-reads.patch`
- patch SHA-256：`bd0e2a8d362179fd68bd5451a932449f059b7038db16ca627eb84843f809bd4d`
- touched paths：13（完整路径见CANDIDATE_INDEX.json）
- 首读与工具：
  - `RPNH_Static_Lease_Reads_Offline_Candidate_20261008/LOCAL_ACCEPTANCE.md`
  - `RPNH_Static_Lease_Reads_Offline_Candidate_20261008/VALIDATION.md`
  - `RPNH_Static_Lease_Reads_Offline_Candidate_20261008/tools/verify_changes.py`

### H7_core / core

- 前置：S1
- 原ZIP：`archives/rpnh-parent-child-core-candidate-20261008.zip`
- ZIP SHA-256：`44f009482056610b1f8d109ab431c65fd486750fa51dd710618898f8cbf2ceff`
- 唯一应用patch：`rpnh-parent-child-core-implementation/H7-core.patch`
- patch SHA-256：`7746881935e0d35e85cb71b2e08bab82ab3f844d52771212b39b3a7728448ed3`
- touched paths：32（完整路径见CANDIDATE_INDEX.json）
- 首读与工具：
  - `rpnh-parent-child-core-implementation/REPRODUCE.md`
  - `rpnh-parent-child-core-implementation/tools/verify_identity.py`
  - `rpnh-parent-child-core-implementation/IMPLEMENTATION_SLICE.md`
  - `rpnh-parent-child-core-implementation/LOCAL_FOLLOWUP_TASK.md`

### H7_history / core

- 前置：H7_core
- 原ZIP：`archives/rpnh-acceptance-history-validator.zip`
- ZIP SHA-256：`5d877046a40423adca388523ea955d73ebaf38320f6936ad67a32b4a6f6b008c`
- 唯一应用patch：`rpnh-acceptance-history-validator/acceptance-history.patch`
- patch SHA-256：`b8ad5b5c18346cf9fa71cd17c96d17deb67ecbb336329d83c78c271f521bedfa`
- touched paths：4（完整路径见CANDIDATE_INDEX.json）
- 首读与工具：
  - `rpnh-acceptance-history-validator/REPRODUCE.md`
  - `rpnh-acceptance-history-validator/tools/verify_identity.py`
  - `rpnh-acceptance-history-validator/VALIDATION.md`
  - `rpnh-acceptance-history-validator/review/REVIEW.md`

### H7_lowering / core

- 前置：H7_history
- 原ZIP：`archives/rpnh-bound-child-material-lowering.zip`
- ZIP SHA-256：`4d2fc67ec4bad0c0b8c557be516bcfdbe4a0b89c58c8d1d348a5c172b3eb61e7`
- 唯一应用patch：`rpnh-bound-child-material-lowering/bound-child-declarations.patch`
- patch SHA-256：`2bc8dd99115f2d139f6b17dd8cd1342087a02110233b8d43c5c37c5199a9cc98`
- touched paths：8（完整路径见CANDIDATE_INDEX.json）
- 首读与工具：
  - `rpnh-bound-child-material-lowering/REPRODUCE.md`
  - `rpnh-bound-child-material-lowering/IMPLEMENTATION_SCOPE.md`
  - `rpnh-bound-child-material-lowering/SOURCE_IDENTITY.json`
  - `rpnh-bound-child-material-lowering/review/REVIEW.md`

### R1_v2_full / rsi

- 前置：干净main基线
- 原ZIP：`archives/RPNH_R1_Inert_Iteration_Profile_v2_Terminal_Fix_20261008.zip`
- ZIP SHA-256：`e860aa7ea5be54e2c63532a592356ae7ef13a9e77e5998a2148a88c0e16e2881`
- 唯一应用patch：`RPNH_R1_Inert_Iteration_Profile_v2/inert-iteration-profile-v2-full.patch`
- patch SHA-256：`67466bfc13a685dd79dc26d0c0c2f0a33b7016553ccf53728b57906cc2f10ac3`
- touched paths：6（完整路径见CANDIDATE_INDEX.json）
- 首读与工具：
  - `RPNH_R1_Inert_Iteration_Profile_v2/LOCAL_GATE_ZH.md`

### R2 / rsi

- 前置：R1_v2_full
- 原ZIP：`archives/RPNH_R2_Single_Registry_Validation_20261008.zip`
- ZIP SHA-256：`e47205abe9e49dfa46e043baafafb196cbf05be4e797a7b0b935da75aec33aed`
- 唯一应用patch：`RPNH_R2_Single_Registry_Validation_20261008/rsi-runtime-validation.patch`
- patch SHA-256：`5054c53cc61bb03fdc58a1da6f7e56b41decc0ccb78124478effc38b2bf93e52`
- touched paths：5（完整路径见CANDIDATE_INDEX.json）
- 首读与工具：
  - `RPNH_R2_Single_Registry_Validation_20261008/LOCAL_RETURN_TASK_ZH.md`
  - `RPNH_R2_Single_Registry_Validation_20261008/LOCAL_GATE_ZH.md`

### Codex_reader / codex

- 前置：干净main基线
- 原ZIP：`archives/RPNH_MainThread_History_Local_Validation_20261008.zip`
- ZIP SHA-256：`9183a673ebf04256d93d7c13b4deaf7acd00855201ae32088b8c8ab79a50acc5`
- 唯一应用patch：`RPNH_MainThread_History_Local_Validation_20261008/native-main-thread-history.patch`
- patch SHA-256：`f4726d7c1a230c8935b22310bb1d29f7c8e68342ef9b660cabb6430b8a3d4654`
- touched paths：7（完整路径见CANDIDATE_INDEX.json）
- 首读与工具：
  - `RPNH_MainThread_History_Local_Validation_20261008/LOCAL_GATE_ZH.md`
  - `RPNH_MainThread_History_Local_Validation_20261008/run_local_gate.py`

### Codex_effort / codex

- 前置：干净main基线
- 原ZIP：`archives/RPNH_Codex_Effort_Local_Validation_20261008.zip`
- ZIP SHA-256：`8e78f56ceed64c294f84e4f3a19eb7db6346567884d4561e87e7ab25c366f645`
- 唯一应用patch：`codex-effort-local-validation-20261008/codex-effort-wire-codec.patch`
- patch SHA-256：`3be0de65edb5a4f68579f115f5072bacc3e4fe4ffefeba96b226e45cbcab8a8d`
- touched paths：12（完整路径见CANDIDATE_INDEX.json）
- 首读与工具：
  - `codex-effort-local-validation-20261008/LOCAL_VALIDATION_ZH.md`
  - `codex-effort-local-validation-20261008/LOCAL_COMMANDS.md`

### Codex_history / codex

- 前置：Codex_reader, Codex_effort
- 原ZIP：`archives/RPNH_Codex_History_Native_Gate_20261008.zip`
- ZIP SHA-256：`452947cdf814bf7f74de66c1ff4d35d1a7ffc57209d1ef5d94cf9ee2219b0ab7`
- 唯一应用patch：`RPNH_Codex_History_Native_Gate_20261008/codex-owner-history-projection.patch`
- patch SHA-256：`df0c3090e84f532d158dd02d7b65489323563a470843e4bd4e2688ffcc25900b`
- touched paths：21（完整路径见CANDIDATE_INDEX.json）
- 首读与工具：
  - `RPNH_Codex_History_Native_Gate_20261008/native-gate/NATIVE_GATE_ZH.md`

### Codex_0161 / codex

- 前置：Codex_history
- 原ZIP：`archives/RPNH_Codex_0161_Candidate_Local_Gate_20261008.zip`
- ZIP SHA-256：`66f7058ecbbff375f12a36d070bad550a2627d0b951c5e0c0ed777ef9bbcaaea`
- 唯一应用patch：`RPNH_Codex_0161_Candidate_Local_Gate_20261008/codex-0161-candidate.patch`
- patch SHA-256：`720bcf2bd95886a26425e00caec80eab0fbcf0eec0481202ca54ce88a5f8a3c8`
- touched paths：6（完整路径见CANDIDATE_INDEX.json）
- 首读与工具：
  - `RPNH_Codex_0161_Candidate_Local_Gate_20261008/native-gate/NATIVE_GATE_ZH.md`
  - `RPNH_Codex_0161_Candidate_Local_Gate_20261008/BASE_SOURCE_LOCK.json`
  - `RPNH_Codex_0161_Candidate_Local_Gate_20261008/verify_package.py`

### DSH / dsh

- 前置：干净main基线
- 原ZIP：`archives/RPNH_DSH_Codec_Offline_Candidate_20261008.zip`
- ZIP SHA-256：`a068ddc977bcbad53976abdcb58465e0ba06d01000a44011d66429fc11a9b083`
- 唯一应用patch：`rpnh-dsh-session-codec/candidate.patch`
- patch SHA-256：`0bddfeb49e1b829911d2f594670476bef39a6e6aefff28c5f61f545506a26b06`
- touched paths：13（完整路径见CANDIDATE_INDEX.json）
- 首读与工具：
  - `rpnh-dsh-session-codec/LOCAL_VALIDATION.md`
  - `rpnh-dsh-session-codec/scripts/verify_bundle.py`

### OpenCode / opencode

- 前置：干净main基线
- 原ZIP：`archives/RPNH_OpenCode_11835_Candidate_Local_Gate_20261008.zip`
- ZIP SHA-256：`870497258d07fb002af8c063db93e4b768d2410d41b657a6cc3c89fe92296520`
- 唯一应用patch：`rpnh-opencode-candidate-gate/opencode-candidate.patch`
- patch SHA-256：`ee33ed95126989319e981683a308e3182d9b5b4e4c0f0a900e5bb8f111716bba`
- touched paths：10（完整路径见CANDIDATE_INDEX.json）
- 首读与工具：
  - `rpnh-opencode-candidate-gate/README_ZH.md`
  - `rpnh-opencode-candidate-gate/NATIVE_GATE_ZH.md`
  - `rpnh-opencode-candidate-gate/verify_package.py`

## 核心阶段身份与0.161目标锁

- S1：979-file curated identity `4a7841ac16432aeb173562ec797bc062839a006fdb684d5ea1e3b161c6c49fbc`；S1小包只含13个replacement，完整979-file证据副本在H7_core原包inputs/S1-source/，不可再次应用S1。
- core：992 files，`ac68327e442b7bda8a6aa2ba93c0cd20ad72181a661b0497627b08713334907e`
- history：994 files，`a3af1d85ffbc8acac72e719fed316ed3c848229fce92026c62e571cb77a913a2`
- lowering：999 files，`7867bec81c0830f19cfd6f8a58b0d1598d5efc8220c69e6cfdd02604f98f1a0e`
- Codex0.161：1,044-file目标锁，`316a06aad41653e890d16a19519d07b09042c54c4961586b54cf2842b10d8658`；原native gate逐项核目标，不是只核包ZIP。

核心各阶段full snapshot只是相应curated集的完整，不是全repo。extra checkout文件的补充与完整仓库native运行需新记录；不要把候选source subtree当repo reset目标。

交集/锁命中逐路径证据见plan-v5/PATCH_CONFLICT_AUDIT_ZH.md和plan-v5/evidence/IDENTITY_AND_INTERSECTIONS.json。最终组合身份必须另建，不能替换以上任何摘要。
