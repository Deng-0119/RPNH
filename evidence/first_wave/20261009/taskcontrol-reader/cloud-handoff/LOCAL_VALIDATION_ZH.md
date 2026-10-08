# H2a 本地原生补验任务书

目标：在既有、正常支持 AF_UNIX 的授权 Linux/WSL2 测试环境，对冻结 H2a 五文件补齐原生门槛，并返回可对应到真实测试字节的结果。仅此范围；不调用模型或外部 provider，不启动 Actions/业务评分，不安装软件，不改安全配置，不以 pipe/mock transport 宣称 native 通过。

## 0. 核包和本地 checkout

以下 Bash 变量须替换成真实绝对路径。EVIDENCE 是仓库外新建的证据目录。包中并非完整仓库。

```bash
export PKG=/absolute/path/rpnh-taskcontrol-reader-convergence
export REPO=/absolute/path/RPNH
export PY=/absolute/path/existing-test-env/bin/python
export EVIDENCE=/absolute/path/new-h2a-native-evidence
mkdir "$EVIDENCE"
cd "$REPO"
git remote get-url origin > "$EVIDENCE/origin.txt"
git rev-parse HEAD > "$EVIDENCE/head-before.txt"
git rev-parse 'HEAD^{tree}' > "$EVIDENCE/head-tree-before.txt"
git status --porcelain=v1 --untracked-files=all > "$EVIDENCE/status-before.txt"
git show-ref --verify refs/remotes/origin/main > "$EVIDENCE/origin-main-local-ref.txt" 2>&1
git ls-remote origin refs/heads/main > "$EVIDENCE/main-remote.txt" 2>&1
printf '%s\n' "$?" > "$EVIDENCE/main-remote.exitcode"
"$PY" --version > "$EVIDENCE/python-version.txt" 2>&1
"$PY" -m pytest --version > "$EVIDENCE/pytest-version.txt" 2>&1
"$PY" "$PKG/verify_package.py" --repo "$REPO" --state baseline > "$EVIDENCE/preflight.json" 2>&1
printf '%s\n' "$?" > "$EVIDENCE/preflight.exitcode"
```

核 origin 为 Deng-0119/RPNH；保留真实 HEAD、本地 ref、实时远端 main。网络读取失败按未核记录，不改网络。基线 ec9077 若已前进，先审差异；不能凭本包旧证据认定新主线自动兼容。已有 dirty 修改不得 reset/覆盖/自动 stash。四个旧 hash 或新增测试文件状态不符时停在应用前。脚本只验证目标路径，不证明全仓清洁。

## 1. 应用并冻结待测身份

仅在应用获授权且预检通过时继续：

```bash
git apply --check "$PKG/taskcontrol-reader-convergence.patch"
git apply "$PKG/taskcontrol-reader-convergence.patch"
git diff --check > "$EVIDENCE/diff-check.log" 2>&1
printf '%s\n' "$?" > "$EVIDENCE/diff-check.exitcode"
"$PY" "$PKG/verify_package.py" --repo "$REPO" --state final > "$EVIDENCE/final-file-verification.json" 2>&1
printf '%s\n' "$?" > "$EVIDENCE/final-file-verification.exitcode"
"$PY" "$PKG/capture_source.py" "$REPO" > "$EVIDENCE/tested-source-before.json"
```

每项必须真实成功，非零先诊断，不能继续覆盖。capture_source 记录 HEAD、HEAD Git tree、dirty、五文件身份，以及 tracked 文件和候选新增文件的完整 worktree 清单 SHA256。后者不是 Git tree ID；仅提供 HEAD 不足以识别未提交的实际测试字节。

## 2. 只读 result/status 和兼容回归

这些是离线 real-Registry/static-product 证据，不是 owner/socket 原生执行证据。

```bash
PYTHONPATH=. "$PY" -m pytest -q tests/test_task_control_registry_reads.py --junitxml="$EVIDENCE/H2a-readers.xml" > "$EVIDENCE/H2a-readers.log" 2>&1
printf '%s\n' "$?" > "$EVIDENCE/H2a-readers.exitcode"
PYTHONPATH=. "$PY" -m pytest -q tests/test_run_descriptor_reads.py tests/test_package_result_projection.py tests/test_opencode_application_boundary.py tests/test_basic_cli_task_switching.py --junitxml="$EVIDENCE/H2a-compatibility.xml" > "$EVIDENCE/H2a-compatibility.log" 2>&1
printf '%s\n' "$?" > "$EVIDENCE/H2a-compatibility.exitcode"
PYTHONPATH=.:examples/harnessaudit_office/tests "$PY" -m pytest -q examples/harnessaudit_office/tests/test_registry_reader_consumers.py --junitxml="$EVIDENCE/H2a-shared-reader.xml" > "$EVIDENCE/H2a-shared-reader.log" 2>&1
printf '%s\n' "$?" > "$EVIDENCE/H2a-shared-reader.exitcode"
PYTHONPATH="$REPO:$REPO/tests:$REPO/examples/harnessaudit_office/tests:$PKG/independent-review" "$PY" -m pytest -q "$PKG/independent-review/test_taskcontrol_independent.py" "$PKG/independent-review/test_large_descriptor.py" --import-mode=importlib --junitxml="$EVIDENCE/H2a-independent.xml" > "$EVIDENCE/H2a-independent.log" 2>&1
printf '%s\n' "$?" > "$EVIDENCE/H2a-independent.exitcode"
```

核文本字符串、object/array/null 结构化输出、complete/failed 原始 outcome、legacy JSON keys 和 task ID；body/descriptor 合法超过 4 MiB 可读，膨胀 backing file 不截断伪成功；累计非零 counts、当前 generation 不选旧 terminal、错误 provenance/identity、counts 和 decode 后同一 cut 重查。关注结果正文确实相同，而不是只看 terminal 存在。合成历史计数不是实际模型调用。

## 3. 原生 status、owner callback 与停止/恢复

仅运行下面明确 nodeid/筛选的既有离线 scripted 测试。禁止 pipe plugin、transport fallback 或 provider-backed profile；不要把整个 transport test 文件运行成其他网络/API 测试。

```bash
PYTHONPATH=. "$PY" -m pytest -q tests/test_task_frontend.py -k 'task_control or task_status' --junitxml="$EVIDENCE/H2a-native-status.xml" > "$EVIDENCE/H2a-native-status.log" 2>&1
printf '%s\n' "$?" > "$EVIDENCE/H2a-native-status.exitcode"
PYTHONPATH=.:tests "$PY" -m pytest -q tests/test_transport_completion_faults.py::test_accepted_host_reply_cancellation_is_not_owner_cancellation tests/test_transport_completion_faults.py::test_accepted_host_exception_preserves_later_completion --junitxml="$EVIDENCE/H2a-native-callbacks.xml" > "$EVIDENCE/H2a-native-callbacks.log" 2>&1
printf '%s\n' "$?" > "$EVIDENCE/H2a-native-callbacks.exitcode"
PYTHONPATH=. "$PY" -m pytest -q tests/test_task_frontend.py::test_resume_uses_persisted_transition_profiles_when_graph_swaps_them tests/test_task_frontend.py::test_interrupted_firing_does_not_publish_workspace_files tests/test_task_frontend.py::test_interruption_checkpoints_prior_workspace_action_and_discards_current --junitxml="$EVIDENCE/H2a-native-resume.xml" > "$EVIDENCE/H2a-native-resume.log" 2>&1
printf '%s\n' "$?" > "$EVIDENCE/H2a-native-resume.exitcode"
"$PY" "$PKG/capture_source.py" "$REPO" > "$EVIDENCE/tested-source-after.json"
```

callbacks 两项使用真实 OwnerEventLoop/AF_UNIX 和本地合成回调，核已接受回调不能被取消、异常不吞后继 completion；这些是本地待验项，本轮打包没有新增运行记录。status 批次含纯测试，不能将每项都称 native integration。恢复批次核持久 profile、当前 firing 丢弃和既有完成工作保留。与 H1 可能重跑同一 testcase，按 repo-relative nodeid 去重，executions 另记。

云端历史 native-status 是 6 PASS / 1 EPERM；native-resume 在 socket 构建即阻断，provider 尚未 dispatch、恢复断言未到。新的本地结果单独记录，禁止改写这些历史失败。

## 4. H1 并行与最终组合

`packaging/H1_COMPATIBILITY.json` 对照已交付 H1 七条与 H2a 五条路径，交集为空；带有既有 evidence-only 基线差异证明。本包没有吸入 H1 的未合入代码。

可由独立任务准备和预检 H2a。应用时若目标已有 H1，先验证其冻结七文件，明确记录组合来源；未知修改、冲突或当前授权不覆盖组合时停下核对。不可把 H2a 原始 1,335 项历史 source digest 说成组合源身份。

最终采用 H1+H2a 时，在同一组合源码上重跑第 2、3 节受影响门槛，并按 H1 任务书完成其 default native focused、原生 CLI/独立回读和生命周期 gate。先前分别通过或两补丁成功应用不构成组合测试。

```bash
export H1PKG=/absolute/path/RPNH_Owner_Entry_Local_Validation_20261008
"$PY" "$PKG/capture_source.py" "$REPO" --h1-package "$H1PKG" > "$EVIDENCE/combined-tested-source-before.json"
# 在同一源码上执行上述 H2a 与 H1 所列原生门槛，然后再次采集：
"$PY" "$PKG/capture_source.py" "$REPO" --h1-package "$H1PKG" > "$EVIDENCE/combined-tested-source-after.json"
```

## 5. 回传与结束标准

- 每批分别 PASS / FAIL / BLOCKED_ENV / NOT_RUN，附命令、实际退出码、原日志、JUnit、repo-relative testcase 清单和去重规则。不存在的 XML 不补造。
- 回传实际 tested version、HEAD tree、前后 worktree 清单 hash、dirty、patch hash、五文件 final hash；组合时包括 H1 七文件身份。前后源码不同就定位变化，不能转移旧 PASS。
- 原生环境或安全前置不满足时保留原异常及阶段，允许独立任务继续；不绕守卫、不换 pipe 标成功，不因 A1 host config/env 阻断搁置 H2a 交接。
- 保留失败运行用于本地诊断。对外证据剔除 DB、私有 profiles、token、key、.env、无关个人路径和缓存；若脱敏日志，记录原始/分发 hash、修改区间并重新解析 JUnit。
- 仅涉及本包的实际产品问题才修复并重新审阅/测试，新字节需新证据。完成要求是最终源码上 gate 实际闭合、原始失败有归类、源码身份和证据匹配；成功 apply 不算 tested。

本任务书不自动授权 commit、push、merge、Actions 或远端写入。后续代码与真实证据需作为同一逻辑交付供合并，具体动作按用户授权执行。
