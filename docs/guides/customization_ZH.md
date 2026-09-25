---
name: rpnh-customize
description: "Extend declarations and explicitly selected plugins without bypassing the core."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: customization.md
  revision: "2026-09-24.1"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](customization.md) | [中文](customization_ZH.md)

# 自定义：工作流、工具、skill 与 MCP

## 选择正确边界
业务工作流声明 operation 和可见 Petri 结构，可信宿主绑定实现身份，harness 负责准入/结算，Registry 记录精确身份、资源与版本。前端只转换交互。不能把新私有 agent loop 藏进插件，执行完成后再补日志。

新类型化工作流使用 `ModuleDeclaration`、`Registration` 和已注册 lowering 组件；外部工具或指令资源使用明确安装的原生插件。当前 SDK 位于核对的插件/DSH 增量，不在 core main。普通文档不会自动扫描为可执行 skill。

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
在含原生插件增量的源码中，先审查示例，再独立安装：

```bash
python -m pip install ./examples/native_plugin
rpnh plugins --config examples/native_plugin/plugins.json list
rpnh plugins --config examples/native_plugin/plugins.json check
rpnh plugins --config examples/native_plugin/plugins.json build demo/add
```

`--config` 在子命令之前。示例提供 `demo/add`、`demo/instruction`；handler 检查取消，指令结果带精确注册资源身份。check 不调用 operation，但会导入选中的可信 Python factory，factory 必须只声明。

经明确授权后，可创建含 `left`、`right` 的输入 JSON，再运行 `rpnh plugins --config CONFIG run demo/add --input INPUT --run-dir NEW_RUN_DIR`。虽然纯加法不需要模型，这会创建真实 run/Registry 并执行 handler，不能归入“完全不执行”的安装检查。

## 编写和绑定插件
通过 `rpnh.plugins` entry-point group 暴露只声明的 factory，在 `rpnh/plugins/v1` 配置中明确选择 name、entry_point、version、config 和环境变量**名称**。`RPNH_PLUGIN_CONFIG` 可替代显式 `--config`；未选择时 catalog 为空，不扫描目录。

handler 应是可导入的顶层函数，有 Draft-07 输入/输出 schema、资源声明、`pure`/`external_read`/`external_write` 效果与限额。实现身份钉住 handler 源文件字节；传递依赖仍由安装环境管理。版本声明不是 OS 安全沙箱。不得隐藏重试、接受任务 JSON 提供的动态导入地址或直接拿内部 Registry handle。

skill 可作为准入 operation 消费的已注册指令资源；Markdown front matter 本身不会安装 skill。MCP 工具需要通过明确安装的宿主能力绑定，把 operation、资源和效果暴露给核心；这不等于自动发现任意 MCP server，也不表示支持所有传输。应测试实际选用的宿主和协议后再声明兼容。

## 供应商与运行行为扩展
使用既有传输的新供应商通常只需 catalog 配置。新传输需要适配契约、精确身份、物理调用/未知效果记录及确定性测试，不能仅改供应商名称。密钥和业务策略不进入通用核心默认值。

验证非法输入、缺注册、schema 不符、取消、身份漂移、超时、资源访问与结算失败。schema/命令变化记录兼容或迁移说明。代码：`cpn/rpnh/module.py`、`compiler.py`、`harness.py`；增量 `cpn/plugins/{api,catalog,cli,runtime}.py` 和 `examples/native_plugin/rpnh_demo.py`。
