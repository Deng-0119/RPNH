# 混合汇总工作流

[English](README.md) | 中文

从已安装 RPNH 获取完整的可修改依赖闭包：

```bash
rpnh examples export --example hybrid_summary --output /absolute/absent/my-hybrid
cd /absolute/absent/my-hybrid
python -m pip install -e ./examples/native_plugin
```

必须保留同级的 `examples/_support` 和 `examples/native_plugin`；只复制 hybrid_summary 不够。同样的运行命令也适用于源码根目录。可修改 `input.txt`、`graph.json` 或 editable 安装的插件 handler。脚本化 fixture 只理解随附任务协议；任意 prompt/graph 改动可能需同步修改 `examples/_support/scripted_model.py`，或明确授权真实 profile。脚本化运行仍需支持的本地进程/IPC 权限。

这个三节点 DAG 会真正读取 `graph.json`，并通过 `AgentWorkflowGraph` 和
`run_agent_task` 执行：

```text
脚本化/模型整理 -> demo/summarize -> 脚本化/模型解释
```

下图是确定性运行完成后的实际 Detailed flow，展示原生 `demo/summarize` operation 前后的
两个 Agent 节点，以及它们之间已登记的交接。

![已完成混合计算流程](assets/hybrid-summary-flow.png)

安装 RPNH 和 `examples/native_plugin` 后运行确定性模式：

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-hybrid.XXXXXX")"
python examples/hybrid_summary/run.py --run-dir "$DEMO_ROOT/run"
rpnh net --run "$DEMO_ROOT/run" --show-resources
```

加入 `--input examples/hybrid_summary/variant-input.txt` 可运行总量 `39`、平均值 `13`
的变体。若使用已授权真实路线，则加入
`--execution /absolute/path/to/execution.json`；案例不硬编码 provider/model，也不降级。

预期证据以及脚本化模式与真实模型模式的区别见[案例指南](https://github.com/Deng-0119/RPNH/blob/main/docs/guides/examples_ZH.md)。
