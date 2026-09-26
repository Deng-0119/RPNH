# 混合汇总工作流

[English](README.md) | 中文

这个三节点 DAG 会真正读取 `graph.json`，并通过 `AgentWorkflowGraph` 和
`run_agent_task` 执行：

```text
脚本化/模型整理 -> demo/summarize -> 脚本化/模型解释
```

安装 RPNH 和 `examples/native_plugin` 后运行确定性模式：

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-hybrid.XXXXXX")"
python examples/hybrid_summary/run.py --run-dir "$DEMO_ROOT/run"
rpnh net --run "$DEMO_ROOT/run" --show-resources
```

加入 `--input examples/hybrid_summary/variant-input.txt` 可运行总量 `39`、平均值 `13`
的变体。若使用已授权真实路线，则加入
`--execution /absolute/path/to/execution.json`；案例不硬编码 provider/model，也不降级。

预期证据以及脚本化模式与真实模型模式的区别见[案例指南](../../docs/guides/examples_ZH.md)。
