---
name: rpnh-native-net-operations
description: "在不引入 RSI 专用 runtime 的前提下提取、组合、实例化、分支和替换 PetriNet 定义。"
metadata:
  document-kind: guide
  audience: developer
  language: zh-CN
  counterpart: net-operations.md
  revision: "2026-09-26.2"
  status: implemented-basic-scope
---

[English](net-operations.md) | [中文](net-operations_ZH.md)

# 原生 PetriNet 操作

RPNH 提供可显式安装、与应用语义无关的 PetriNet 定义操作。它们不实现优化器或 RSI 策略。
普通工作流组装、人工改图，或应用自己设计的改进流程，都可以使用同一组基础能力。

## 已实现的基础范围

| 能力 | 已实现边界 |
|---|---|
| Extract | 提取完整 `ModuleDeclaration`，或提取边界落在已有组件公共端口上的完整组件集合；选区必须保留源定义的一条 terminal。 |
| Compose | 展开具名定义实例、保留登记契约，并显式连接输出与入口。只有每个相邻边界恰好有一个 exit 和一个 entry 时，`serial` 才提供简写。 |
| 并行 | 保留互相独立的入口与出口 lane。RPNH 不把融合库所解释成广播；共享输入分发和 all-settled 汇合必须是实际声明的组件。 |
| Instantiate | 生成一个带前缀、符号身份独立的定义实例。预算桶仍是原来登记的预算桶，实例化不会增加预算。 |
| Branch | 提取定义，并可选择实例化该定义；不会另建 Registry 或 owner 进程。 |
| Replace | 通过现有 owner edit 路径准备并提交完整后继网。首个支持边界是 `whole_net_quiescent`、显式状态映射和既有精确预算清单。 |

定义函数从 `cpn.rpnh` 导出：

```python
from cpn.rpnh import (
    ComposeConnection,
    ComposePlan,
    ExtractPlan,
    compose_modules,
    extract_module,
    instantiate_module,
    prepare_replacement,
    apply_replacement,
)
```

在调用 `apply_replacement` 前，这些定义操作都是纯操作。结果仍必须用提供相应 key 的同一份
可信 `Registration` 进行 lowering 或编译。
仓库中的 `examples/net_operations/compose_serial.py` 可以输出完整的两阶段定义，不写
Registry，也不调用模型。配套的 `live_agent_replacement.py` 会把 Extract、Branch、
Instantiate 和 Compose 的输出作为真实 Agent 图执行，再应用 Replace 并执行其后继网。

## 显式登记的操作

在可信 HOST 组成根调用 `register_net_components(registration)`，会安装：

- component `rpnh/net-definition-operation/v1`；
- executor `rpnh/net-definition-executor/v1`；
- config schema `rpnh/net_operation_config/v1`。

应用还必须在自己的 Module 中声明 operation、`rpnh/module_declaration/v1` 输入/输出端口、
内部 `net_operation_config` capability 输入、预算绑定和 outcome。注册不会自动把能力加入所有
Agent 的工具列表。确定性 executor 发布普通定义资源并完成一次普通 firing，不会自动采用结果网。

### Operation 配置参考

每种配置都使用精确字段集合；夹带其他 operation kind 的字段会被拒绝。`source_order` 是
Module 输入端口名称的有序列表。

| `kind` | 必填字段 | 含义 |
|---|---|---|
| `extract` | `kind`、`source_order`、`selection` | 按 `selection` 提取唯一源定义。 |
| `compose` | `kind`、`source_order`、`instances`、`compose` | 将每个源端口映射到唯一实例名，再执行组合。 |
| `instantiate` | `kind`、`source_order`、`instance`、`output_name` | 用 `instance` 为一个源添加前缀；`output_name` 可为 null。 |
| `branch` | `kind`、`source_order`、`selection`、`output_mode`、`instance` | 提取后返回定义，或返回一个新实例。 |

`selection` 精确包含：`kind`（`whole_module` 或 `components`）、`components`
（唯一组件名）、`boundary_policy`（目前仅为 `preserve_all_dependencies`）和可为 null 的
`output_name`。`compose` 精确包含：`name`、`terminal_instance`、`mode`（`explicit`、
`serial` 或 `parallel`）和 `connections`。每条连接包含 `source_instance`、
`source_exit`、`target_instance` 和 `target_entry`。Branch 使用 `definition_only` 时必须
配置 `instance: null`；使用 `new_instance` 时必须提供非空实例名。

这些选项只配置已声明 operation，不会启用全局模式、增加预算、选择模型/provider，或自动把
操作开放给 Agent。每个公共源端口和结果端口都必须是精确 `1..1` cardinality 的 data-channel
`rpnh/module_declaration/v1` 端口；不支持的数量会在 lowering 阶段、firing 准入前失败。

## 组合规则

- 一条连接指明源实例的公共 exit 与目标实例的公共 entry。
- 每个目标 entry 最多有一个生产者。
- 限定后的 component、公共 entry 和公共 exit 名必须保持唯一；含下划线的歧义名称会被拒绝，
  不会静默隐藏某条 lane。
- schema、channel、cardinality、库所容量、颜色、初始 token 和 reusable 属性继续由现有编译器复核。
- 完全相同的预算声明共享同一预算桶；bucket ID、scope 或上限冲突时拒绝，不重命名或扩充预算。
- 并行实例保留独立输入。如需复制不可变业务数据，应声明真实 distributor transition，产生精确的分支 occurrence。
- 如果并行流程要求所有分支都完成，必须提供真实的下游 join 定义。

## 替换边界

`prepare_replacement(owner, candidate, ...)` 将候选绑定到 owner 当前精确 `net_ref`，并使用同一
Registration 验证候选。它还要求候选预算桶及 operation 绑定与当前 run 的预算 manifest 完全一致。
`apply_replacement` 将不可变计划提交到现有 owner edit 队列：

1. 登记候选与 owner command；
2. 暂停新 firing 准入；
3. 旧 firing 尚未结束时返回 `DRAINING`；
4. 排空后应用“精确 token version ID → 候选库所”的显式映射或普通退休；
5. 提交后继 checkpoint 和 `net_adopted/v1` 权威记录。

存在待处理替换时，正常 owner stop 会明确拒绝。新 Agent run 会创建一条空 workspace lineage；
同一 owner 替换 Agent 图时，候选网会继承当前 checkpoint 唯一的已结算 workspace revision 及
原 lineage。如果该权威不存在或不唯一，替换会明确失败；RPNH 不会静默重置文件。
候选网还会复用本 run 唯一且精确的 execution environment 与 workspace profile；若重复发布
第二套权威，工具执行会产生歧义，因此 runtime 会拒绝。

## 可复现的真实验收任务

真实 provider 命令与验收边界见 `examples/net_operations/README_ZH.md`。仓库内的
脱敏记录表明：一次经授权的 `volcano` / `deepseek-v4-pro` 运行中，五项可执行 operation
能力全部通过，五次正式模型响应成功，没有 health probe 或路线切换；后继 Agent 同时使用了
`workspace` 与 `read_file`，Registry terminal outcome 为 `complete`。

结构操作本身仍是确定性零模型操作；这里的真实结论是，其结果网及替换确实参与了模型任务，
并不声称惰性的 Reentry 或 workspace fork/import 计划已经运行。

## 明确尚未开放

以下内容不作为可执行 v1 能力宣传：单体 Agent workflow 组件内部的 node 级切分、隐式广播、
局部在线替换、跨 Registry 迁移、历史 token 重入、workspace fork/import，以及复制在途
模型或工具 invocation。reentry 与 workspace policy 的 typed plan 目前只是惰性的准备数据，
不授予执行权；这些分支需要独立 Registry 协议后才能提供 apply 函数。

在仓库根目录运行确定性验收命令：

```bash
python -m pytest -q tests/test_native_net_operations.py
```

测试覆盖定义 round trip、串行与并行拓扑、禁止隐式广播、精确配置 ABI、真实登记的 Extract
firing、零模型调用、真实的同 owner 替换采用，以及替换 Agent 网时保留 workspace lineage。
