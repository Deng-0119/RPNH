[English](README.md) | [中文](README_ZH.md)

# Inert iteration profile 声明便利层（R1，交付修订 2）

`propose_evaluate_select.json` 只有一个**声明示例**，不是可运行的 RSI campaign，
也不实现 scorer、proposer 或 selector。可选便利 API 位于现有 Module/compiler
相邻的 `cpn.rpnh.iteration_profile`，不新增 runner、Registry 类型或调度器。
本修订替换尚未合入的第一版 R1 交付包。旧包输出空 terminal config，Module 即使编译
并 settlement 产物，也无法发布原生 run terminal。不要合入已撤回旧包。
尚未发布的 candidate schema/template ID 仍为 v1；新增必填 `terminal_outcomes`
使旧 candidate profile fail closed，不宣称 wire 向后兼容。

不修改冻结的 [RRSI v0.6 示例](https://github.com/Deng-0119/RPNH/tree/main/examples/rrsi_v06)。

## 已实现 API

```python
from cpn.rpnh.iteration_profile import (
    load_iteration_profile, compile_iteration_profile,
)

profile = load_iteration_profile("propose_evaluate_select.json")
# registration 是调用者已准备好的可信 Registration。
prepared = compile_iteration_profile(profile, registration=registration)
module = prepared.module
compiled = prepared.compiled
```

`IterationProfile.from_dict(...)` 与 `.from_json(...)` 使用同一个封闭的
`rpnh/iteration_profile/v1` schema。初版只支持可信固定模板
`propose_evaluate_select/v1`。`iteration_profile_schema_data()` 提供显式可选的
content schema inventory，使用既有 `SchemaCatalog`；不加入 mechanical Registry
schema index。不新增模板注册系统、import loader、通用模型 client、`rpnh rsi`
命令或 run 方法。

加载只读取所选 UTF-8 JSON 文件（最大 65,536 字节），拒绝重复字段、未知字段/版本/
模板、非有限值、非整数轮数以及其他预算单位。编译重新验证直接 Python 构造。
这是未来 RSI 设计的较窄实现子集：不接受 training、test dataset、fixture 读取、
child orchestration、promotion、任意图、并行候选或 retry 配置。

## 可信 HOST 要求

调用者显式准备 `Registration`：

- 使用 `cpn.components.basic` 中已有的 `register_basic_components(registration)`。
  v1 模板要求 `operation` key 对应原有 `lower_operation`；其他 lowerer 在调用前被拒绝。
- 四个明确的 application schema：state、candidate、evaluation request、evaluation
  result。它们是调用者拥有的版本化契约，不新增 mechanical 或通用评分 schema。
- 明确的 proposer/evaluator/selector executor keys 和 terminal tool key。原 compiler
  解析它们，并校验其已注册 config schema。模板提供空 executor config，以及显式 terminal-tool config
  `{"run_outcome": <所选映射>}`。tool config schema 必须接受该原生 `complete`/`failed`
  值；不兼容时编译失败。标准 binding protocol 为 `rpnh/module_terminal/v1`。

编译本身不注册这些 binding，也不调用 executor 或 terminal tool。原 `compile_module`
会调用已有的可信纯 component lowerer。所用 schema 必须内联或仅使用本地片段 `$ref`；
外部 schema 引用在原 compiler 有机会读取之前拒绝。与原 compiler 一样，调用者提供的
可信 Python Registration 是信任边界，不是针对恶意 Python 实现的沙箱。

每个 role 必須声明 `model_profile_ref`，可为 `null`。非空值只是用户已有 profile 的
非秘密、不透明引用，按展开后的 operation 原样返回到 `required_model_bindings`。
不读取 profile 文件、凭据、环境秘密或 provider；不推断或替换模型。这些是待兑现的
绑定要求，不是实际 registered-model binding，单独的 Module 不证明模型身份。
未来经授权的 HOST 接线必须沿既有模型边界解析并绑定 exact profile。不要把凭据或
私有 profile 正文放入此字段。

## 实际编译内容

对于 1–32 个固定轮次，每轮生成三个标准组件：

1. Proposer consume `state`，产生 `incumbent` 和 `candidate`。
2. Evaluator read 该 candidate，并 consume 本轮 validation `request`，产生 `evaluation`。
3. Selector consume incumbent、candidate 和 evaluation；`select`/`retain` 产生 `next`，
   `stop` 产生单独的 terminal carrier。

共享 candidate place 通过标准 Module links 融合，不由外部 dispatcher 广播。所有 place
容量均为一。后继 proposer consume 上轮 selector 的 `next`。没有 Python 运行期轮数
计数器、ready queue、retry 或 winner 状态。Python 循环只在编译期展开有限声明。
提前 `stop` 与末轮 `select`/`retain` 使用已有 terminal bindings/alternatives。
terminal binding 是声明，不是终态证据。只有原 owner 经过原生 settled-product
核验后才能发布 exact terminal。

每份 profile 必须显式提供 `terminal_outcomes` 的三项：

- `stop`：任意轮 domain `stop` 的原生结果，包括末轮 stop
- `final_select`：最后一个声明轮次 select 后的原生结果
- `final_retain`：最后一个声明轮次 retain 后的原生结果

每项只能是 `complete` 或 `failed`，没有默认值；原样写入既有
`TerminalBinding.config.run_outcome`。不新增原生 outcome、结果推断或 `run_terminal`
字段。固定轮次耗尽对应末轮 select/retain，不暗含成功。示例显式选择三项 complete，
只表示正常完成该示例的协议，不证明候选有改善，也不把未知评估变成已知。领域可以为
任一分支显式选择 failed。如果同一个 domain stop 需要多种原生含义，本模板不能表达。

领域 evaluation UNKNOWN 不同于物理执行不确定性：HOST 的领域契约可允许在未知评估后
保留有效 incumbent，并正常完成这一决定；但 provisional outputs、未 settlement/
unknown 的物理执行或 owner interruption，不会因为 stop 映射 complete 就成为终态。
原 Registry 的 settlement、active claim、exact output 和 authority 检查保持不变。
domain stop 不请求或覆盖 owner stop。

轮次和 operation 推进上界来自有限 PN 结构。便利层没有 generic operation-attempt
计数器。`native_model_call_budget` 显式使用 `registered_model_call` 单位和正整数
maximum，**与轮数无推导关系**，只映射既有 `ModuleBudgetDeclaration.ordinary_global_cap`
与 `task_total_hard_cap` 的 model-call 契约，保持其 registered returned-model-call
计账原义（`ModelCallCapProjection.actual_returned_calls`），不等于总 dispatch 或所有
physical attempt 数。示例值 1 不代表一个 operation：两轮展开
为六个 operation。既有 native 契约要求 call cap 为正数或 null，因此这里不提供零调用 cap。

每个 role 显式绑定 native scope/bucket，`max_attempts=null`，不宣称该字段可以计数
任意 operation firing。terminal/finalization allocation 为零。这些只是交给原 owner
使用的构造材料，不是新 ledger，也不授权模型调用。当前模板没有 provider request
operation 或自动 model adapter。未来 registered-model 接线必须保持原 call/attempt
计账语义并证明其实际覆盖范围。本层不实现 USD、token、训练、跨 child 或隐藏在
executor 内的工作上限，也不能把 registered model-call 计数说成完整 provider
physical-call 覆盖。

inert 返回材料包括：

- `module` 和原 `compile_module` 的产物 `compiled`
- 与 Module bucket 声明一致的 `budgets`
- `source_map`：仅 role JSON path 与编译期轮次/component 名称
- `required_entry_bindings`：initial state 和每轮一份 validation request，附明确 schema
- `required_model_bindings`：尚未解析的 model-profile 绑定要求

原 `compiled.to_dict()["registrations"]` 提供实际解析的 HOST 声明库存。source map
只是诊断信息，不是 source-qualified author revision。本层不铸造 Registry refs。仅 profile JSON 源被冻结；返回的 Module/compiled/budget
容器沿用既有浅可变契约，调用者修改后必须重新编译/校验。

## 明确限制与后续门槛

initial state 应只含已批准的 incumbent/feedback；evaluation request 意图用于 validation。
模板没有给 proposer 提供 evaluation-request 输入。**这不是运行期数据隔离或无泄漏证明。**
本层不加载或检查 request 正文。caller schema/executor 与原 Registry/HOST 资源权限边界
必须验证 exact candidate/dataset/scorer/model identity、split 权限、比较条件、missing/
unknown 结果及原 score/cost 语义。`select` 是 outcome 名称，不是比较规则实现。
本包不含 task-specific scorer，不修改 RRSI 分数或冻结协议限制。

便利 API 不提供 publication 或执行入口；应用仍沿已有 `ClosedModuleAuthor`、`start_run`、Registry
admission/budgets 与 exact terminal reader。不能忽略未满足的 binding 要求就运行 Module。
R2 需提供可信通用 domain operations、exact resource bindings 和单 Registry 证据；
R3 需验证完整受支持 native/scripted 生命周期与传输、中断和恢复行为。完整 RRSI
campaign/Digester parent-child 迁移依赖原生 child 接缝；teacher/student 训练与真实模型
需要另行实现和授权。本修订下述窄范围无 socket Registry terminal 测试不代表这些完整应用/native 门槛已通过。

## 确定性检查

在仓库根目录、已有测试环境中：

```bash
python -m pytest -q tests/test_iteration_profile.py tests/test_compiler_json_contract.py
```

inert checks 编译/回读声明及校验 schema/products。注入的 HOST 函数一旦调用就失败，
inert compile 检查还拒绝 socket、SQLite、subprocess、环境 lookup 与 registration 写入。
两个便利函数自身仍不创建 Registry 或 owner。

另有 terminal 回归显式建立临时真实 SQLite Registry，使用原 `start_run`，并通过明确的
`RunOwner.admit → start → products → succeed → terminal` 测试探针验证。三种映射分别
测试 complete/failed，再进行 exact 只读终态重建。历史空 config 控制组保持 running；
provisional output 与 owner interruption 均无 terminal evidence。这些测试不调用
业务 executor、scheduler、Orchestrator、event loop、socket、provider 或模型，只证明
特定原生 terminal publication 契约，不宣称完整 RSI campaign 或传输生命周期通过。
无需外部服务。
