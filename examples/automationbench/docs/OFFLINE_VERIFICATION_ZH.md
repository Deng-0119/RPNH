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

当前扩展回归还覆盖冻结 18 题 cohort 解析、过期 score 拒绝、normalization-event 导出清单、
持久化 status/stop 控制、native 与 DSH host-result 投影、DSH 超过普通 48-attempt 默认值的显式
无累计上限模式，以及真实七项 installed-host producer。确定性测试和 `accept-host` 都不调用真实
provider。

installed-host acceptance 强于 startup probe：它让合成业务变更通过实际 managed-tool 边界，使用
上游 rubric 对结果 world 评分，跨过旧 DSH 64 KiB 结果边界，并验证停止状态不会产生可评分 final
world。manifest 与所选 host/code/tool condition 绑定，不能转用到另一 condition。
验证会重新解析所引用的 attempt、lifecycle、tool event、score 与 host/Registry 记录，不只信任
case 标签。

组件测试通过不等于重现本次模型成绩；真实 pilot 也只证明固定条件，不能外推到所有模型、宿主、
任务或未来上游版本。
