# 本地运行手册

[English](RUNBOOK.md) | [示例](../README_ZH.md)

## 1. 查看已保留结果

在 RPNH 源码根目录执行：

```bash
python -I examples/automationbench/example.py results
python -I examples/automationbench/example.py results --json
```

该步骤只读，不调用模型或业务 API。

## 2. 独立安装固定上游

**先安装 Python 3.13+。** AutomationBench adapter 声明了这一最低版本，核心 RPNH 的
Python 3.11/3.12 环境不足以安装它。请在新 shell 中从 RPNH 源码根目录开始。下列步骤使用
`python3.13`，创建独立环境，并将 `AB_UPSTREAM` 初始化为仓库外目录。固定版本读取自
`examples/automationbench/config/selection.json`：
[`zapier/AutomationBench`](https://github.com/zapier/AutomationBench)，提交
`4a8e1061254004d9dac807054eed33fad7d1ff14`，包版本 `1.0.6`。不要把其题库复制进本仓库。
Git 和依赖安装需要联网；这些准备命令不调用模型：

```bash
SOURCE_ROOT="$PWD"
AB_ENV_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-ab-env.XXXXXX")"
python3.13 -m venv "$AB_ENV_ROOT/venv"
. "$AB_ENV_ROOT/venv/bin/activate"
python -m pip install -e "$SOURCE_ROOT"
python -m pip install -e "$SOURCE_ROOT/examples/automationbench[test]"
AB_UPSTREAM="$AB_ENV_ROOT/AutomationBench"
AB_PIN="$(python -c 'import json; print(json.load(open("examples/automationbench/config/selection.json"))["upstream_commit"])')"
git clone https://github.com/zapier/AutomationBench.git "$AB_UPSTREAM"
git -C "$AB_UPSTREAM" checkout --detach "$AB_PIN"
test "$(git -C "$AB_UPSTREAM" rev-parse HEAD)" = "$AB_PIN"
python -m pip install -e "$AB_UPSTREAM"
```

所有生成 work 都放在仓库外，并使用短路径满足 native Unix owner socket 限制：

```bash
AB_WORK="$(mktemp -d)/ab"
```

## 3. 运行确定性检查

```bash
python -m pytest examples/automationbench/tests -q -m 'not integration'
RPNH_AB_UPSTREAM="$AB_UPSTREAM" \
  python -m pytest examples/automationbench/tests/test_offline_runtime.py -q
```

第二条命令使用脚本化 local-process adapter、真实 native worker 和固定 AutomationBench world，
会经过 Unix socket 与 Registry，但不调用真实 provider。
如果系统临时目录对 Unix socket 来说过长，把 `RPNH_AB_TMPDIR` 设为仓库外的短可写目录。

## 4. 选择并检查新条件

`PROFILE` 必须是已经另行授权的 RPNH execution-selection JSON，不是 API key 文件，并且必须留在
仓库外。

不提供 selection 选项表示全部 600 题 public split；`--split simple` 表示独立 200 题 simple
split；`--cohort` 表示计划内精确且有序的 task ID。已保留 18 题 plan 可以作为一个*新* condition
的输入，但不会续接历史 run：

```bash
AB_COHORT="$SOURCE_ROOT/examples/automationbench/results/stratified-pilot-plan-20261002.json"

rpnh-ab doctor --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --cohort "$AB_COHORT"
rpnh-ab bridge-smoke --upstream "$AB_UPSTREAM" --work "$AB_WORK"
rpnh-ab prepare --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --cohort "$AB_COHORT" --host native
```

`doctor`、`bridge-smoke` 和 `prepare` 都不调用模型。`doctor` 会记录所选 plan digest；`run` 会拒绝
来自另一 selection 的 doctor 记录。继续前检查 `plan.json`、`conditions.json` 和 `doctor.json`。

## 5. 生成 installed-host acceptance

批次门禁要求绑定 condition 的 `rpnh-ab/acceptance-manifest/v1`。必须经已安装 host 生成，不能复制
单元测试 fixture：

```bash
rpnh-ab accept-host --upstream "$AB_UPSTREAM" --work "$AB_WORK" --host native
```

producer 只运行确定性合成 world 与 local-process adapter。七项记录覆盖 installed runtime、managed
schema/effect、真实 host 执行、结果边界、超过 48 次的无累计上限分派、静止停止和匹配的上游
world/rubric 行为；真实 provider 与生产业务 API 调用数均为零。门禁会重新解析底层 attempt、
lifecycle、tool event、score 与 host/Registry 证据；复制标签或通用 proof 文件不能通过。

用完全相同参数再次执行幂等 prepare，把该 manifest 绑定到 launch request：

```bash
rpnh-ab prepare --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --cohort "$AB_COHORT" --host native \
  --acceptance "$AB_WORK/host-acceptance/acceptance.json" \
  --launch-output "$AB_WORK/launch.json"
```

## 6. 实时执行与控制界面

以下命令需要明确授权，并可能产生付费模型调用：

```bash
rpnh-ab run --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --config "$AB_WORK/launch.json"
```

shell、Basic、Codex 与 OpenCode terminal 都使用同一 CLI，不会改变 benchmark actor。可从另一个
terminal 执行：

```bash
rpnh-ab status --work "$AB_WORK"
rpnh-ab stop --work "$AB_WORK"
```

`stop` 会阻止停止后的 world 冻结与评分，但不声称能撤销已经进入上游同步 business helper 的调用。
在包含已配置 ChatGPT helper 的完整 public600 条件中，已经开始的 helper 调用必须返回后才能完成
drain、宿主静止和 lifecycle 写入；这段等待不是重放，也不是 in-flight helper 取消。当前合成 stop
acceptance 使用延迟 local model adapter，不覆盖正在执行的业务 helper。

若使用可选 DSH 执行宿主，应准备专用固定 checkout，并在两次 `prepare` 与 `accept-host` 中加入
`--host dsh --dsh-checkout "$DSH_CHECKOUT"`。acceptance 绑定宿主，不能在 native 和 DSH condition
之间复用。checkout 可以是干净状态，也可以只包含 `integrations/dsh/prepare.sh` 应用的精确幂等
factory seam；两者规范化为同一个 post-prepare identity，其他任何 tracked change 都会被拒绝。
DSH attempt 内的 `managed-bindings.json` 保存三项工具的完整 selector、上游 description、上游
input schema 与显式 admitted effects；`api_fetch` 的 `external_write` 不依赖 DSH 的 pure 默认值。

除非修改固定上游（适配器会拒绝），live run 不会连接生产业务 SaaS 账号。

## 7. 离线证据维护

`score`、`reproject` 和 `summarize` 只能读取已保留冻结证据。lifecycle、task identity、task
contract、scoring input 或 final world 不再匹配的 score 一律不合格。`export` 包含 normalization
event 与实际导出字节清单，同时排除私有 profile、原始 Registry 数据库和 provider transcript。
不要为了让汇总完整而重跑失败任务；修复复验应使用新条件，并保留首轮。

保留 pilot 使用 [`EXPERIMENT_ZH.md`](../EXPERIMENT_ZH.md) 记录的历史 native condition；本
example 没有把它冒充成新生成的通用 batch manifest。这些命令用于准备新条件，不会自动复现或
覆盖当时的 provider 结果。
