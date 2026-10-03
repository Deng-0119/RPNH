# 工具后端边界

[English](TOOL_BACKEND.md) | [设计](../DESIGN_ZH.md)

`api_search`、`api_fetch` 和 `base64_encode` 作为 RPNH managed plugin operation 暴露。
每题 broker 将调用串行送入一个本地 AutomationBench world。由于 endpoint 可能读取或修改 world，
`api_fetch` 保守声明为 external write。

业务服务是 AutomationBench 提供的模拟实现，不会连接生产 Gmail、Salesforce、Trello 或其他 SaaS
账号。部分上游任务可使用独立 ChatGPT 业务 helper；本次保留 pilot 排除了这些任务，避免把缺失
helper 冒充成完整后端。

对于包含已配置 helper 的完整 public600 条件，broker 对上游 dispatch 的调用是同步的。若 stop 到达时
helper 已经开始，停止流程会等待该调用返回并完成 drain；它不会取消 in-flight helper。停止的 attempt
仍不会冻结 final world 或评分。当前 installed-host stop acceptance 使用延迟 local model adapter，
不构成 helper 可取消性的证据。

本次 pilot 报告的外部网络活动是 executor 的模型 provider 路线。上游 rubric 是程序化评分，不会
调用模型。
