# RPNH 累计本地验证包：依赖、入口与未执行边界核查

核查日期：2026-10-09 UTC。方法：只读 v5 计划和 `evidence/IDENTITY_AND_INTERSECTIONS.json` 指定的 12 个最终 ZIP 的原文；未导入产品、未运行 pytest、模型、native 或安装。下列命令是本地复验模板，不是本次已执行记录，也不新增执行授权。保留所有原 ZIP、patch、manifest 和旧失败证据。

## 1. 来源索引与使用约定

下文 `代号::member` 表示表中 ZIP 根目录下的精确 member，不是另一个工作目录的副本。

| 代号 | 最终 ZIP | ZIP 根 |
|---|---|---|
| S1 | RPNH_Static_Lease_Reads_Offline_Candidate_20261008.zip | RPNH_Static_Lease_Reads_Offline_Candidate_20261008/ |
| CORE | rpnh-parent-child-core-candidate-20261008.zip | rpnh-parent-child-core-implementation/ |
| HIST | rpnh-acceptance-history-validator.zip | rpnh-acceptance-history-validator/ |
| LOW | rpnh-bound-child-material-lowering.zip | rpnh-bound-child-material-lowering/ |
| R1 | RPNH_R1_Inert_Iteration_Profile_v2_Terminal_Fix_20261008.zip | RPNH_R1_Inert_Iteration_Profile_v2/ |
| R2 | RPNH_R2_Single_Registry_Validation_20261008.zip | RPNH_R2_Single_Registry_Validation_20261008/ |
| READER | RPNH_MainThread_History_Local_Validation_20261008.zip | RPNH_MainThread_History_Local_Validation_20261008/ |
| EFFORT | RPNH_Codex_Effort_Local_Validation_20261008.zip | codex-effort-local-validation-20261008/ |
| CHIST | RPNH_Codex_History_Native_Gate_20261008.zip | RPNH_Codex_History_Native_Gate_20261008/ |
| C161 | RPNH_Codex_0161_Candidate_Local_Gate_20261008.zip | RPNH_Codex_0161_Candidate_Local_Gate_20261008/ |
| DSH | RPNH_DSH_Codec_Offline_Candidate_20261008.zip | rpnh-dsh-session-codec/（另有 rpnh-dsh-codec-independent-review/） |
| OC | RPNH_OpenCode_11835_Candidate_Local_Gate_20261008.zip | rpnh-opencode-candidate-gate/ |

- `PYTHON`：已有且获准的解释器绝对路径；不能直接使用历史 `/workspace/.../.venv/bin/python`。
- `PACKAGE`：当前 stage 的解压根；`REPO`：本阶段适用的精确源码/worktree；`OUT`：新建结果目录。先实际设定，再执行模板。
- CORE/HIST/LOW 原包装 runner 自动使用包内 `source/`，不是任意累计 checkout。它们会写包内 `evidence/`；应在一次性解压副本用全新 run name，保留原 ZIP 和历史 evidence。DSH/OC wrapper 同样绑定自身 `source/`，即便其他位置已有累计树也不会自动改测累计树。
- `source/` 有时只是改动白名单，有时是有限冻结快照，均不能当完整部署覆盖物。READER 7、EFFORT 12、CHIST 21 文件 source 尤其不能独立运行完整产品。
- stage 正确顺序：main→S1→CORE→HIST→LOW；R1v2→R2；READER+EFFORT→CHIST→C161；DSH/OC 各自独立。完整组合须新 source identity、新 gate 和真实复验，不能继承各 stage PASS。
- 包装 hash/CRC/净 apply 校验、历史 XML 解析、D0、实际 AF_UNIX、stock UI、模型执行是不同结果类别。本文不把任何一个替换为另一个。

来源：v5 `LOCAL_HANDOFF_ORDER_ZH.md`、`PATCH_CONFLICT_AUDIT_ZH.md`；各包下列入口原文。

## 2. 环境与版本：范围约束和历史实测必须分开

### 2.1 产品声明的范围（不是精确锁）

`C161::source/pyproject.toml`、`DSH::source/pyproject.toml` 与 `OC::source/pyproject.toml` 的产品依赖：

- Python `>=3.11`，列出的 classifiers 为 3.11/3.12/3.13，POSIX Linux。
- `jsonschema>=4.20,<5`、`packaging>=24,<27`、`websockets>=12,<16`。
- test extra：`pytest>=8,<9`、`numpy>=1.26,<3`、`scipy>=1.11,<2`。
- 构建依赖 `setuptools>=77`。构建工具不是运行已准备 gate 必须安装的理由。
- 产品版本为 `rpnh-harness 0.1.0rc2`。CORE/HIST 环境里记录的已安装 distribution `rpnh-harness 0.1.0rc1` 是当时环境事实，不得替换候选源码身份；必须核实际 `cpn` import 来源。

这些范围不能写成统一 `requirements-lock`，也没有证据证明随便一个满足范围的环境都已通过全部 stage。核心冻结快照不含根级 pyproject；不要借别 lane 的 source 拼接补齐。

### 2.2 各 stage 可以证明的历史环境

| stage | 原文明确的版本事实 | 精确锁限制与出处 |
|---|---|---|
| S1 | Python 3.12.14、pytest 8.4.2、jsonschema 4.26.0 | 历史实测，不是强制环境 lock；`S1::evidence/python-environment.json` |
| CORE | Python 3.12.14、pytest 8.4.2、jsonschema 4.26.0、Linux 6.18.44 x86_64/glibc2.41 | 完整已安装 distribution 清单在 `CORE::evidence/python-environment.json`；不等于最小依赖集合 |
| HIST | 同上三版本与 OS | `HIST::evidence/python-environment.json` |
| LOW | Python 3.12.14、pytest 8.4.2、pluggy 1.6.0 | `LOW::evidence/final-author.log`、`final-core.log`、`review/REVIEW.md`；该包没有独立 jsonschema 精确版本锁，勿仅凭共用 venv 路径补写 4.26.0 |
| R1v2 | 文档要求已有 Python 3.11+；历史命令记录已有 venv | `R1::LOCAL_GATE_ZH.md`、`COMMANDS.md`；本包没有独立完整 Python/pytest/jsonschema 精确锁 |
| R2 | Python 3.12.14、pytest 8.4.2、jsonschema 4.26.0 | `R2::runtime-provenance.json`、`review/independent-runtime-provenance.json` |
| READER | Python 3.11+、既有 pytest/jsonschema；默认 Python 当时缺 pytest | `READER::README_ZH.md`、`LOCAL_GATE_ZH.md`、`COMMANDS.md`；未提供三者统一精确锁 |
| EFFORT | 既有 pytest/jsonschema/websockets | `EFFORT::LOCAL_COMMANDS.md`；没有独立精确版本锁 |
| CHIST | 既有 pytest/jsonschema/websockets；历史默认 Python 缺 pytest | `CHIST::native-gate/NATIVE_GATE_ZH.md`、`COMMANDS.md`；没有独立三者精确锁 |
| C161 | pytest 8.4.2（独审）；其余产品范围如上 | `C161::independent-review/IMPLEMENTATION_REVIEW_ZH.md`、`source/pyproject.toml`；不能把源码1044-file lock解释成环境 lock |
| DSH | Node 24.19.0、Python 3.12.14；portable gate 强制 Node major=24 | `DSH::evidence/freeze/portable-runner-smoke/results.json`、`scripts/run_pure_gates.py`；pytest/jsonschema只检查存在，精确小版本未锁 |
| OC | Python 3.11+ 与预置项目测试依赖 | `OC::README_ZH.md`、`source/pyproject.toml`；未给独立完整精确环境 lock |

CORE/HIST 完整历史分发版本：attrs 26.1.0、iniconfig 2.3.0、jsonschema 4.26.0、jsonschema-specifications 2025.9.1、markdown-it-py 4.2.0、mdurl 0.1.2、numpy 2.5.3、packaging 26.3、pip 25.0.1、pluggy 1.6.0、Pygments 2.21.0、pytest 8.4.2、PyYAML 6.0.3、referencing 0.37.0、rpds-py 2026.6.3、rpnh-harness 0.1.0rc1、scipy 1.18.1、typing_extensions 4.16.0、websockets 15.0.1。仅为可重建证据，不能自动安装或当作每包最低需求。

### 2.3 原生/JS前提

- Codex：默认精确 `codex-cli 0.155.0`；C161 增加显式 `candidate-0.161.0`，精确 `codex-cli 0.161.0`，默认仍 `pinned-0.155.0`。需既有指定 binary、AF_UNIX/WebSocket 与真实交互终端/PTY；版本、realpath、SHA256分别留证。原文没锁一个通用 binary digest，不得猜。来源：`C161::source/cpn/frontend/codex_compatibility.v1.json`、两份 Codex native gate。
- DSH：原生产 `0.1.6-alpha.2` / commit `ddefc45fbc7f8e46dd73185e68295696d1297887`；新 codec 候选 `0.2.1-alpha.1` / `5badb15009ae1756c3afe0ae0cef1faafc290ccc`，尚未 production admission。旧 pin `pnpm@11.7.0`、其 upstream `pnpm-lock.yaml` frozen dependencies；engine `^22.19.0 || >=24.0.0`，tested Node major 24。纯 gate 比 engine 更严格，必须现成 Node24。来源：`DSH::source/integrations/dsh/UPSTREAM.json`、`IMPLEMENTATION_STATUS.md`、`LOCAL_VALIDATION.md`、`scripts/run_pure_gates.py`。
- DSH ZIP 不含完整已安装 upstream/node_modules/锁文件运行环境；需要现成精确 upstream 与其依赖。不能以本包只读上游摘录代替可运行 checkout；新 upstream typecheck 的具体工具版本/完整命令未在本地交接页锁定，应按对应已准备官方 workflow核实，不能自造 `pnpm typecheck` 结论。
- OpenCode：默认 `1.18.32` / commit `545f51d26cc39a907d2867492d498d9607ea5fa4`，显式 certification-only `1.18.35` / commit `53d1eabb61e21162157817bf677da0a4ad3332e3`。只支持 Linux/WSL2 原生 gate，需可信官方 stock Linux ELF 的绝对路径，拒绝 wrapper/PATH替代。`OC::source/tests/opencode_candidate_support.py::CandidateRun.prepare` 还检查 `pty`、`fcntl`、`termios`、`jsonschema`、`numpy`、`scipy`，并要求 `source` 正是 Git top-level。官方 release 来源/digest须另留证，脚本的 version/hash不能证明官方发行来源。
- 通用工具：现有 Python、Git（apply/hash/revision）、Bash（shell wrappers）；Node/pnpm只属于需要它们的DSH gate。没有要求为了 Python D0 安装 Codex/OpenCode/DSH runtime。

## 3. 核心链入口与命令

### 3.1 S1

身份与入口：`S1::LOCAL_ACCEPTANCE.md`、`VALIDATION.md`、`source-identity.json`、`tools/verify_changes.py`。

```sh
"$PYTHON" "$PACKAGE/tools/verify_changes.py" --source "$REPO" --manifest "$PACKAGE/source-identity.json"
cd "$REPO"
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" "$PACKAGE/tools/offline_pytest.py" -p no:cacheprovider -q \
  tests/test_static_lease_reads.py tests/test_static_lease_interactions.py \
  tests/test_static_lease_exact_selection.py tests/test_marking_modularization.py \
  tests/test_petri_marking_delta.py tests/test_optional_resource_request.py \
  tests/test_firing_activity.py --junitxml="$OUT/s1-author.xml"
"$PYTHON" "$PACKAGE/tools/run_independent_review.py" --source "$REPO" --mode pytest --junitxml="$OUT/s1-independent.xml"
```

独审脚本还支持 `--mode lowlevel`、`indices`、`empty-consume`、`read-edit`，各单独执行和留日志；不能把循环断言数加成pytest cases。另有既有纯回归，来源`S1::independent_review/PORTABLE_REPLAY.md`。从`REPO`运行：

```sh
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" "$PACKAGE/tools/offline_pytest.py" -p no:cacheprovider -q \
  tests/test_marking_modularization.py tests/test_registered_operation_recovery.py tests/test_structural_evidence.py \
  -k 'not test_inspector_routes_real_execution_and_preserves_exact_request and not test_scheduler_cannot_turn_disabled_operation_into_enabled_one' \
  --junitxml="$OUT/s1-independent-regressions.xml"
```

重要：原作者71项中有fresh Python只读冷恢复子进程；`tools/offline_pytest.py`只禁socket/URL，不全禁subprocess。若额外政策不允许该子进程，应明确 NOT_RUN/排除及新计数，不把改变后的集叫原71通过。作者71、独审42中13重叠，100 unique pytest；4个read/edit探针另列。来源：`S1::VALIDATION.md`、`independent_review/PORTABLE_REPLAY.md`、两个runner。

Native没有一键脚本：`LOCAL_ACCEPTANCE.md`给并发共享lease、同transition多occurrence、stop/drain、重入、variable共存、exact selection与dynamic-change场景。现有原OwnerEventLoop/确定性HOST条件具备且授权后可以逐场景实验；全部原native和全仓gate在此包是NOT_RUN。S1不授予child origin/launch。

### 3.2 CORE（精确S1 979-file输入→992-file输出）

```sh
cd "$PACKAGE"
"$PYTHON" tools/verify_identity.py
RPNH_PYTHON="$PYTHON" bash tools/run_final_offline.sh local-unique-core
"$PYTHON" independent-review/run_d0.py "$PACKAGE/source" "$OUT/core-independent.json" \
  -v --tb=short -p no:cacheprovider "$PACKAGE/independent-review/tests"
```

原入口：`CORE::REPRODUCE.md`、`tools/run_final_offline.sh`、`independent-review/run_d0.py`。作者final wrapper选31 core+68 adjacent；独审40与合并最终139 unique的关系见 `VALIDATION.md`/`independent-review/JOINT_FINAL_COUNTS.json`，不累加窗口。明确排除：

- `tests/test_static_lease_reads.py::test_new_process_cold_registry_reconstructs_active_and_settled_refs`
- `tests/test_invocation_functional_boundary.py::test_functional_modules_import_without_preloading_invocations`
- `tests/test_operation_functional_split.py::test_operation_facade_wrappers_delegate_and_preserve_contracts`

`inputs/S1-source`已含S1；净重建仅在全新复制目录apply根级`H7-core.patch`一次，再用`tools/source_manifest.py`与`evidence/final21-source-after.json`比较。`evidence/source-v1/H7-core.patch`为历史旧失败版本。`tools/*build*`、`create_h7_schemas.py`、`wire_core_hooks.py`、`harden*`、`final_hardening.py`不是安装或复验入口，不能重跑改冻结源。

### 3.3 HIST（992→994）

```sh
cd "$PACKAGE"
"$PYTHON" tools/verify_identity.py
RPNH_PYTHON="$PYTHON" bash tools/run_final_offline.sh local-unique-history
RPNH_PYTHON="$PYTHON" RPNH_SOURCE="$PACKAGE/source" REVIEW_RESULTS_DIR="$OUT/history-review" \
  bash review/tools/run-review.sh
```

来源：`HIST::REPRODUCE.md`、`tools/run_final_offline.sh`、`review/REVIEW.md`、`review/tools/run-review.sh`。最后runner强制994-file identity `a3af1d85ffbc8acac72e719fed316ed3c848229fce92026c62e571cb77a913a2`，不能拿去验证LOW或跨lane累计树。

作者247=108 history+31 core+68 adjacent+40原core独审；新增独审51，最终298 unique。core的139是其中回归，不再加一次。沿用上述三项subprocess排除。净重建输入`inputs/H7-core-source/`只套根级`acceptance-history.patch`一次，不能再套CORE。

HIST只补historical mechanical validator：VALID不是receipt、fresh permission或child重启许可。H7后继 execution_generation/publish/abandon、完整child binding尚不可达/未验。来源：`HIST::VALIDATION.md`、`review/REVIEW.md`。

### 3.4 LOW（994→999）

```sh
cd "$PACKAGE"
RPNH_PYTHON="$PYTHON" bash tools/run_final_offline.sh local-unique-lowering
RPNH_PYTHON="$PYTHON" REVIEW_RUN_NAME=local-unique-lowering-review bash tools/run_independent_offline.sh
```

来源：`LOW::REPRODUCE.md`及两个shell。author wrapper精确选28新增+516 compiler/ControlIR/serializer+177 core/history/static=721；独审wrapper46新增+1 ordinary-revision=47；最终768 unique。原review shell采用原`reviewed-source/`布局，消费端应使用包根portable wrapper，不能让缺路径变成额外下载/补源码。

只排除cold-process节点；额外6 native-net-operation cases因冻结输入缺`examples/net_operations/live_agent_replacement.py`无法collect，NOT_RUN，不能从另包复制该文件凑通过。净重建从`inputs/acceptance-history-source/`复制到全新目录，只套`bound-child-declarations.patch`，与`evidence/FINAL_SOURCE_MANIFEST.json`比较。来源：`LOW::REPRODUCE.md`、`JOINT_TEST_COUNTS.json`，v5 `LOWERING_STATUS.md`。

### 3.5 H7必须补实现，不是换个native环境即可跑通

最终LOW只返回`FrozenBoundChildDeclarations`，intent拒绝它，`require_execution_materials()`保持`ParentChildUnsupported`。当前可直接复验的是各阶段D0和受支持机械origin/声明冻结，不是完整执行材料。仍缺：

1. 原selected-installed HOST有限public/opaque-binding完整material contract与两类payload normalizers。现有`NEXT_REGISTERED_MATERIAL_CONTRACT.md`只是原候选的后续提案，保持设计待修订/UNIMPLEMENTED，不作为新的获审设计或可执行stage。本累计交付另按组包方提供的最新public-material独审阻塞状态处理；该外部最新状态不是本次12包原文审计得出的新设计验收结论。
2. 原task-lock封存bundle、fresh commit唯一ticket、原Popen observation生产issuer。
3. 原OwnerEventLoop双向SO_PEERCRED/birth身份、严格完整receipt交付/消费、sticky-stop顺序。
4. 任何可写Core/EventStore/SQLite之前typed exclusive target reservation；两类原AgentTask/Module wrapper实际消费它，EEXIST零触碰。
5. trusted child source/bootstrap/origin与receipt/reservation接线；exact child binding、same-cut terminal、parent registered completion与Success。
6. H7b observation-only recovery及H8消费迁移另阶段。

这些项当前是UNIMPLEMENTED/UNSUPPORTED + NOT_RUN，不能把注入`_NativeBoundaryEvidence`的D0算真实issuer。N01–N36为将来设计验收矩阵，入口在 `CORE::inputs/frozen-contract-v3/ACCEPTANCE.md` 与 `H7_PARENT_CHILD_SEAM_DESIGN.md`；不是12包内现成可运行native脚本。CORE旧`NATIVE_HANDOFF.md`第6/7项须由HIST/LOW限定完成范围更新理解，不能仍笼统说history/lowering全没实现。

来源：`LOW::IMPLEMENTATION_SCOPE.md`、`NATIVE_HANDOFF.md`、`NEXT_REGISTERED_MATERIAL_CONTRACT.md`；`CORE::NATIVE_HANDOFF.md`；v5 `LOCAL_HANDOFF_ORDER_ZH.md`、`LOWERING_STATUS.md`。

## 4. RSI链

### 4.1 R1 v2

先根据`R1::supersession.json`和`file-manifest.json`二选一：无旧R1用`inert-iteration-profile-v2-full.patch`；旧六文件精确等于superseded hashes才用`r1-v1-to-v2-terminal.delta.patch`。已v2不再apply；不能两个都用。

```sh
cd "$REPO"
"$PYTHON" -m pytest -q tests/test_iteration_profile.py tests/test_compiler_json_contract.py --junitxml="$OUT/r1.xml"
"$PYTHON" -m py_compile cpn/rpnh/iteration_profile.py tests/test_iteration_profile.py
```

来源：`R1::LOCAL_GATE_ZH.md`、`COMMANDS.md`。150历史项=141编译/schema+9真实临时SQLite/RunOwner terminal steps；非socket/Orchestrator native。源码以715468d实测，后继d92/H2a需实际stage重新验证。真实physical outcome_unknown/recovery、author CAS、R2端到端另门槛；旧R1空terminal config不得runtime补洞。

### 4.2 R2

```sh
cd "$REPO"
"$PYTHON" -m pytest -q tests/test_iteration_profile.py tests/test_compiler_json_contract.py \
  -o junit_family=xunit1 --junitxml="$OUT/r2-r1-compiler.xml"
"$PYTHON" -m pytest -q examples/rsi_workflows/tests/test_runtime.py -k 'not original_orchestrator' \
  -o junit_family=xunit1 --basetemp="$OUT/r2-step-runs" --junitxml="$OUT/r2-step.xml"
# 以下是真实AF_UNIX的独立native门槛，需相应授权/环境。
"$PYTHON" -m pytest -q examples/rsi_workflows/tests/test_runtime.py -k original_orchestrator \
  -o junit_family=xunit1 --basetemp="$OUT/r2-native-runs" --junitxml="$OUT/r2-native.xml"
```

来源：`R2::LOCAL_RETURN_TASK_ZH.md`、`LOCAL_GATE_ZH.md`。原生四项要求默认OwnerEventLoop/AF_UNIX，历史EPERM保留。明确请求重现替身才另跑`--rsi-transport=pipe`全文件，记D0；pipe来自原`examples/tool_pipeline/tests/pipe_transport.py`，不能当native fallback。`h2a_readback.py`是只读复核入口；从实际repo运行 `PYTHONPATH="$REPO" "$PYTHON" "$PACKAGE/h2a_readback.py" "$OUT/r2-native-runs" "$OUT/r2-readback.json"`，两个位置参数依次为固定basetemp和输出JSON。它只读已生成且可识别的completed fixtures；没有结果就失败，不会替你创建或补执行。

R2只新增五个example文件，依赖R1v2，不依赖H7； author/CAS、R3 registered-model/scripted port、physical unknown、完整R4 campaign仍NOT_RUN/未完成。作者15与独审重跑15+5不等于35 unique。来源：`R2::DEPENDENCIES.json`、`LOCAL_RETURN_TASK_ZH.md`、v5任务书。

## 5. Codex链

### 5.1 READER

```sh
"$PYTHON" "$PACKAGE/verify_package.py"
"$PYTHON" "$PACKAGE/run_local_gate.py" --repo "$REPO" --junitxml "$OUT/reader.xml"
```

来源：`READER::README_ZH.md`、`LOCAL_GATE_ZH.md`、`run_local_gate.py`。runner核全部7个冻结文件后跑32（14新history+12原Registry+6独审）；旧作者57与两依赖overlay重跑不再累加。9项native执行/socket明确deselected，见`deselected-tests.txt`。full docs build NOT_RUN。旧`independent-review/run_guarded.py`不应替代portable runner。

### 5.2 EFFORT

```sh
"$PYTHON" "$PACKAGE/verify_package.py"
"$PYTHON" "$PACKAGE/verify_patch.py" "$REPO"     # 适用于未应用前的preimage核验
"$PYTHON" "$PACKAGE/verify_package.py" --candidate "$REPO"  # 已应用后核最终字节
cd "$REPO"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "$PYTHON" -m pytest -q \
  tests/test_codex_compat.py tests/test_frontend_boundary.py \
  --deselect=tests/test_frontend_boundary.py::test_codex_frontend_uses_external_socket_for_absent_long_root \
  --junitxml="$OUT/effort-offline.xml"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "$PYTHON" -m pytest -q \
  "$PACKAGE/review/test_review_codec_boundaries.py" --junitxml="$OUT/effort-independent.xml"
# 真实AF_UNIX单项：另授权后执行。
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. "$PYTHON" -m pytest -q \
  tests/test_frontend_boundary.py::test_codex_frontend_uses_external_socket_for_absent_long_root \
  --junitxml="$OUT/effort-af-unix.xml"
```

来源：`EFFORT::LOCAL_COMMANDS.md`。第一组历史43 pass/1 deselected，独审4；完整两模块44（含socket）可在获准本地执行，单项与完整集重叠不加计。该socket项用`/bin/true`，不是stock TUI证明。

stock0.155 picker/config/None-vs-`none`/冷重开流程在`LOCAL_VALIDATION_ZH.md`，没有独立一键安全runner；`rpnh --frontend codex`可能执行模型，不得当零调用smoke。安全隔离fixture/支持入口没有就NOT_RUN。history分页旧缺口后来由CHIST补候选，不能照旧STATUS再要求用户另找未知包。

### 5.3 CHIST

包装：`"$PYTHON" "$PACKAGE/verify_delivery.py"`。

```sh
cd "$REPO"
"$PYTHON" -m pytest -q tests/test_codex_history.py tests/test_codex_compat.py \
  tests/test_main_thread_history.py tests/test_frontend_session_access.py tests/test_frontend_boundary.py \
  -k 'not test_codex_frontend_uses_external_socket_for_absent_long_root' --tb=short \
  --junitxml="$OUT/codex-history.xml"
```

来源：`CHIST::COMMANDS.md`（119 pass/1 deselected）。另43个原Registry/MainSession节点完整argv在`native-regression-command.json`，只替换第0项历史解释器和最后`--junitxml`输出，保持中间节点不变，从`REPO`运行；它们仍是fake child观察的离线回归，“native-regression”文件名不意味着stock native。另9native执行节点未选，独审128与作者有重叠。

具备授权/环境后，现成stock0.155 gate：

```sh
"$PYTHON" "$PACKAGE/native-gate/prepare_fixture.py" --repo "$REPO" --output "$OUT/cold" --turns 60
"$PYTHON" "$PACKAGE/native-gate/prepare_fixture.py" --repo "$REPO" --output "$OUT/pending" --turns 6 --pending
"$PYTHON" "$PACKAGE/native-gate/run_native_gate.py" --repo "$REPO" --fixture "$OUT/cold" --codex "$CODEX_0155" --log "$OUT/cold-run-1.jsonl"
```

之后同cold root新log重开；pending root同入口首次/重开，共4份独立log。来源：`CHIST::native-gate/NATIVE_GATE_ZH.md`。prepare写全新合成Registry、核21源文件，不能用用户真实会话；run用真实stock TUI+原Unix transport，拒绝执行RPC/TaskControl.start。60turn/120item需真实请求+UI原文/隐私哨兵检查。pending未执行重连不是running-worker认证。原本只有prepare两turn纯Registry烟测，run/native全NOT_RUN。

### 5.4 C161：严格保留1044-file锁

- `BASE_SOURCE_LOCK.json`锁1041-file前置组合；`file-manifest.json`锁最终1044-file subset。
- manifest SHA256：`76ebe5ff56404901014110ae406c39a8e26980f6d26edf8b4e7a0ea54a37d3b7`。
- source fingerprint：`316a06aad41653e890d16a19519d07b09042c54c4961586b54cf2842b10d8658`。
- 根级patch：`720bcf2bd95886a26425e00caec80eab0fbcf0eec0481202ca54ce88a5f8a3c8`。

前置READER/EFFORT/CHIST加d92两产品改动已在该组合内；不重套H2 overlay。不把S1/H7/DSH/OC混入后改expected hash，旧gate按锁定路径核字节，遇不同必须拒绝。新的多lane组合要独立新manifest/gate；旧1044锁永不改。

包装：`"$PYTHON" "$PACKAGE/verify_package.py"`（静态字节+临时净apply，不是产品测试）；`freeze_package.py`依赖作者旧workspace，不是消费端setup。

离线重放命令据`reports/VERIFICATION.json`和对应JUnit节点构造，包内没有另一个统一D0 shell入口：

```sh
cd "$REPO"
PYTHONPATH=. "$PYTHON" -m pytest -q tests/test_codex_0161_candidate.py tests/test_codex_history.py --junitxml="$OUT/c161-history.xml"
PYTHONPATH=. "$PYTHON" -m pytest -q \
  tests/test_codex_compat.py::test_codex_0155_handshake_projects_only_rpnh_backend \
  tests/test_codex_compat.py::test_codex_handshake_rejects_unaccepted_initialized_and_correlates_errors \
  tests/test_codex_compat.py::test_codex_schema_candidate_does_not_expand_native_version_pin \
  'tests/test_codex_compat.py::test_codex_effort_wire_codec_matches_official_schema[0.155.0]' \
  'tests/test_codex_compat.py::test_codex_effort_wire_codec_matches_official_schema[0.161.0]' \
  tests/test_codex_compat.py::test_codex_none_marker_does_not_become_an_unlisted_effort \
  tests/test_frontend_boundary.py::test_manifest_matches_actual_method_dispatch_and_has_no_static_pass_claim \
  tests/test_frontend_boundary.py::test_frontend_argv_preserves_remote_and_disabled_features \
  --junitxml="$OUT/c161-default.xml"
PYTHONPATH=. "$PYTHON" -m pytest -q "$PACKAGE/native-gate/test_handoff_offline.py" --junitxml="$OUT/c161-handoff-fake.xml"
```

对应122/8/16历史cases；16项为fake runner/logger/analyzer，不能写native PASS。精确8节点来源：`C161::reports/default-regression-junit.xml`。非本次实际运行，任何新环境collection差异必须保留。

具备授权/环境后，native现成入口：

```sh
"$PYTHON" "$PACKAGE/native-gate/prepare_pair.py" --repo "$REPO" --output "$OUT/synthetic-pair"
"$PYTHON" "$PACKAGE/native-gate/run_native_pair.py" --repo "$REPO" --fixtures "$OUT/synthetic-pair" \
  --output "$OUT/native-evidence" --codex-0155 "$CODEX_0155" --codex-0161 "$CODEX_0161"
"$PYTHON" "$PACKAGE/native-gate/analyze_log.py" "$OUT/native-evidence/0.161.0-cold-first.jsonl"
"$PYTHON" "$PACKAGE/native-gate/run_object_rpc_probe.py" --repo "$REPO" \
  --fixture "$OUT/synthetic-pair/cold" --output "$OUT/object-rpc.json"
```

来源：`C161::native-gate/NATIVE_GATE_ZH.md`及同名脚本。两版本顺序运行，各cold首次/重开、pending首次/重开；共用同一canonical roots，不复制后要求ID不变。缺一binary只BLOCK对应lane。子入口`run_profile_gate.py --repo ... --fixture ... --codex ... --log ... --profile pinned-0.155.0|candidate-0.161.0`与`prepare_fixture.py --repo ... --output ... --turns ... [--pending]`供上层组合调用。

逐log analyzer最多给`RPC_COVERAGE_COMPLETE_UI_REVIEW_REQUIRED`，不认证stock UI；object probe是真Unix WebSocket但不启动stock，不可冒充stock发object。`native-gate/NATIVE_STATUS.json`明确0.155/0.161 stock、object RPC、native certification均NOT_RUN/NOT_ESTABLISHED，Rust也未编译。

## 6. DSH

```sh
"$PYTHON" "$PACKAGE/scripts/verify_bundle.py"
"$PYTHON" "$PACKAGE/scripts/verify_bundle.py" --checkout "$REPO"  # preimage/13 changed与12 protected检查
"$PYTHON" "$PACKAGE/scripts/run_pure_gates.py" --output "$OUT/dsh-pure"
```

portable pure gate固定包内1346-file snapshot，只跑Node55、Python codec/parity39、launcher/source9与detached factory文本变换；不测owner/native/typecheck。`--checkout`检查的是未应用preimage，不是接受任何已修改累计树。来源：`DSH::scripts/verify_bundle.py`、`scripts/run_pure_gates.py`、`LOCAL_VALIDATION.md`。

实际已应用独立checkout上的原纯命令：

```sh
cd "$REPO"
node --test integrations/dsh/src/message-codec.test.ts
"$PYTHON" -m pytest -q tests/test_dsh_message_codec.py
"$PYTHON" -m pytest -q tests/test_dsh_distribution.py -k 'not distribution_contains_runtime_and_public_source_assets and not console_help_works_from_installed_distribution'
# 以下为真实owner/socket门槛。
"$PYTHON" -m pytest -q tests/test_dsh_backend.py tests/test_dsh_long_path.py tests/test_net_view_dsh_adapter_integration.py
```

原owner结果12 pass/22 AF_UNIX权限失败；后续retry中断exit130不计PASS。两项build/installed-console未跑，不能称发行认证。

旧runtime gate的现成入口为 `bash integrations/dsh/verify.sh "$DSH_PINNED_CHECKOUT"`，仅精确`ddefc45...`且依赖已准备时有意义。警告：此脚本会调用`patch_upstream.py`修改upstream、复制integration TS、写`vitest.rpnh.config.ts`和results，再`pnpm exec vitest run --config vitest.rpnh.config.ts`；不是只读verify，不能放进自动D0检查。`prepare.sh`同样会patch/copy；`run.sh`会prepare后启动应用，不是零执行验收捷径。来源：`DSH::source/integrations/dsh/{verify.sh,prepare.sh,run.sh}`。

新revision现成只有dependency-free `projectHistory(...,candidateRevision)`与纯测试；`LOCAL_VALIDATION.md`要求精确upstream typecheck、真实V4 Session create/restore/header/flush一致性、V4→V3拒绝、零输入Cordis/Session/Agent生命周期，但没有绕production pin的成品runner或精确一键命令。这是需要准备/审查验证接线的独立gate；新runtime执行admission、bridge/backend identity、最终support manifest仍待实现/审阅。旧/新typecheck、真实DSH cold reopen/lifecycle均NOT_RUN。

## 7. OpenCode

```sh
"$PYTHON" "$PACKAGE/verify_package.py"
"$PYTHON" "$PACKAGE/run_g1.py" --output "$OUT/opencode-g1"
```

来源：`OC::README_ZH.md`、`run_g1.py`、`RESULTS_ZH.md`。G1精确274纯case（169+41+64），禁socket/Popen/thread/fork/PTY；不可用`tests/test_opencode*.py`通配替代，否则带入旧native/Registry边界。另33独审有重叠，不能简单加成认证总数。

真正Git checkout与官方现有ELF binary、Linux/WSL2及native授权均具备时：

```sh
cd "$REPO"
"$PYTHON" -m pytest -q tests/test_opencode_candidate_native.py \
  --opencode-certify-version=1.18.35 --opencode-certify-binary="$OPENCODE_11835" \
  --opencode-certify-lane=contract
"$PYTHON" -m pytest -q tests/test_opencode_candidate_native.py \
  --opencode-certify-version=1.18.35 --opencode-certify-binary="$OPENCODE_11835" \
  --opencode-certify-lane=registry-read
```

对照同命令改精确`1.18.32`与对应binary。来源：`OC::NATIVE_GATE_ZH.md`、`source/tests/{conftest.py,opencode_candidate_support.py,test_opencode_candidate_native.py}`。显式请求缺参数/依赖/binary/版本或未执行lane必须fail/blocked，不可collect-only、skip或`-k/-m`排除后叫PASS。

G2 contract是stock+ApplicationDouble有限bootstrap/help/prompt/fixture answer；G3是stock+原Gateway/Application+两committed-turn合成Registry，准备2次fake-port与测量新增零effects分别记。保留原tick和owner lease；`/rpnh-tasks`有reconcile，不能当零写测点。两个native smoke、官方SDK编译均NOT_RUN；即使未来通过也只叫`passed-limited-smoke`。完整picker/effort、强制SSE重连、failure/abort、并行session、长history、active-worker recovery、真实provider/plugins、OS出站网络隔离不在已实现smoke范围。

## 8. 交付与复验中不能隐去的风险

1. 不重跑生产、模型、native或安装来“确认本文”；本次仅静态核查。授权不足/依赖缺失按lane标BLOCKED/NOT_RUN。
2. 原包里历史绝对路径、旧文档和旧失败必须保留。READER旧组合页的R1待修、EFFORT旧history/0.161缺口、CORE旧history/lowering待办，只在新总说明中按后继范围解释，不改原件。
3. 单一新shell wrapper不能静默把所有stage改用一个guard：S1允许冷读Python子进程；H7各自明确排除；DSH pure wrapper主动spawn Node/pytest而无owner；Codex native/OpenCode要真实client/transport。必须标清究竟测了哪个边界。
4. “一个包”可包含全部非产品材料、stage补丁、fixture helper、原runner与设计任务书；外部仍需授权的完整checkout/既有解释器依赖/确切可信客户端/真实OS能力。尚未实现的H7路径和未准备的DSH新native接线不能变成打包遗漏，也不能说拿到ZIP即可全部执行。
5. 新run不得覆盖旧evidence目录/文件。每阶段记录UTC/argv/cwd/真实exit、source-before/after、collection与JUnit、deselected/blocked、binary身份、transport、模型/worker/child计数。只读SQLite可创建SHM/空WAL，不等于canonical authority写；不能用immutable=1或忽略真实非空WAL获取表面零写。
6. 核心/RSI/Codex/DSH/OC历史PASS互不继承。祖先final-hash在合法后继树可能拒绝，这是预期stage边界；不得回滚源码或改旧manifest来使它们同时全绿。
