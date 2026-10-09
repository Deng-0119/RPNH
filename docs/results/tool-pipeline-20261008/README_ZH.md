---
name: rpnh-result-tool-pipeline-20261008
description: "原子工具 pipeline 原生验证"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: zh-CN
  counterpart: README.md
  revision: "2026-10-09.1"
  status: historical-curated-result
---

[English](README.md) | 中文

# 原子工具 pipeline 原生验证

确定性电费流程的实测源码为 `00f2d29c7deffed44e2ec635a24f390c6e0d9ace` 加 16 个示例文件，core 不变。精确 source-set 摘要为 `a547de500345f51c102fb197dfabedafc2aa48c251dc19d9e10733cbe2553618`。发布于 `80a17c3ce45ec3c7a1b39c170c36bbb922276de1` 保留这些字节，不是另一次 clean-checkout 重跑。[完整输入/结果/状态投影](results.json)包含 16 文件身份、四组公开合成输入、全部用例 ID 与回读计数。

| 检查 | 结果 |
|---|---|
| 默认原生套件 | 22 项通过 |
| 补充不同用例 | 10 项通过 |
| 总计 | 32 个唯一 ID / 33 次执行；一个 join 用例重复 |
| 标准 CLI | complete；**2.000 kWh / 1.70 CNY**；10 firing、12 工具产物、2 source |
| 逐时段舍入 | **0.02 CNY**；两个 5 Wh 时段、100 fen/kWh |
| 并发 | 两个 read worker 重叠；单 worker fixture 最大 active 1 |
| 新进程回读 | 稳定字段一致；ordinal/event count 1001、dispatch/execution start 10 前后不变；模型 `[0,0]` |

独立 scorer 是示例的整数 fen half-up 校验器，不是外部 benchmark grader；按原始 Wh/fen 重算每项金额，不信任 calculator 结果。此窗口使用真实 AF_UNIX owner、零模型调用；套件含单元检查，32 个测试不是 32 次 socket 集成。早期云端 native 因 AF_UNIX EPERM 阻断，pipe 语义窗口继续单列，不能认证原生 transport。

在历史源码、原 fixture 与既有依赖下，RUN/EXPORT/READBACK 使用新的较短原生目录；原 CLI 退出后才回读：

```sh
python -B -m pytest -q examples/tool_pipeline/tests
python -B -m examples.tool_pipeline.run --run-dir "$RUN" --output-dir "$EXPORT"
python -B -m examples.tool_pipeline.run --readback --run-dir "$RUN" --output-dir "$READBACK"
python -B -m examples.tool_pipeline.run   --usage examples/tool_pipeline/fixtures/usage-rounding.json   --tariff examples/tool_pipeline/fixtures/tariff-rounding.json   --run-dir "$ROUND_RUN" --output-dir "$ROUND_EXPORT"
```

这些是参数化复现说明，本次文档任务没有运行。补充历史探针保留用例 ID，不重新分发。参见[示例指南](../../../examples/tool_pipeline/README_ZH.md)。本 fixture 不产生业务模型评分、比较性能结论或 V6 组合验收。


[分发字节清单](MANIFEST.json)记录本摘要与投影的新字节身份，不能当作原始材料字节。
