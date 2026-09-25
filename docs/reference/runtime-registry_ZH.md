---
name: rpnh-runtime-registry-reference
description: "Reference the sole owner, dispatch, marking and atomic Registry boundary."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: zh-CN
  counterpart: runtime-registry.md
  revision: "2026-09-25.2"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](runtime-registry.md) | [中文](runtime-registry_ZH.md)

# Runtime、marking、RunOwner 与 Registry

## Owner 与 harness 协作
`RunOwner` 是高级可信宿主接口，不是观察者构造器。公共 `start_run(module, registration, **owner_arguments)` 转发到 `cpn.rpnh.run.start_run`，发布全新谱系，不自动执行模型/工具。宿主参数必须依据真实函数签名和配置，不虚构 `run(prompt)` SDK。

`OwnerInput(schema_id, payload: bytes, summary)` 携带原始输入。owner 方法包括 `admit(transition_id, *, logical_tau, command_id, prepare_admission=None)`、`start(admitted, *, command_id)`、`products(execution, *, outcome_id, products, command_id)`、`succeed(outputs, *, command_id)`、`terminal()`、`snapshot()`。它们交换注册权威对象，不接受从展示复制出的任意 ID。图编辑暂停准入时，admit 抛 `RuntimeError`。

Harness 构造接口：

```python
# Signature reference, not a call or an executable example:
# Harness(*, owner, event_loop, prepare_dispatcher, submit_operation,
#         select_ready=None, prepare_admission=None, max_in_flight=1)
```

必须传实际 RunOwner、属于它的 OwnerEventLoop、可调用 dispatch/submission 和正整数 max_in_flight（不接受 bool）。owner/callback 错误抛 TypeError，并发参数非法抛 ValueError。物理并发不给 worker 独立 writer 权限。

## 派发与结果契约

| 类型 | 职责 | 关键边界 |
|---|---|---|
| `OperationDispatch(execution, invoke)` | 回调绑定精确准入执行 | execution 必须为 OperationExecutionAuthority，invoke 可调用 |
| `OperationProducts(authority, timing_observation=None)` | 提供已注册输出 | executor 原始返回不等于 RegisteredOperationOutputsAuthority |
| `OperationDisposition(execution, kind, payload=None, resume=None)` | 资源等待、执行阻塞、终态交接 | kind 仅 resource_wait/execution_block/terminal_handoff，不是 Success |
| `HarnessResult` | goal/stop、marking、trace、终态证据 | 检查 terminal_evidence_ref 与 completion_error，不能只看文本 |

边界对象非法抛 `HarnessBoundaryError`。resource_wait 需要 payload 和可调用 resume，其余 disposition 不接受 resume。trace 把 execution lease、result、start event、输出资源和预算关联到精确已提交身份。

`request_owner_stop()` 请求安全 owner 工作，不宣告物理执行已停止。`RunOwner.record_owner_stop(*, idempotency_key)` 记录已授权安全边界事件，不处理信号、不自动结算、不新开 writer；停止授权仍由 launcher 负责。

## DSH 维护线的持久完成记录与有界恢复
宿主无关的 completion/recovery 行为位于共享的 `cpn/components` 与 `cpn/rpnh`
模块，不属于某个前端适配器。

registered-operation 路径在核验精确输出 bundle 后、向 dispatcher 调用方返回 products 前，记录 `registered_operation_completion_recorded/v1`。该事件绑定 run、invocation、firing、execution lease、Start、admission、marking checkpoint、operation specification/binding、所选 outcome、有序输出资源引用与 writer epoch。它是持久的 executor-return 证明，**不是 Petri 结算或终态答案**。适配器或修复脚本不能自行伪造、追加这个事件。

跨进程重开时，共享 `resume_run` 可对具有这一精确 completion 的单个 stale running firing 补结算，不再次调用 HOST/provider/tool。registration、不可变身份和预算材料在 writer 准入前检查。缺失、旧格式、串线或冲突证据不授权重放。空 writer-epoch 间隔仅在没有后续 writer 发布事实时允许；声明 HOST effects 或绑定 workspace 的 outcome 不进入自动结算恢复，需要其专属的持久 effect/revision 指令。

registered-host LLM 边界还会在同一准入 execution 重入时分类精确 v3 call 事实。已登记响应可以完成处理或直接返回，而不再物理调用；已有 submission permit 却没有响应时为 `submission_unknown`。这种同一 execution 内的处理，**不等于** operation-completion 事件之前断点的跨进程恢复。部分传输观察、响应头、本地已写出均不能证明远端完成。provider 权威状态查询／幂等仍是具体 provider 契约的可选能力，不是所有 route 已实现的通用保证。

owner stop 若与已验证且已返回 harness 的 registered products 竞态，先结算 products，再在下一安全边界停止。不能用 interrupted outcome 替换这些 products 并导致恢复重执行。这些变化不意味着支持任意 crash、通用重试或外部效果回滚。

DSH headless 的退出判定中，`terminal` 和“最新 turn 已提交”的 `idle` 为完成状态；空会话或非终态 idle 失败。`submission_unknown` 等诊断状态是失败，不是部分成功。terminal 记录也可能包含被拒绝的业务结果：命令／协议完成不证明业务动作已获批；provider 响应持久登记本身也不证明外围任务已经终态结算。

## Marking、资源与事务归属
`TeamNetMarking` 是 facade 和状态所有者；`_marking` 只是分开 selection、claims、outputs、revisions、checkpoints、delta，不新建 token/epoch/claim 状态。当前图与 marking 由 Registry hydration 提供，不能用可变展示投影替代。

`EventStore` 是内部事务协调与 writer fencing 边界；`registry/_event_store` 拆为 backend、queries、views、lineage、provenance、accounting、proposal、commit/validation。`publish_batch` 接收类型化 task/transaction、writer epoch、幂等 key、expected head、prepared object/event/relation、firing publication。batch 至少有一条事实事件，同一事务至多结算/发布一个 firing。精确引用或发布结构不一致由 `RegistryConflict` 拒绝。私有 helper 使用调用者事务，不能成为独立公共服务。

`_ResourceServiceKernel` 保留 live core、资源权威和 delivery/publication 边界。`RunOwner.access_resource(execution, resource_ref, *, access_mode, command_id)` 使用准入 execution 与精确资源版本。succeed 在已提交 firing success 之前准备 workspace 结算。文件存在或候选输出不能替代已确认访问和注册结算。

## 读取、恢复与兼容
`snapshot(owner_or_client)` 委托已有 owner/client，不新开 writer。快照包含精确 run/task/net/checkpoint、声明、marking、控制队列和 enabled transition，同时明确 `global_liveness=UNKNOWN`，不授予完成权威。

私有类、SQL helper、`_event_store`、`_operation`、`_invocation`、`_provider_calls`、`_resource_service` 均是实现细节。重组时保留 facade/事务兼容，文档不能启用历史 inert 路径。恢复和数据保留见[使用](../guides/usage_ZH.md)、[排障](../guides/troubleshooting_ZH.md)，DSH 专用命令见 [DSH 指南](../guides/dsh_ZH.md)。

代码：`cpn/rpnh/run.py:RunOwner,OwnerInput,resume_run`、`harness.py:Harness,OperationDispatch,OperationProducts,OperationDisposition,HarnessResult`、`marking.py`、`registry/event_store.py`、`registry/_event_store/commit.py`、`workspace_settlement.py`；DSH 维护线的 `registry/firing_recovery.py`、`cpn/components/registered_operation_dispatcher.py`、`registered_host_llm.py`、`tests/test_registered_operation_recovery.py`、`tests/test_dsh_backend.py`。
