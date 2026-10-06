---
name: rpnh-examples
description: "运行递进的离线案例、一个安装版跨宿主任务和保留的 benchmark 示例。"
metadata:
  document-kind: tutorial
  audience: user-and-developer
  language: zh-CN
  counterpart: examples.md
  revision: "2026-10-06.1"
  status: current-instructions-with-dated-evidence
  basis: "current main commands; deterministic runs and dated authorized live evidence explicitly separated"
---

[English](examples.md) | [中文](examples_ZH.md)

# RPNH 案例目录

案例按用户任务而不是实现包组织。下面每个可运行 workflow 都会创建真实 Registry 与
PetriNet 投影。脚本化模型只是确定性协议替身，不是语言模型推理证据；provider-backed
案例与离线案例明确分开。
命令和恢复语义描述当前 `main`；截图与验收表保留生成它们的日期和精确边界，不会被静默
改标为当前执行结果。

| 想查看的内容 | 从哪里开始 | 默认模式 |
|---|---|---|
| 本地计算与登记资源 | 案例 1，原生插件 | 不使用模型 |
| 带原生 operation 的串行计算 | 案例 2，混合汇总 | 脚本替身 |
| 串行与并行拓扑对比 | 案例 3，工作流模式 | 脚本替身 |
| 文档经过起草和审阅 | 案例 3，`document` | 脚本替身 |
| checkpoint 更多的长流程 | 案例 3，`long_process` | 脚本替身 |
| 独立后台任务 | 案例 4，任务工作区 | 脚本替身 |
| 通过不同宿主执行同一真实语义任务 | 案例 5，安装版任务 | 已授权 profile |
| 真实临床文档／数据 packet | 案例 6，JB steering packet | 已授权 profile |
| 真实数值最优控制任务 | 案例 7，3-DOF 动力下降 | 已授权 profile |
| 独立 child Registry 驱动的 RRSI 两轮 Policy 演化 | 案例 8，RRSI v0.6 application | 已授权 profile |
| 带保留逐题成绩的公开业务 workflow benchmark | AutomationBench 示例 | 离线查看结果；新运行需要已授权 profile |

## 前置条件

使用 Linux 或 WSL2、Python 3.11 或更高版本，并在源码仓库根目录安装两个包：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python -m pip install ./examples/native_plugin
rpnh --help
rpnh plugins --config examples/native_plugin/plugins.json check
```

以下运行目录是私有运行数据，应保存在仓库之外。查看结束且不再需要 Registry 证据时，
可以删除最外层 `DEMO_ROOT`。

## 案例 1：原生工具与登记指令

此路径不需要模型 profile 或凭据。父目录可以存在，但每个 run 目录在 `run` 启动前必须
不存在。

![原生插件完成后的实际 PetriNet](../../examples/native_plugin/assets/native-plugin-petrinet.png)

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-native.XXXXXX")"
ADD_RUN="$DEMO_ROOT/add-run"
INSTRUCTION_RUN="$DEMO_ROOT/instruction-run"
rpnh plugins --config examples/native_plugin/plugins.json list
rpnh plugins --config examples/native_plugin/plugins.json run demo/add \
  --input examples/native_plugin/input.json --run-dir "$ADD_RUN"
rpnh plugins --config examples/native_plugin/plugins.json run demo/instruction \
  --input examples/native_plugin/instruction-input.json \
  --run-dir "$INSTRUCTION_RUN"
rpnh net --run "$ADD_RUN" --show-resources
```

第一份 JSON 结果的 `output.value` 为 `5`，`terminal_evidence_ref` 非空，且
`actual_model_call_counts[0]` 为 `0`。第二份结果包含指令正文和本次运行登记的资源身份、
资源版本身份。复制输入并修改两个数字，再使用一个全新的 run 目录即可比较结果。

三个 demo operation 都显式把规范 JSON 结果限制为 1 KiB。这不是照抄 DSH 上限：有界
整数输入使最大合法加法结果与 1000 项汇总都远小于 1 KiB，指令结果则具有固定资源形状。
其它 operation 必须根据自己的合法输出域推导上限。

## 案例 2：模型、程序、模型

启动器会实际读取 `examples/hybrid_summary/graph.json`，它不是只供展示的配置。三个节点为：

```text
整理输入 -> demo/summarize -> 解释登记后的汇总
```

![混合流程完成后的实际 Detailed flow](../../examples/hybrid_summary/assets/hybrid-summary-flow.png)

默认模式临时生成 local-process execution selection。替身先通过 AgentLoop 工具契约读取
精确 Located input，再返回确定性工具调用；插件执行、图 lowering、Registry 发布、资源
传递和终端结算均走普通 RPNH 路径。

每个模型节点最多四轮，允许 Located-input 读取以及一次发布被拒后的有界纠正；这不是
provider 重试策略。

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-hybrid.XXXXXX")"
RUN_DIR="$DEMO_ROOT/run"
python examples/hybrid_summary/run.py --run-dir "$RUN_DIR"
rpnh net --run "$RUN_DIR" --format json
rpnh net --run "$RUN_DIR" --show-resources
rpnh net --run "$RUN_DIR" --view --no-open
```

结果为 3 个批次、总量 `36`、平均值 `12`、最小值 `9`、最大值 `15`。再运行一个全新的
变体可证明插件结果不是固定回放：

```bash
VARIANT_RUN="$DEMO_ROOT/variant-run"
python examples/hybrid_summary/run.py \
  --input examples/hybrid_summary/variant-input.txt \
  --run-dir "$VARIANT_RUN"
```

变体输出总量 `39`、平均值 `13`。若已单独授权真实模型，可保持图和插件不变，传入已有
精确 execution selection：

```bash
: "${EXECUTION_CONFIG:?Set an authorized execution selection}"
REAL_RUN="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-real-parent.XXXXXX")/run"
python examples/hybrid_summary/run.py --execution "$EXECUTION_CONFIG" \
  --run-dir "$REAL_RUN"
```

这条命令可能产生付费或外部调用。案例不选择 provider/model、不切换路线、也不降级；
执行前按[模型指南](models_ZH.md)完成配置与授权。

## 案例 3：串行、并行、文档和长流程

同一个 runner 提供四种明确图结构；这些都会完成真实任务，不是只生成声明的示意图：

| 串行 PetriNet | 并行 Agent 概览 |
|---|---|
| ![串行工作流](../../examples/workflow_patterns/assets/serial-petrinet.png) | ![并行 Agent 图](../../examples/workflow_patterns/assets/parallel-overview.png) |

| 文档 Detailed flow | 长流程概览 |
|---|---|
| ![文档工作流](../../examples/workflow_patterns/assets/document-flow.png) | ![长工作流](../../examples/workflow_patterns/assets/long-process-overview.png) |

[工作流案例库](../../examples/workflow_patterns/README_ZH.md)还展示同一并行 run 的完整
PetriNet 投影。

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-patterns.XXXXXX")"
python -m examples.workflow_patterns.run --list
python -m examples.workflow_patterns.run \
  --scenario serial --run-dir "$DEMO_ROOT/serial"
python -m examples.workflow_patterns.run \
  --scenario parallel --run-dir "$DEMO_ROOT/parallel"
python -m examples.workflow_patterns.run \
  --scenario document --run-dir "$DEMO_ROOT/document"
python -m examples.workflow_patterns.run \
  --scenario long_process --run-dir "$DEMO_ROOT/long-process"
```

`parallel` 包含一次 fan-out、两个可独立启用的 reviewer 和一个显式 all-input join；
`document` 依次登记 outline、draft、review 和 publication；`long_process` 结算六个 Agent
节点，为看板时间轴提供更多 canonical checkpoint。可以直接打开任一结果：

```bash
RUN_DIR="$DEMO_ROOT/parallel"
rpnh net --run "$RUN_DIR"
rpnh net --run "$RUN_DIR" --view --no-open
```

传入 `--execution "$EXECUTION_CONFIG"` 可将脚本替身替换为一条已单独授权的精确 profile；
runner 不选择或切换路线。参阅[源码案例说明](../../examples/workflow_patterns/README_ZH.md)
和[看板教程](viewer_ZH.md)。

## 案例 4：两个独立任务

先在仓库外生成脚本化 profile，再启动一个全新的 basic 会话：

![已完成子任务的实际 PetriNet](../../examples/task_workspace/assets/independent-task-petrinet.png)

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-tasks.XXXXXX")"
python examples/task_workspace/prepare_profile.py \
  --output-dir "$DEMO_ROOT/profile"
rpnh --frontend basic --execution "$DEMO_ROOT/profile/execution.json" \
  --session-dir "$DEMO_ROOT/session"
```

以下内容输入 RPNH 提示符，不是 shell。保存每次返回的 task ID，再替换 `FIRST_ID` 和
`SECOND_ID`：

```text
/agent List exactly three data quality checks for a small batch table.
/switch main
/agent Task values: 4, 8, 12. Summarize the count, total and mean.
/tasks
/switch FIRST_ID
/task FIRST_ID status
/task FIRST_ID result
/task FIRST_ID net
/switch SECOND_ID
/task SECOND_ID status
/task SECOND_ID result
/task SECOND_ID net view --show-resources --no-open
/switch main
/quit
```

两个 ID 和 run 目录不同。切换只改变进程内焦点，不停止、复制或替换子任务。`status`
给出所选子任务的 `run_dir`；退出前端后可把该精确路径用于 `rpnh net --run RUN_DIR`。
主会话和每个子任务拥有独立 Registry，主 Registry 保存子任务链接。

要查看当前 checkpoint 控制，先让一个子任务结算，再列出它的精确切面：

```text
/task FIRST_ID checkpoints
/task FIRST_ID reopen CHECKPOINT :: Re-run from this committed cut and verify the result.
/task FIRST_ID status
/task FIRST_ID result
```

只能用该 task 返回的 ID 替换 `CHECKPOINT`。`reopen` 在同一 Registry／run 中追加新的执行
代次，不删除后续历史。选择已 terminal 的切面时，新代次无需模型调用即可闭合；选择较早
切面时可能再次执行脚本 adapter。要续接最新 owner-stopped 切面，应使用
`/task ID resume`。

## 案例 5：通过每个宿主运行同一个安装版任务

每个 wheel 都包含一个 provider-neutral 语义任务，以及 Basic、Codex 0.155.0、固定版本
DSH 和 OpenCode 1.18.32 的独立说明。列出或导出任务都不会调用 provider：

| Basic | Codex 0.155.0 |
|---|---|
| ![Basic 验收运行](../../cpn/examples/adapter_task/assets/basic-petrinet.png) | ![Codex 验收运行](../../cpn/examples/adapter_task/assets/codex-petrinet.png) |

| 固定版本 DSH | OpenCode 1.18.32 |
|---|---|
| ![DSH 验收运行](../../cpn/examples/adapter_task/assets/dsh-petrinet.png) | ![OpenCode 验收运行](../../cpn/examples/adapter_task/assets/opencode-petrinet.png) |

这些图片是验收 Registry 的只读 PetriNet，不是宿主 TUI 截图；不同执行结构仍然可见，
公开图片移除了精确 checkpoint 身份。

```bash
EXAMPLE_PARENT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-adapter-example.XXXXXX")"
EXAMPLE_ROOT="$EXAMPLE_PARENT/task"
rpnh examples list
rpnh examples export --output "$EXAMPLE_ROOT"
cd "$EXAMPLE_ROOT"
```

先阅读 `README_ZH.md`，再选择一个宿主目录。每份说明都要求已有的用户自有
`EXECUTION_CONFIG`、新的运行根目录和一次明确任务提交。捆绑文件不包含 provider、
endpoint、凭据或模型选择。公共任务对同一组三个批次数值计算数量、总量、平均值、最小值
和最大值，是语义任务而不是 READY 健康标记。

只把 assistant 的 JSON 对象复制到 `answer.json`，再验证任务结果：

```bash
rpnh examples verify --result answer.json
```

该检查本身不能证明执行成功；还须核对宿主的 Registry 终态证据、已登记 final result、
所选 profile provenance 与物理调用记录。Basic、Codex 和 OpenCode 共用 MainSession 权威；
DSH 通过同一 provider adapter 使用自己的登记宿主 turn。案例不会假装 DSH 提供 Basic 专属的
任务／workflow 控制。

任务包内各宿主的 `evidence.json` 分别记录了 2026-09-26 聚焦验收：

| 宿主 | 逻辑 turn | 成功物理响应 | Registry 权威 | 语义结果 |
|---|---:|---:|---|---|
| Basic | 1 | 2 | PASS | PASS |
| Codex 0.155.0 | 1 | 2 | PASS | PASS |
| 固定版本 DSH | 1 | 1 | PASS | PASS |
| OpenCode 1.18.32 | 1 | 2 | PASS | PASS |

七次成功物理响应始终使用明确选择的同一 `local_process` 路线与精确
`gpt-5.6-terra` 模型；health probe 为 0，路线／模型切换为 0。MainSession 的一个逻辑
turn 可以包含多次物理 generation；DSH 的登记宿主 turn 使用了一次。OpenCode 展示层在
turn 尚未结算时显示了保守的 reconciliation 提示，随后显示已提交答案及正常的“无子任务
decision”注释；Registry terminal/final-result 证据和语义验证均通过。原始 Registry、标识、
路径与 transcript 保持私有，脱敏摘要不代表这些原始资料。

## 案例 6：JB 临床 steering packet

![实际验收通过的 JB 图](../../examples/jb_steering_packet/assets/jb-steering-petrinet.png)

这个真实任务把论文、统计分析计划和数据工作簿从 PLOS 官方来源下载到仓库外目录，再由
准备脚本转换为一个精确注册的任务 packet。主 agent 必须自行设计图；案例不包含固定
workflow，也不选择 provider/model。离线聚合分析器在不发布参与者行的前提下，提供可复算
的基线、Kaplan-Meier 式和样本量验收值。

完整步骤见 [JB 复现指南](../../examples/jb_steering_packet/README_ZH.md)。

## 案例 7：3-DOF 动力下降

![实际验收通过的 3-DOF 图](../../examples/three_dof_powered_descent/assets/three-dof-petrinet.png)

这个真实数值任务注册精炼的公开问题定义和与 solver 无关的轨迹 verifier。主 agent 自行
选择图和数值方法。验收要求生成的实现实际运行，并且轨迹满足终端、动力学和路径检查；
solver 退出状态本身不够。

完整步骤见 [3-DOF 复现指南](../../examples/three_dof_powered_descent/README_ZH.md)。

## 案例 8：RRSI v0.6 application

这个 provider-neutral application 将 Analyst、Digester、Proposer、Critic 与 Policy
occurrence 作为独立 RPNH child Registry 执行。示例自带的 timeout fixture 展示当前 RPNH
harness 上的两轮 Policy 演化与公开 RRSI 选择规则。

离线测试使用脚本化 input port。执行真实 campaign 时，先授权本次实验并提供自己的
execution profile。完整步骤见 [RRSI application 指南](../../examples/rrsi_v06/README_ZH.md)。

## Benchmark 示例：AutomationBench 公开业务任务

已保留 AutomationBench 实验将一个 RPNH native actor 接入固定上游的三项 API 工具和模拟业务 world。
仓库内保留的 score-blind 18 题 pilot 覆盖六个业务域与三种集成宽度。严格首轮为 8/18 满分、
17/18 基础设施闭环并评分，共 437 次模型调用和 1,081 次成功工具分派。

以下命令不调用 provider，可直接查看保留结果：

```bash
python -I examples/automationbench/example.py results
```

example 包含逐题成绩、独立修复复验、与原始 public-600 汇总的描述性比较及明确历史条件。当前
扩展增加 native/DSH installed-host acceptance、精确冻结 cohort、严格 score 资格，以及可从 shell、
Basic、Codex 或 OpenCode 操作的同一权威 CLI；这些扩展不是历史成绩的证据。它不声称完成 600 题
或形成同条件榜单对照。完整说明见
[AutomationBench 指南](../../examples/automationbench/README_ZH.md)。

## 查看与修改

- 默认 net 隐藏资源节点；`--show-resources` 只显示实际 net 声明的资源。
- 可以修改原生输入中的两个数字、批次数值或任务 prompt；每次使用新的 run/session 目录。
- 缺少插件必填字段或空汇总列表会被声明的 schema 拒绝，不能表述为成功运行。
- 看板只读且默认监听 loopback。查看结束后关闭自己启动的进程。详见[看板指南](viewer_ZH.md)。

验收参考边界见[案例验证](examples-validation_ZH.md)。其中区分确定性 fixture、此前授权
的 provider 调用和当前定向兼容检查，均不能替代用户对自有 route 的验证。

2026-10-06公开记录：freeze04首轮18题为5 PASS / 9 FAIL / 4 BLOCKED；独立repair四题为1 PASS / 3 FAIL。旧14道已评分题未重跑。 [2026-10-06结果](../../examples/automationbench/PUBLIC_RESULTS_20261006_ZH.md).
