# 设计与边界

[English](DESIGN.md) | 中文

## 对 RPNH 的复用

`formal_role.py` 与 `formal_policy.py` 声明 application 专用的 module、schema、tool 和
transition handler。注册、registered-host 执行、Registry resource、operation settlement、
owner event loop 与精确 PetriNet 执行均复用现有 RPNH primitive；application 不维护第二套
lifecycle 数据库或替代 scheduler。

`formal_campaign.py` 组装业务顺序并启动彼此独立的 role 与 Policy run。它只记录
application 层 manifest、score row、selection decision 和不透明 RPNH 引用。只有 production
runner、唯一 child-run/provider-attempt 证据及预期 transition trace 同时满足时，才会产生完成
声明。测试替身会明确标记为 `injected_test_runners`，不能形成完成声明。

## 冻结内容与用户自有内容

`protocol.example.json` 冻结 timeout fixture、任务切分、两轮方法、role 限额、精确 scorer、
公开的固定种子 bootstrap 和本地选择参数。execution profile 由用户所有，决定 provider、
模型、adapter、请求上限与 runtime policy。application 只校验收到完整的 RPNH selection，
不读取私有账号配置，也不要求某一种 route 实现。

heldout 与 export 的任务正文不会投影给演化 role。候选源码来自 Proposer 结果中的字面文本，
只允许修改 `policy.py`，在 Critic 与 smoke evaluation 前先经过固定检查，随后在独立 Policy
child run 中评估。

## 方法与执行约定

本示例展示公开 RRSI 角色循环、独立评估、证据流与分数/成本选择在当前 RPNH harness 上的
实现。选择过程遵循公开 RRSI Algorithm 2 成本规则；timeout fixture 的 bootstrap 使用
seed 7、2,000 次，其系数、task manifest、source fixture、gate 和 smoke check 均冻结在
`protocol.example.json` 中。

运行采用 `application_petri_conformance/v1` 与 B0 recovery profile。源码身份通过显式
protocol、run 和 resource 引用记录。

## 执行控制与报告修订

`formal_reporting.py` 以原子替换方式写入应用快照，不构成新的执行账本权威。
started/returned/failed/interrupted child 条目仅描述协调器观察到的状态，并在可用时保留
Registry 引用。`completed_evaluations` 仅包含完整评估；未完成评估中的 trial 结果保留在
`child_runs`，不补造聚合成绩。继续使用 v1 报告外壳，通过 `execution_control_version`
标识新的观察与控制行为。只导出显式允许列表中的传输错误码；任意错误码与异常正文不导出。

`formal_execution.py` 组合既有 `ExecutionServices` 回调与 `Harness.request_owner_stop`。
停止请求只锁存一次，在执行开始前、dispatcher 准备阶段及支持中断的传输回调中检查。
已获准执行的操作沿用核心结算规则；与停止竞争时，已持久化的完成结果优先。线程池退出前
会等待已启动工作结束。示例不新增中断 route，也不承诺强行终止不支持中断的旧端口；
不新增操作系统信号处理或 CLI 取消界面。

这些修改不能确定历史实验的失败原因。实际 profile 与源码来源证据仍更多保留在 child
Registry 中，顶层报告尚未完整导出。冻结的 `safe_missing_retry_max=1` 仍是未启用的
应用层额度，实际应用层重试次数为零。Digester 既有约束为 6000 个摘要字符，模型 token
上限来自共享 execution target；本修订不重新解释或修改冻结的 `max_output_tokens` 字段。
这些来源证据与单位契约改进需要另行处理。
