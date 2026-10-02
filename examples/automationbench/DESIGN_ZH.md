# AutomationBench 适配器设计

[English](DESIGN.md) | [示例](README_ZH.md) | [历史条件](EXPERIMENT_ZH.md) | [扩展](EXTENSIONS_ZH.md)

适配器明确分开四类权威：

1. AutomationBench 提供 public task、本地业务世界、API 工具语义和严格程序化 rubric。
2. RPNH 提供 actor、execution profile、受管工具准入、生命周期、Registry 和 PetriNet 证据。
3. 串行 broker 独占每个可变 world，并在上游调用前后记录每次分派。
4. 离线评分只消费冻结 final world，不把 actor 的文字报告当作任务成功。

每题使用独立 world、worker、Registry 和首轮目录。隐藏断言与完整初始世界只留在宿主侧；actor
只看到 public prompt 和工具返回。

已保留 WSL 结果使用 native host 与 Unix socket。当前扩展代码还提供固定版本的 DSH 执行宿主；
两者使用同一冻结 task/world/scorer 权威，但各自需要绑定 condition 的 acceptance 证据。DSH 支持
不构成本次 pilot 的证据；本地执行不需要云端 socket fallback、第二套 harness 或生产 SaaS 账号。

benchmark CLI 是唯一控制权威。shell、Basic、Codex 与 OpenCode terminal 可以调用它并读取持久化
状态，但展示界面不会收到第二份 task prompt，也不拥有另一套 agent loop。由此明确区分“执行宿主”
和“控制界面”。

`rpnh_ab.upstream` 中两项兼容规则只处理固定上游中已经实测到的输入，不把 JSON 标量 body
泛化转换到其他 endpoint。原参数保留在追加式 tool event 中，独立 normalization event 记录
被转换字段与规则。

只有所选 host 与 world owner 均静止并冻结 final world 后，attempt 才可评分；attempt identity、
task-contract identity、scoring-input hash 与 final-world hash 还必须和保留 score 一致。scorer error
保持 null，不改成零分。修复复验单独保存，绝不覆盖严格首轮 cohort。
