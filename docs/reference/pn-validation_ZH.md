---
name: rpnh-pn-validation
description: "在明确的 owner 策略边界使用有限、输入绑定的 Petri 网分析。"
metadata:
  document-kind: reference
  audience: developer
  language: zh-CN
  counterpart: pn-validation.md
  revision: "2026-10-09.2"
  status: experimental
---

[English](pn-validation.md) | [中文](pn-validation_ZH.md)

# 有限 Petri 网验证

`cpn.rpnh.pn_validation` 分析固定 compiled declaration、精确 marking 与显式建模的
operation outcome。分析复用生产 marking 的 reservation/deposit 函数，不调用 executor、
tool、provider 或模型。报告说明输入、假设与有限范围，不授予 Registry 执行、settlement
或 terminal authority。

每个属性独立返回 `HOLDS`、`VIOLATED`、`UNKNOWN` 或 `NOT_APPLICABLE`。当前
enabledness、safety、proper completion、成功终态可达性、每个可达状态的允许完成路径、
dead transition 与无 fairness 的必然完成分别判定。允许失败终态与成功终态分开。
terminal marker 不能遮蔽未完成 work、active claim 或未释放 lease；持久数据与允许的
资源归还状态必须由 terminal contract 明确声明。

## 支持的分析范围

初始子集支持加权 consume、普通 read/return、静态 lease read/borrow/return、有限
bool/string 颜色、reader/all count guard、有限建模 outcome 与同一 transition 的并发
occurrence。bool `True`、字符串 `"true"`、字符串 `"True"` 保持区别。普通 read
claim 后归还新的 token occurrence；静态 lease read 保留精确引用。各 outcome 选择
自己的实际输出弧。

模型描述无内容 control 输出。HOST 遵守有限摘要是显式假设。
未建模 data product、变量 lease、逻辑资源更新、selected/reset effect、动态网络、
外部 reply/timeout 与 fairness 当前为 `UNKNOWN`。局部 agent/task 进展义务也未建模，
返回 `UNKNOWN`。scheduler contract 明确为 `any-exact-binding`，不认证任意调度回调
或生产默认选择器的顺序。

状态身份保留精确 token/firing ref、颜色、consumer、resource、claim、attempt 和分配
计数，不按 place 数量合并。retry 可能持续生成新身份并达到预算，结果为 `UNKNOWN`，
不能据此宣称找到环。状态、边、深度、binding 与时间上限保留未展开 frontier。
全局 `HOLDS` 需要完整且受支持的图；可重放的有限成功路径或反例可以在截断前成立其
具体存在性或否定结论。

加权 binding 按确定顺序惰性生成，不预先物化组合池。`max_bindings` 统计已检查的
START 候选，最多再取一个候选区分耗尽和截断；显式 SETTLE 选择由输入中的
occurrence/outcome 数量限定。枚举内部也检查分析 deadline。复验仅重放存储的时间
截断前缀，不重新枚举未触及的 frontier，也不启动新的语义计时。这个结构性界限
不是固定的提交墙钟上限。初态已满足 terminal-stop 时，所请求的 enabledness
仍保留受预算约束的点查询，独立于终态图闭合。

## 明确的 owner 策略

既有运行采用兼容行为：没有有限模型即未验证（`UNKNOWN`）。通过 `start_run` 的
`pn_validation` 参数显式传入 `ValidationConfiguration` 后，在真实 owner inputs 和
资源绑定确定、第一条 execution admission 之前登记不可变 operation/environment/
scheduler/terminal contract 与 policy。

advisory 记录结论并保留既有 Registry gate。strict 要求每个明确指定的必需属性都为
`HOLDS`；缺模型、截断或必需属性未建模都会阻断。已登记策略在 reopen 后保留，后续
owner adoption 不能省略它。`properties` 和 `required_properties` 共用别名规范化：
`completion` 展开为 `terminal_classification`、`proper_completion`、
`possible_successful_completion`、`allowed_completion_from_every_state`；
`cycle` 展开为 `inevitable_completion_without_fairness`。必需属性也加入实际计算集合，
报告和 strict gate 使用同一组规范 ID；别名不扩大未建模范围。

下列函数为声明了无内容 control 输出的 `step.run` 创建有限策略。向 `start_run`
提供匹配的 compiled Module 和真实 owner inputs；它不是独立 workflow，也不是已执行
的案例。

```python
from cpn.rpnh.pn_validation import (
    AnalysisPolicy, OperationCase, OperationModel, ProducedSpec, TerminalContract,
)
from cpn.rpnh.pn_validation.runtime_gate import ValidationConfiguration


def control_step_validation():
    return ValidationConfiguration(
        operation_models=(OperationModel(
            "step.run", "v1",
            (OperationCase("complete", "complete", (ProducedSpec("step.result"),)),),
        ),),
        terminal_contract=TerminalContract(
            success_places=("step.result",), unfinished_places=("step.request",),
            stop_on_terminal=True,
        ),
        policy=AnalysisPolicy(
            mode="strict", max_states=8, max_edges=8, max_depth=4,
            properties=("safety", "possible_successful_completion"),
            required_properties=("safety", "possible_successful_completion"),
        ),
    )
```

owner edit 暂停 admission，等待已有 firing 正常 settle。随后从最新 checkpoint 构造
无写入 mapping preview 并分析。proposed token ID 没有 authority；allocation 保持
精确的一一 source mapping。mapping token、lineage、报告、checkpoint 和 adoption
共同提交。commit gate 在该事务快照内复核精确输入和有界报告。验证器复核存储图前缀、精确后继、展开节点的完整闭合与逐项结论，
不重新启动 BFS。验证时钟更快不会使有效的 advisory UNKNOWN 失效。拒绝不会留下部分已登记 mapping/adoption。
中止事务后可能保留 object-store prewrite 文件，它们没有 Registry authority。

既有执行与替换合同见 [Registry/runtime 语义](runtime-registry_ZH.md)及
[原生 net operations](../guides/net-operations_ZH.md)。diagnostics 插件 ABI 仍为只读 warning。
