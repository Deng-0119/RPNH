---
name: rpnh-design
description: "Explain record authority, Petri admission, settlement and extension boundaries."
metadata:
  document-kind: explanation
  audience: operator-and-developer
  language: zh-CN
  counterpart: design.md
  revision: "2026-09-29.1"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](design.md) | [中文](design_ZH.md)

# 架构与执行原理

## 两类权威为何协作
Registry 管理记录、身份、版本与已提交证据；类型化 Petri net 表达准入和执行结构：什么可以启动、需要哪些精确输入/资源、outcome 如何改变 marking。两者属于同一闭环设计，不是可以互换的日志系统。

可把单个 run 的已提交状态理解为 `(G, M, R)`：已采用图、marking、Registry 历史。这只是解释记号，不是新增运行接口。executor 返回的只是候选材料，必须在既有权威中通过 publication/Success 建立匹配结果、资源、marking/workspace 后继和证据，下游才可使用。

## Intake 到 Finalization
用户输入进入主会话权威；直接回答路径或 Designer 决策可产生带独立声明与精确配置的 run。声明经宿主注册 lowering，编译为候选，再由 owner 发布/采用。准入绑定精确 transition、输入/资源 claim、身份及预算；start 在物理派发之前。返回产品经验证注册，Success 结算 firing，由注册终态规则建立 terminal evidence/final result。

Intake 与 Finalization 是完整生命周期，不能拆掉一端。格式化器、UI 或外部 agent 有了文本不等于有完成权威。资源等待、执行阻塞、owner stop、terminal handoff 与 Success 分开；没有可执行工作不自动证明全局活性或完成。

## 状态归属与并发
每个 run 只有一条 writer/owner 路径。`RunOwner` 提供现有权威，`OwnerEventLoop` 串行处理 owner 工作。`Harness` 可以有多个物理 operation 在途，但不能建立多个 Registry writer；完成回调应回到 owner 路径。EventStore 提交边界统一控制 optimistic head、writer epoch、幂等和发布顺序；拆模块不意味着每个 helper 自行开启事务。

尽管 `_marking` 拆分了实现，`TeamNetMarking` 仍持有 token、epoch、active claim 状态。`_ResourceServiceKernel` 和 EventStore facade 同样保留状态所有权。为了方便适配而复制可变状态，会破坏这一边界。

## 主会话、独立子任务与 delegated leaf
主 Registry 拥有对话与子任务链接。每个独立子任务有自己的 Registry/run、PN 和 owner channel；主会话只保存精确身份及相对链接，不复制子事件。UI 焦点和生命周期不决定子任务生命周期，主 rollback 因而不能删除或回退子执行。

`delegate_leaf` 不同：由父任务拥有、显式有界，并返回到精确父 action。把它当独立任务，或把所有独立任务当内部 leaf，都会丢失权威边界。

## 资源、workspace 与业务安全
资源身份/版本和已确认访问才是依据，不只是路径或无版本内容。每个 firing 获得注册版本的私有 workspace 视图；成功结算发布允许的后继，未完成写入不进入已提交共享历史。这不意味着任意外部副作用可自动补偿。

harness 负责**结构安全**：明确准入、所有权、声明效果与证据。业务提供访问与披露政策，通过 guard、Inspector place/token 或注册 operation 接入。核心不应硬编码某公司的权限政策，但业务政策也不能成为绕过结构闭环的理由。可信插件/宿主仍需审查，Registry 和 PN 都不是 OS sandbox。

## 结构校验与运行策略
Registry schema 与 commit validator 只强制历史重放时必须持续成立的事实：类型化身份、
精确引用、所有权、append-only 顺序、Petri token／基数规则、transport 里程碑及匹配的终态
证据。它们不能把一次 runtime 决策固化为历史完整性。重试资格与次数、工具失败后的处置、
业务验收和答案是否充分，属于所选 provider／operation／用户策略。因此 failure 记录保存
已发生事实和 transport disposition；当前 invocation policy 决定能否准入新的 attempt，
历史有效性不要求保存“最终不可重试”断言。未改版本号的 v1 schema 在读取旧 append-only
事件时仍接受该字段，但当前 writer 和决策均不再生成或使用它。

点查询必须使用 Registry 按 identity、type、aggregate、transaction 或 idempotency 提供的
索引读取。只有明确的全历史审计，或语义确实依赖全部后续 writer 事实的恢复规则，才应加载
完整事件历史。这样既不削弱证据边界，也避免长生命周期 Registry 仅为解析一个 authority
或 publication event 而持续变慢。

## 观察者与适配
net view 是配置结构或 Registry 当前结构的只读投影，应保留来源模式、精确 ID 与未知状态。它不能造 token、根据 UI 状态推断终态，或为了好看隐藏真实执行环节。翻译只改变解释标签，不改协议 ID、字段名、模型身份和运行数据。

Codex 复用展示层；DSH 复用上层宿主能力，但准入效果通过同一后端。真正集成不是外部 agent 独立执行、事后异步导入日志。原生插件显式绑定版本化 operation/resource，不复制核心。

## 取舍、扩展与非目标
明确准入/结算比纯 transcript loop 多一些工作，却能核查精确资源/结果谱系及恢复边界。优先结构组合，而不是隐藏特例。修改 schema、协议、dispatch 或供应商行为需针对性证据；移动代码文件不授权改语义。

框架不保证模型答案正确、全局活性、业务策略必然正确、任意传输兼容或所有未知外部效果都可恢复。相关主张需分别验证。代码：`module.py`、`compiler.py`、`run.py`、`harness.py`、`registry/_event_store/commit.py`、`workspace_settlement.py`、`inspection.py`。继续阅读[声明参考](../reference/declarations_ZH.md)、[运行与 Registry 参考](../reference/runtime-registry_ZH.md)。
