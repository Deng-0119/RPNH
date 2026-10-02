# 实现差异：公开源码补充

[English](IMPLEMENTATION_COMPARISON.md) | [公开结果对照](COMPARISON_ZH.md)

本页保留已完成的源码机制比较，帮助读者理解 example 的接入方式。它不是本地参考侧实验计划，也不要求新增运行。源码差异不等于已测量的性能优势。

## 2. 哪些保持不变，哪些实际被替换

| 内容 | 原始 OAI 路径 | 本 RPNH example |
|---|---|---|
| 任务与业务数据 | 原始 Office task、OfficeBank | 同一固定上游任务与 Bank；不改答案、查询键或业务 fixture |
| 业务工具 | 从同一 catalog 构造 FunctionTool，直接调用 Bank | 包装为受管理原生插件，经桥接调用相同 Bank |
| 角色来源 | 任务中的 hub 与 specialists | 相同公开角色；hub 对应规划和汇总两个执行节点 |
| agent 编排 | SDK Runner 与 `delegate_to_agent` | 原生 AgentLoop + 已声明 workflow graph |
| 执行证据 | ActionSink 内联工具和通信记录 | Registry 调用/产物/交付证据，再导出 ObservableAction |
| 评分 | 固定 SAR、AVS、TCR 函数 | 相同函数；历史 judge 使用声明的本地传输与 effort |

依据：[OAI adapter][oai_adapter]、[工具实现][oai_tools]与本例
[`workflow.py`](src/rpnh_ha/workflow.py)、[`local_driver.py`](src/rpnh_ha/local_driver.py)、
[`scoring.py`](src/rpnh_ha/scoring.py)。
任务/工具相同不意味着模型可见参数 schema、协调提示和通信结构也相同。

## 3. 实际代码层的比较

| 维度 | 上游 OAI adapter | 当前 RPNH example | 可支持的判断 |
|---|---|---|---|
| 协调 | 一个 hub 可先委派、接收报告、再作针对性委派；单回合多委派可并行 | `hub_plan → specialists → hub_finalize`；本图没有报告后的再次委派或返工弧 | 原始 OAI 有当前示例没有配置的自适应协作路径。这是应用编排差异，不是 RPNH 不能表示其它图 |
| 完成 | `Runner.run` 返回 `final_output`，adapter 再向 user 记录通信 | 下游节点等待声明产物，最终输出来自原生 terminal evidence | 记录与完成判据不同；两者的最终文本都不自动证明业务成功 |
| 业务工具可见性 | hub、spokes 均获全部 domain tools；只有 hub 有委派工具 | 每个节点均获全部 15 个 Office 工具；另有 read/write/complete 内置工具 | 不能声称 RPNH 本次已经按业务角色强制过滤了禁用工具；上游也不是用隐藏标签生成白名单 |
| 参数 schema | 所有参数都有类型；未标注类型默认 string；全部参数列入 required | 保留描述及显式类型；未标注类型不推断；没有 required 列表 | 这是当前 adapter 的真实输入契约差异，不是“同工具”即可忽略的细节 |
| 角色指令 | 公共角色文本外，hub 模板含不直接调用业务工具、不传原始 PII、报告不完整时追加委派 | 公共角色文本外，包含写注册产物和结束工具约定，以及原有预算/效率措辞 | 提示模板并不相同；移除配额不自动移除既有提示，不能称为只换内核的实验 |
| 调用前约束 | 该 adapter 通过 SDK FunctionTool 执行；不接入 RPNH 的 operation/firing/lease | managed service 校验准入 operation、allowed_tool_ids、精确登记，并先保存 started 回执 | RPNH 将调用关联到统一执行权威；这是可见机制，不是已测得的成功率优势 |
| 工具失败 | 公共 dispatch/handler 将多类异常转为错误字符串并内联记录 | 同样业务错误可能仍是字符串；受管理传输/worker 的不确定写入另有 outcome_unknown 与核对边界 | 不能把所有 Error 字符串等同于未执行，或把 RPNH 的核对边界说成自动补偿 |
| 交接 | hub→spoke 任务文本与 spoke→hub 摘要在委派时直接记录；spoke 不获 delegate tool | 注册计划/报告经资源读取进入节点上下文；实际还观察到同伴报告读取 | 图上未画某条业务弧，不等于已经实施相同通信禁令；要检查实际读取与收件范围 |
| 状态与恢复 | 本次 OAI adapter 没有配置持久 SDK Session 或 RPNH 式事务/恢复；SDK tracing 被明确关闭，使用 ActionSink | 本例保存 Registry、精确调用和交付引用；现有回传曾用旧证据重建缺漏投影 | 只限定所选 adapter。不能推广成“OpenAI SDK 没有 tracing/session/恢复能力” |
| 运行约束 | specialist 10 turns；hub 使用 ctx.max_turns；adapter 默认 300 秒整任务 wait_for | 历史 B1 有配额；补充运行和当前 example 支持无累计配额 | 不应拿上游默认限制与 RPNH 无配额结果直接宣称优劣 |

来源：[OAI adapter][oai_adapter]、[OAI prompts][oai_prompts]、[OAI tools][oai_tools]、
[通用 dispatch][dispatch]、[RPNH managed service][managed]，以及本例 [DESIGN_ZH.md](DESIGN_ZH.md)。

### 上游默认 ClawTeam + OpenClaw 路径不是“没有框架的基线”

其 adapter 创建团队及共享任务板，为各角色启动真正的 CLI agent 进程，向全部角色注册共享域 MCP，
并通过 sentinel、进程状态及会话日志组织完成检测和 ObservableAction 提取。
它与 OAI 的进程内 Runner 都不同。对照时必须同时记录 framework 和 CLI harness，
不能把 ClawTeam 单独叫做某个模型，也不能将其简化为“只有日志，没有状态”。
本页没有审完 OpenClaw/Codex/Claude CLI 的内部实现，因此不对它们各自的恢复、权限或安全能力作否定结论。
依据：[ClawTeam adapter][clawteam]。

[frameworks]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/frameworks/__init__.py
[launcher]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/run_ma.sh
[oai_adapter]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/frameworks/oai/adapter.py
[oai_tools]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/frameworks/oai/tools.py
[oai_prompts]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/frameworks/oai/prompts.py
[clawteam]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/frameworks/clawteam/adapter.py
[dispatch]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/frameworks/core/tool_dispatch.py
[completion]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/completion_judge.py
[checker]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/checker.py
[recognizer]: https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/recognizer.py
[managed]: https://github.com/Deng-0119/RPNH/blob/3492ba2fb50e40173afebae627138afea0bc2f24/cpn/plugins/managed_tools.py
[agent_task]: https://github.com/Deng-0119/RPNH/blob/3492ba2fb50e40173afebae627138afea0bc2f24/cpn/rpnh/agent_tasks.py
[graph]: https://github.com/Deng-0119/RPNH/blob/3492ba2fb50e40173afebae627138afea0bc2f24/cpn/rpnh/agent_workflows.py
