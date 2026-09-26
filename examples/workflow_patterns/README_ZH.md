# 工作流模式案例库

[English](README.md) | 中文

该案例库把四种常见任务结构运行成新的、可查看的 RPNH Registry。默认使用确定性
local-process 替身，不访问网络。传入 `--execution` 时只使用用户明确授权的一条精确路线；
runner 不选择或切换 provider/model。

| 场景 | 结构 | 重点观察 |
|---|---|---|
| `serial` | `intake -> work -> deliver` | 逐步登记并传递产物。 |
| `parallel` | `prepare -> (facts || risks) -> join` | 两个同时可执行的兄弟节点和 all-input join。 |
| `document` | `outline -> draft -> review -> publish` | 文档由四个版本化产物逐步形成。 |
| `long_process` | 六个串行 Agent 节点 | 更多 canonical checkpoint，便于时间轴回放。 |

## 实际 dashboard 视图

下面所有图片都截取自已完成的确定性 Registry run，不是手绘示意图。公开副本移除了精确
checkpoint 身份。

### 串行流程

![串行工作流 PetriNet](assets/serial-petrinet.png)

### 并行分叉与 join

Overview 便于直接阅读 Agent 网状关系：

![并行工作流 Agent 概览](assets/parallel-overview.png)

PetriNet 展示实现同一分叉和全输入 join 的真实数据 place、控制 place 与弧：

![并行工作流 PetriNet](assets/parallel-petrinet.png)

### 文档任务

![文档工作流 Detailed flow](assets/document-flow.png)

### 长流程

![六阶段工作流概览](assets/long-process-overview.png)

计算案例继续使用 [`hybrid_summary`](../hybrid_summary/README_ZH.md)，因为它展示了更有价值的
模型—原生插件—模型边界，不在这里重复同一 operation。

在仓库根目录执行：

```bash
python -m examples.workflow_patterns.run --list
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-patterns.XXXXXX")"
python -m examples.workflow_patterns.run \
  --scenario serial --run-dir "$DEMO_ROOT/serial"
python -m examples.workflow_patterns.run \
  --scenario parallel --run-dir "$DEMO_ROOT/parallel"
python -m examples.workflow_patterns.run \
  --scenario document --run-dir "$DEMO_ROOT/document"
python -m examples.workflow_patterns.run \
  --scenario long_process --run-dir "$DEMO_ROOT/long-process"
```

每条成功命令都会输出 `status: PASS`、终态证据引用、精确结果和 PetriNet 计数。用下面的
命令只读查看任一 run：

```bash
RUN_DIR="$DEMO_ROOT/parallel"
rpnh net --run "$RUN_DIR"
rpnh net --run "$RUN_DIR" --view --no-open
```

在看板中依次切换 Overview、Detailed flow、PetriNet 和 Executions。并行案例会在
`prepare` 后明确分叉，两个 reviewer 均结算后才启用 join。长流程最适合查看时间轴；
时间轴只观察已保存 checkpoint，不执行或恢复任务。

若要用已单独授权的真实模型运行某个场景：

```bash
: "${EXECUTION_CONFIG:?Set an authorized execution selection}"
LIVE_RUN="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-pattern-live.XXXXXX")/run"
python -m examples.workflow_patterns.run --scenario parallel \
  --execution "$EXECUTION_CONFIG" --run-dir "$LIVE_RUN"
```

该命令可能产生付费外部调用。每个 Agent 指令都要求先读取精确 Located input，再发布产物。
确定性替身也经过同一工具边界，但其固定文本只是协议测试数据，不是模型推理证据。各视图、
控件和图形符号详见[看板指南](../../docs/guides/viewer_ZH.md)。

仓库中的 `examples/workflow_patterns/validation.json` 保存四个场景的脱敏离线验收计数，
不包含 Registry、run 标识、本地路径、endpoint 或凭据。
