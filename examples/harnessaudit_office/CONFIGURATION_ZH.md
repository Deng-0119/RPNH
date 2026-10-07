# 配置契约

[English](CONFIGURATION.md) | [示例入口](README_ZH.md)

复用已有 RPNH 本地进程路由；示例不实现新的 provider 传输或密钥读取器。
执行 profile 引用 adapter；judge 直接引用 adapter。两者可使用同一模型，但输出 token 配置和职责不同。

| 字段 | 含义 |
|---|---|
| `execution.profile_path` | 用户自己的 `llm_execution_selection/v1`，`adapter_kind=local_process` |
| profile 的 `adapter_config_path` | 相对 profile 或本地绝对路径，不提交到账户配置仓库 |
| `execution.exact_model`、`reasoning_effort` | 与 profile/model 及 adapter 的 `--model`、`--reasoning-effort` 一致 |
| `scoring.adapter_path` | 用户自己的本地进程 adapter，包含 `env` 对象、`inherit_env` 数组 |
| `scoring.max_output_tokens` | 512，与固定评分器的本地传输请求一致 |
| `scoring.transport_status` | `ready` 为配置声明，不是已探测网络成功 |
| `limits_per_run` | 三个累计限制默认 null；有限值可以自行设置，但属于另一实验条件 |
| `authorized` | configure 未传 `--authorize` 则为 false；仍需显式 run／score 才会调用 |

`check-config` 分别报告 executor 与 judge。缺少 judge 文件不使执行路由无效，但配置字段仍需存在。
现有检查器依赖本页列明的 argv 参数，不能假定任意 RPNH adapter 均兼容。
不要求金额上限、价格表、账户 ID 或嵌入式密钥；历史模型标签仅保存在结果元数据。

模型、路由、effort、图、指令封装、工具面、源码或评分投影变化均需另记条件。
可复现指按明确协议运行并检查证据，不承诺随机输出或分数逐字一致。

显式添加 `--configuration-condition office-public-discovery-workflow-v1` 才选择独立的[公开检索/工作流对照条件](CONFIGURATION_COMPARISON_ZH.md)。省略即保留 baseline；新条件运行验收和模型效果仍未验证。
