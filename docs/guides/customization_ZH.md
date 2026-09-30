---
name: rpnh-customize
description: "Extend declarations and explicitly selected plugins without bypassing the core."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: customization.md
  revision: "2026-10-01.1"
  status: source-reviewed-v0.1.0rc1
  basis: "core; adapter differences explicitly labelled"
---

[English](customization.md) | [中文](customization_ZH.md)

# 自定义：工作流、工具、skill 与 MCP

## 选择正确边界
业务工作流声明 operation 和可见 Petri 结构，可信宿主绑定实现身份，harness 负责准入/结算，Registry 记录精确身份、资源与版本。前端只转换交互。不能把新私有 agent loop 藏进插件，执行完成后再补日志。

新类型化工作流使用 `ModuleDeclaration`、`Registration` 和已注册 lowering 组件；外部工具或指令资源使用明确安装的原生插件。当前 main 包已包含原生 SDK 与宿主桥接，但每个外部插件仍需单独安装和明确选择。普通文档不会自动扫描为可执行 skill。

具有模型调用能力的宿主集成同样是 adapter/plugin，但必须使用 harness 所有的
`registered_llm/v1` capability。adapter 把宿主消息转换为已注册请求，并把 canonical
response 映射回宿主；provider profile 解析、凭据、transport、recovery、物理 attempt
记账和 Registry 结算仍属于共享 harness。不得在 Codex、DSH 或 OpenCode 专用代码中复制
这些能力。

## 工作流与 Inspector
声明组件配置 schema、类型化端口、executor、outcome/product、link、entry/exit 和 terminal binding。由宿主注册可信 lowering、executor、tool、schema，再用 `compile_module` 编译。编译结果只是候选网，不是已注册 run；通过唯一 owner 启动，准入后才能派发。

业务判定可落在明确的 Inspector place/token、guard 或注册 operation 上。业务定义政策，核心执行结构并记录判定。声明相关资源访问和结算约束，不能只在隐藏 UI 分支判断权限。资源容量、lease、精确版本引用是执行条件，不是画图注释。

link 融合兼容 place，不制造广播副本。分支、join 和有界反馈需要对应端口、弧和预算。不得直接编辑 Registry 或 marking 使步骤“可执行”。见[声明参考](../reference/declarations_ZH.md)。

## 仓库中的真实插件示例
统一源码树包含原生插件 SDK 和一个示例插件，但该示例仍需独立安装并显式选择。先审查其
源码，再执行：

```bash
python -m pip install ./examples/native_plugin
rpnh plugins --config examples/native_plugin/plugins.json list
rpnh plugins --config examples/native_plugin/plugins.json check
rpnh plugins --config examples/native_plugin/plugins.json build demo/add
```

`--config` 在子命令之前。示例提供 `demo/add`、`demo/instruction` 和 `demo/summarize`；执行工作的 handler 会检查取消，指令结果带精确注册资源身份。check 不调用 operation，但会导入选中的可信 Python factory，factory 必须只声明。

经明确授权后，可创建含 `left`、`right` 的输入 JSON，再运行 `rpnh plugins --config CONFIG run demo/add --input INPUT --run-dir NEW_RUN_DIR`。虽然纯加法不需要模型，这会创建真实 run/Registry 并执行 handler，不能归入“完全不执行”的安装检查。

## 编写和绑定插件
通过 `rpnh.plugins` entry-point group 暴露只声明的 factory，在 `rpnh/plugins/v1` 配置中明确选择 name、entry_point、version、config 和环境变量**名称**。`RPNH_PLUGIN_CONFIG` 可替代显式 `--config`；未选择时 catalog 为空，不扫描目录。

handler 应是可导入的顶层函数，有 Draft-07 输入/输出 schema、资源声明、`pure`/`external_read`/`external_write` 效果与限额。实现身份钉住 handler 源文件字节；传递依赖仍由安装环境管理。版本声明不是 OS 安全沙箱。不得隐藏重试、接受任务 JSON 提供的动态导入地址或直接拿内部 Registry handle。

每个 worker 都会收到由 harness 所有的 `context.call_id`，以及对应的 operation、invocation
和 firing 身份。外部效果服务需要防重放时应使用该逐调用身份；不得把同一 firing 内对同一
operation 的所有调用折叠为一个请求。

## 按 Agent 节点绑定登记工具

`AgentTaskSpec` 可以在精确 stage 或 workflow node 上，把选定的原生插件 operation 暴露为
managed tool。任务仍钉住完整插件配置与 catalog digest；每个节点将模型可见名称映射到
精确 selector，并显式声明可信宿主接受的 effect：

```python
managed_bindings = {
    "planner": {
        "tools": {"lookup": {"selector": "catalog_a/lookup"}},
        "admitted_effects": ["external_read"],
    },
    "worker": {
        "tools": {"lookup": {"selector": "catalog_b/lookup"}},
        "admitted_effects": ["pure"],
    },
}
```

相同模型可见名称只在节点内有效，不会合并两个实现。声明也可收窄模型可见的
`description` 与 object `input_schema`，但执行时会同时验证这一表面 schema 和原插件
operation schema。AgentLoop 内置工具名属于保留名，不能被重新绑定；空绑定继续走旧任务
路径。

managed 调用会在派发前持久记录精确 call identity 与 started 回执，再记录 returned、failed
或 outcome-unknown 证据。已完成的同一调用读取持久结果；外部效果缺少 terminal observation
时必须由 owner 核对，不能据此重放 handler。Registry 可以保存完整结果，而后续上下文压缩
只暴露有界投影；因此仅有结果引用并不能证明某次 provider 请求实际包含了完整结果。

skill 可作为准入 operation 消费的已注册指令资源；Markdown front matter 本身不会安装 skill。MCP 工具需要通过明确安装的宿主能力绑定，把 operation、资源和效果暴露给核心；这不等于自动发现任意 MCP server，也不表示支持所有传输。应测试实际选用的宿主和协议后再声明兼容。

## 供应商与运行行为扩展
使用既有传输的新供应商通常只需 catalog 配置。新传输需要适配契约、精确身份、物理调用/未知效果记录及确定性测试，不能仅改供应商名称。密钥和业务策略不进入通用核心默认值。

验证非法输入、缺注册、schema 不符、取消、身份漂移、超时、资源访问与结算失败。schema/命令变化记录兼容或迁移说明。代码：`cpn/rpnh/module.py`、`compiler.py`、`harness.py`、`cpn/plugins/{api,catalog,cli,runtime}.py` 和 `examples/native_plugin/rpnh_demo.py`。
