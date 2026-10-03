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
