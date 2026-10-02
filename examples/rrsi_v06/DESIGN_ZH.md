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

## 方法范围

验收目标位于框架层：公开 RRSI 的角色循环、独立评估、证据流与分数/成本选择可以在当前
RPNH harness 上实现并正常使用。这证明 RPNH 能承载先进的 RSI 框架，不等于达到或复现论文
报告的 benchmark 性能。

选择实现遵循公开 RRSI Algorithm 2 的成本规则；本地 fixture 的 bootstrap 固定 seed 7、
2,000 次。本地系数、task manifest、source fixture、gate 和 smoke check 是示例配置，不是
论文官方 domain 设置。

这是 B0 application conformance，不声称 strict AgentLoop conformance、B1 突然丢失自动
对账、恶意代码 sandbox 或论文结果。源码身份使用显式 protocol/run/resource 值，不引入
内容派生的 hash、checksum 或 fingerprint。
