---
name: rpnh-examples
description: "通过真实 Registry 与 PetriNet 证据运行三个递进案例。"
metadata:
  document-kind: tutorial
  audience: user-and-developer
  language: zh-CN
  counterpart: examples.md
  revision: "2026-09-26.2"
  status: deterministic-offline-validated
  basis: "current main public APIs; scripted model boundary explicitly labelled"
---

[English](examples.md) | [中文](examples_ZH.md)

# 三个实用 RPNH 案例

三个案例从原生 operation 逐步扩展到图工作流与独立任务。它们使用真实插件宿主、Registry、
任务 owner 和 PetriNet 投影。脚本化模型只是确定性协议替身，不是语言模型推理证据。

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

## 案例 3：两个独立任务

先在仓库外生成脚本化 profile，再启动一个全新的 basic 会话：

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

## 查看与修改

- 默认 net 隐藏资源节点；`--show-resources` 只显示实际 net 声明的资源。
- 可以修改原生输入中的两个数字、批次数值或任务 prompt；每次使用新的 run/session 目录。
- 缺少插件必填字段或空汇总列表会被声明的 schema 拒绝，不能表述为成功运行。
- 看板只读且默认监听 loopback。查看结束后关闭自己启动的进程。详见[看板指南](viewer_ZH.md)。

已核对的验收记录见[案例验证](examples-validation_ZH.md)。
