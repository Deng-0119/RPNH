# H1 本地原生补验任务书

目标：在正常支持 AF_UNIX、原有安全条件自然满足的授权本地环境，对冻结七文件候选补齐 native gate。
只验证本包；不运行模型、外部 provider、Docker、GitHub Actions、业务 benchmark，也不强制全仓。
不放宽安全守卫、不更改 owner/权限或网络设置、不用 pipe 代替原生通过。

## 0. 先核源码与工作区

使用已有安装好测试依赖的仓库环境。以下是 Bash 示例，按本地真实绝对路径设置变量，不复制尖括号。
`PKG` 是解压后的包目录；`REPO` 是 RPNH 仓库；`PY` 是已有测试环境的 Python。
`EVIDENCE` 必须是仓库外一个新的绝对目录，避免生成的日志和数据库混入代码提交。

```bash
export PKG=/absolute/path/RPNH_Owner_Entry_Local_Validation_20261008
export REPO=/absolute/path/RPNH
export PY=/absolute/path/existing-test-env/bin/python
export EVIDENCE=/absolute/path/new-h1-native-evidence
mkdir "$EVIDENCE"
cd "$REPO"
git remote get-url origin > "$EVIDENCE/origin.txt"
git status --porcelain=v1 --untracked-files=all > "$EVIDENCE/status-before.txt"
git rev-parse HEAD > "$EVIDENCE/head-before.txt"
git branch --show-current > "$EVIDENCE/branch-before.txt"
git show-ref --verify refs/remotes/origin/main > "$EVIDENCE/origin-main-local-ref.txt" 2>&1
git ls-remote origin refs/heads/main > "$EVIDENCE/main-remote.txt" 2>&1
"$PY" --version > "$EVIDENCE/python-version.txt" 2>&1
"$PY" -m pytest --version > "$EVIDENCE/pytest-version.txt" 2>&1
"$PY" "$PKG/verify_patch.py" "$REPO" > "$EVIDENCE/preflight-apply-check.log" 2>&1
```

- 核对 origin 确为 `Deng-0119/RPNH`，分别保留真实 HEAD、本地 origin/main ref 和实时远端 main；
  不把一个过期 remote-tracking ref 当成实时主线。远端读取失败就保留原错误并如实标明未核。
- 已冻结代码基线是 `674252feb836f631c162979f177d1fe91f22559f`，已核 evidence-only 后继是
  `ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4`。如果本地主线更新，先审阅新增差异及受影响边界。
- dirty 工作不能被 reset、覆盖或自动 stash。本包任一旧 hash 不同、新测试文件已存在，或与其他候选
  重叠，立即停在应用前核对。不得混入 H2a TaskControl reader 或其他未合入修改。
- `verify_patch.py` 核六个原文件 baseline hash、新文件不存在、临时 apply 与七个 final hash。
  它不修改 REPO；exit 0 才能继续。它不证明整个仓库清洁或新主线语义自动兼容。

## 1. 应用并记录最终源码

仅在应用已被授权且上述核对通过的 checkout 内继续：

```bash
cd "$REPO"
git apply --check "$PKG/owner-entry-convergence.patch"
git apply "$PKG/owner-entry-convergence.patch"
git diff --check > "$EVIDENCE/diff-check.log" 2>&1
git status --porcelain=v1 --untracked-files=all > "$EVIDENCE/status-after-apply.txt"
git diff --stat > "$EVIDENCE/diff-stat.txt"
"$PY" - "$PKG/file-manifest.json" "$REPO" > "$EVIDENCE/source-after-apply.json" <<'PYCODE'
import hashlib,json,sys
from pathlib import Path
manifest=json.loads(Path(sys.argv[1]).read_text())
repo=Path(sys.argv[2]); out=[]
for row in manifest:
    digest=hashlib.sha256((repo/row['path']).read_bytes()).hexdigest()
    out.append({'path':row['path'],'sha256':digest,'matches_frozen':digest==row['final_sha256']})
print(json.dumps(out,indent=2))
raise SystemExit(0 if all(x['matches_frozen'] for x in out) else 1)
PYCODE
```

这里新增的是采证命令，没有新增测试。保留每一步真实退出码；任何非零都先诊断，不继续自动覆盖。

## 2. 默认 native focused（36 项）

不传 `--tool-pipeline-transport=pipe`，不加载 `agent_task_pipe_checks`。默认使用原生 OwnerEventLoop。
运行 34 项 focused 产品测试与 2 项既有 resource-continuation 回归；其中 pure 单元测试不涉及 socket，
不得把每个 testcase 都称为 transport integration。

```bash
cd "$REPO"
"$PY" -m pytest -q tests/test_orchestrator_boundary.py tests/test_harness_quiescence.py tests/test_harness_resource_continuation.py examples/tool_pipeline/tests --junitxml="$EVIDENCE/H1-native-focused.xml" > "$EVIDENCE/H1-native-focused.log" 2>&1
printf '%s\n' "$?" > "$EVIDENCE/H1-native-focused.exitcode"
```

检查同 Module 旧/新入口的 1/2 worker、完整数值结果、逐 firing 顺序/因果、并行进展与 join、
stop 排空且无新后继、兼容 factory、host loop/pool 各关闭一次、提前 stop 零 dispatch。
这些用例已包含在 focused 中，不必重复计数或另造重叠测试。

## 3. 三项既有 AgentTask 中断/恢复

直接运行原来的 native 测试，不使用云端 validation-only pipe plugin：

```bash
cd "$REPO"
"$PY" -m pytest -q tests/test_task_frontend.py::test_interrupted_firing_does_not_publish_workspace_files tests/test_task_frontend.py::test_resume_uses_persisted_transition_profiles_when_graph_swaps_them tests/test_task_frontend.py::test_interruption_checkpoints_prior_workspace_action_and_discards_current --junitxml="$EVIDENCE/H1-native-agent-task.xml" > "$EVIDENCE/H1-native-agent-task.log" 2>&1
printf '%s\n' "$?" > "$EVIDENCE/H1-native-agent-task.exitcode"
```

保留当前 firing 工作区丢弃、已完成工作区工作留存、精确持久化 profile 恢复与 stop/信号恢复结果。
这三项与上一节合计 39 个不重复 testcase；重跑另记 executions，不增加 unique。

## 4. 真实 native pipeline CLI 与独立只读回读

使用新的 run/output 目录，不复用已有 run；CLI 自带的固定 synthetic electricity Module 零模型。

```bash
cd "$REPO"
"$PY" -m examples.tool_pipeline.run --run-dir "$EVIDENCE/native-run" --output-dir "$EVIDENCE/native-export" > "$EVIDENCE/H1-native-cli.log" 2>&1
printf '%s\n' "$?" > "$EVIDENCE/H1-native-cli.exitcode"
"$PY" -m examples.tool_pipeline.run --readback --run-dir "$EVIDENCE/native-run" --output-dir "$EVIDENCE/native-readback" > "$EVIDENCE/H1-native-readback.log" 2>&1
printf '%s\n' "$?" > "$EVIDENCE/H1-native-readback.exitcode"
```

核对 CLI exit 0、`native-export/evidence.json` 的 transport 为 `native_owner_socket`，
完整结果为 2.000 kWh / 1.70 CNY、actual_model_call_counts 为零、恰好一条 complete terminal。
新进程 readback 的 final/checkpoint 应与首次导出一致，读取不产生新执行或额外调用。
资源 close 的断言由第二节既有 lifecycle/stop 测试承担；不能仅凭 CLI exit 0 宣称所有资源路径已验。

如果 CLI 因环境失败且没有有效 run，保留失败，不盲目执行依赖该 run 的 readback。
不删除失败 run；本地保存用于诊断，但回传与提交前剔除数据库、私有 profile、凭据和无关个人路径。

## 5. 失败与回传规范

- 分别报告 PASS、FAIL、BLOCKED_ENV、NOT_RUN；明确失败发生在 setup/collection/test/CLI 哪一步。
  若 AF_UNIX 或安全前置仍阻断，记录原异常、平台、Python、命令和退出码，不降低守卫或改 transport。
- 云端原始失败、最终通过 XML、历史 A 的 PARTIAL_ENV 都保持原结论。A 的环境问题不等于本包失败，
  本包的 native 未验也不阻止 H2a 等独立工作继续。
- 提供真实 HEAD/dirty、patch hash、七文件最终 hash、命令/退出码、原始日志、JUnit、CLI/只读输出、
  独立 review 结论，以及按 repo-relative nodeid 去重的 39 项结果。不存在的 XML 不得补造。
- 回传可以包含必要的 synthetic 导出 JSON，但不包含 `__pycache__`、`.pyc`、DB、私有 profiles、
  token、key、.env 或整个本地运行目录。原始数据库留在本地，不进入本包或版本库。
- 若为修复实际产品问题更改七文件，先记录新 diff/源码 hash 并重新审阅受影响测试；旧包 PASS
  不能自动转给新字节。不得无关扩大范围或启动业务 benchmark。

完成条件：所列 native gate 实际闭合、源码身份匹配、所有失败有真实归类、证据完整且审阅通过。
随后将代码和本次证据作为同一逻辑交付一起提交供合并；仅合入离线报告不算完成代码交付。
本包不执行或自动授权 commit/push/merge/Actions。若现有授权缺少其中某一步，停在该动作前确认。
