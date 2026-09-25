---
name: rpnh-declaration-reference
description: "Reference data-only declarations, trusted registration and compilation boundaries."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: zh-CN
  counterpart: declarations.md
  revision: "2026-09-24.1"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](declarations.md) | [中文](declarations_ZH.md)

# 声明、注册与编译

## 职责与使用时机
此层在 run 存在前描述、验证可复用工作流。`ModuleDeclaration` 是数据；`Registration` 是可信宿主把符号 key 绑定到实现/schema 声明的机制。模型写出的 JSON 不授权导入任意实现。编译不采用图，也不执行供应商。

| 接口 | 参数与返回 | 异常和效果 |
|---|---|---|
| `ModuleDeclaration.from_dict(document)` | JSON 兼容 mapping → 已验证声明 | 非法数据/schema/port 抛 `DeclarationError`，不发布 Registry |
| `ModuleDeclaration.from_json(document)` | JSON 文本 → 同一验证边界 | JSON 解析失败也转为 `DeclarationError` |
| `module.to_dict()`、`module.to_json()` | 重新验证后的数据表示 | 不生成运行身份 |
| `lower_module(module, registration)` | 公共包装 → `SymbolicNet` | 错误 module 类型抛 `TypeError`；会调用可信 lowering |
| `compile_module(module, registration)` | 真正声明对象与 Registration → `CompiledPetriNet` | 类型错误为 `TypeError`，非法 lowering 为 `DeclarationError` |
| `symbolic.validate_products(operation, outcome, products, registration)` | 限定 operation/outcome 和 output-port 列表 | 验证候选产品，不发布 |

声明主要字段是 `name`、`components`、`links`、`entry`、`exit`、`terminal`、`required_schemas`、`budgets`；可选包括 `designer_constraints`、`analyzers`、`terminal_alternatives`、`budget_buckets`。schema_version 为 `rpnh/module_declaration/v1`。组件有 name/key/config_schema/config/ports 和可选 operation。`Endpoint(component, port)` 定位端点；terminal binding 包括 key/source/operation/outcome/config。

## 验证保证什么
Python 构造和 JSON 使用同一边界，包括有限 JSON 转换及声明位置的严格整数检查。必需 schema 与已注册配置契约必须匹配。lowering 观察组件实际返回的 `PNFragment`；compiler 记录声明/fragment inventory 后构建编译文档。用户提供的 lowering 仍是可信可执行宿主代码，必须只声明；命名为 lower 不会自动令任意回调纯化。

`SymbolicNet` 是限定符号名，`CompiledPetriNet` 是发布输入，二者都不是 Registry 执行权威。link 融合端点，不为每个消费者复制 token。产品、弧、预算决定行为，UI 画的拓扑不能替代声明。

## 示例：只读取给定声明，不启动 run
输入文件应为真实完整声明，例如[自定义指南](../guides/customization_ZH.md)中原生 `plugins build` 的输出。下面仅解析：

```python
from pathlib import Path
from cpn.rpnh import DeclarationError, ModuleDeclaration

def read_declaration(path: Path) -> ModuleDeclaration:
    try:
        return ModuleDeclaration.from_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, DeclarationError) as exc:
        raise ValueError("Declaration could not be loaded; no run was started") from exc
```

编译还需匹配的**可信宿主 registration**，不能猜一个空绑定集。插件 CLI 就同时构造 `plugin_registration(catalog)`。JSON 合法但缺注册 key，仍不能当可执行工作流。

## 生命周期与稳定性
声明用于作者/验证阶段的候选，不得通过修改对象代替 Registry ref。`cpn.rpnh.__all__` 的声明导出与高级 owner 接口、下划线内部实现分开；schema 有版本不等于每个 Python helper 都是稳定 SDK。字段变化时同步 schema、compiler、示例和文档；旧验证保留原来源。

代码：`cpn/rpnh/__init__.py`、`module.py:ModuleDeclaration,SymbolicNet,validate_document`、`registration.py`、`compiler.py:compile_module`、`petri_contracts.py`，`cpn/schemas/rpnh/module_declaration.v1.schema.json`。发布边界见[运行/Registry](runtime-registry_ZH.md)。
