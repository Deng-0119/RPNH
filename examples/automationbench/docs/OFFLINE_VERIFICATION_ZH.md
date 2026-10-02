# 离线与本地集成验证

[English](OFFLINE_VERIFICATION.md) | [示例](../README_ZH.md)

最初参考包所在云环境不能使用 Unix socket；该限制不适用于本次保留的 WSL 运行，也不是本 example
的前置条件。

本地验证覆盖：

- 真实固定 AutomationBench 的任务加载器、三项 API 工具、模拟 world 和 strict rubric；
- 已安装 RPNH 的 `TaskControl`、native worker、managed plugin entry point、Unix socket broker、
  Registry 证据、输出 bundle 和终态生命周期；
- 工具响应和 final world 与直接上游调用逐项一致；
- JSON 字符串 `null` 和 Trello add-label 精确 endpoint 标量 body 的兼容规则；
- 一次真实外部模型 provider 单题，以及随后保留的 18 题 pilot。

Trello 修复后，任务内适配回归为 10 项通过。当前公开 example 又增加结果完整性、文档和隔离查看
检查。确定性测试不调用真实 provider。

组件测试通过不等于重现本次模型成绩；真实 pilot 也只证明固定条件，不能外推到所有模型、宿主、
任务或未来上游版本。
