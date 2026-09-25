---
name: rpnh-agent-model-reference
description: "Explain session control, AgentLoop ports, model configuration and physical-call accounting."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: zh-CN
  counterpart: agents.md
  revision: "2026-09-24.1"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](agents.md) | [中文](agents_ZH.md)

# 会话控制、AgentLoop 与模型服务

## 会话与任务组件
`MainSession` 协调主回合和子链接，不复制子执行状态。CLI 使用 turn、launch、reconcile_active_turn、resume_paused_turn、rollback_paused_turn。`MainSessionPaused` 是需明确继续的检查点状态；`MainSessionExecutionFailed` 不是成功答案。用户行为见[使用](../guides/usage_ZH.md)，这些宿主方法不另构成版本稳定 SDK。

任务控制按独立 task ID 暴露 list/get/status/result/message/stop/resume/net，经已有 task/owner 路径操作。读状态或排队消息不代表完成。launch/resume 可创建进程并执行模型/工具，stop 依赖检查点。工作流消息必须指定 target，生命周期/身份错误不能转成隐式新任务。

## AgentLoop 是可选执行组件
通用 harness 派发 operation，不导入某个 AgentLoop、provider 或 workflow 实现。AgentLoop 组合 turn/action、context/compaction、tool、resource wait、workspace、timing 和有界 delegation；拆分模块分开这些职责，但保留 owner-side Registry gateway。

`AgentLoopLLMPort` 声明 `request_agent_turn_v1(execution, loop, catalog, *, idempotency_key)`，返回 `CompletedAgentLLMInvocation`；`compact_agent_context_v1(..., trigger_reason, force, idempotency_key)` 返回完成的 compaction。这些边界可能调用模型，不是纯格式化。compaction 也可能消耗调用，必须保留适用的记账与证据。

`AgentLoopRegistryPort` 包含 prepare/start/record/complete/failure/wait 等操作。例如 `record_agent_llm_turn_v1(loop, attempt, response_bytes, *, idempotency_key)` 返回更新快照和正常 turn record 或 length-interruption record。截断响应不自动成为已完成回合。实现必须事务提交、校验 optimistic revision/exact head，并返回重新加载的已提交记录。

Protocol 方法故意直接声明，因为 gateway 会枚举 `vars(protocol)`。改成表面等价的继承层次可能破坏兼容。forward annotation 绑定也说明：不要仅为生成文档索引就导入整个运行系统。

## 配置与传输是不同组件

| 接口 | 用途和返回 | 限制/效果 |
|---|---|---|
| `discover_profiles(directory=None)` | 从执行文件/manifest 返回 ExecutionProfile tuple | 目录缺失可为空，不匹配抛 ValueError，不真实探测 |
| `load_profile(path)` | 显式 profile，不要求 catalog 成员关系 | 解析/验证 selection 与 adapter |
| `profile_for_path(selected, directory=None)` | 解析 catalog 或显式 profile | 不静默换模型 |
| `ExecutionProfile.as_public_dict(selected=False, environ=None)` | 输出身份、凭据变量名、缺失项、ready | ready 不是 endpoint 可用性 |
| `build_provider_catalog(...)` | 从唯一 catalog 生成配置 | 普通模式写文件，check 校验派生状态，不调用供应商 |

生成器的真实 CLI 参数见[模型配置](../guides/models_ZH.md)。timeout_seconds、max_output_tokens、max_response_bytes、可选的 context_window_tokens 与 context_compaction_retained_tokens 不等于完整费用预算；声明 context window 会启用主动 compaction，并作为非秘密 profile provenance 展示。provider adapter 执行所选传输，Registry ledger 记录物理尝试和恢复证据，不能合并成隐藏 transport retry。

## 失败、测试与稳定性
测试非法响应、length interruption、schema 不符、旧引用、取消/等待以及精确 provider/model 保持。上下文文字和工具结果出现在 prompt 中，并不会获得 Registry 权威。`OptionalAgentLoopRegistryService` 和私有 commit helper 是实现边界，不授权插件作者写 Registry 内部。

代码映射：`cpn/rpnh/main_session.py`、`task_control.py`、`agent_tasks.py`、`agent_workflows.py`；`cpn/components/agent_loop/{ports,service,turn_execution,turn_records,action_execution,action_records,context,compaction,delegation,resource_wait,workspace}.py`；`cpn/rpnh/user_config.py`、`provider_setup.py`、`cpn/llm_adapters`、`cpn/rpnh/registry/_provider_calls`。
