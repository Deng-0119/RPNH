---
name: rpnh-extension-observer-reference
description: "Reference native plugins, adapter lifecycle and read-only net projections."
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: zh-CN
  counterpart: extensions-observation.md
  revision: "2026-09-29.2"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](extensions-observation.md) | [中文](extensions-observation_ZH.md)

# 插件 SDK、适配与只读观察

## 原生插件契约
这些 API 随统一包提供；外部插件仍是独立安装、显式选择的可信包。`PluginDefinition` 绑定
name/version、operation tuple、config schema 和可选资源。`PluginOperation(name,
description, input_schema, output_schema, handler, resources=(), effect="pure",
timeout_seconds=60, max_result_bytes=1048576)` 钉住可导入顶层函数及源模块 hash。effect 仅
pure/external_read/external_write；时间限额 1–7200 秒，结果限额 1–16777216 字节。名称、
schema、handler、限额或非有限／非 JSON 数据错误抛 `PluginError`。

`PluginResource(name, payload, media_type="text/plain")` 要求非空不可变 bytes，描述包含 hash/size/media type。handler 只能选择声明的不同资源名。Draft-07 schema 允许本地 `#/` ref，不加载远端 schema；这防止任务数据变成任意 fetch/import 指令，但不是可信 Python 的 sandbox。

`PluginCatalog.resolve("plugin/operation")` 返回绑定插件和精确 operation；未选择则失败。`load_catalog(document=None, *, factories=None)` 默认空 catalog，factories 是可信 Python 宿主/测试注入，不是 JSON locator。导入前验证所有配置行；安装的 entry point 必须唯一且符合 name/version/API。版本和 binding digest 支持复现，传递依赖完整性仍由打包环境负责。

## CLI 生命周期
list 描述 catalog，check 编译所有 operation，build SELECTOR 返回经编译检查的声明。它们导入选中可信 factory，但不调用 operation。run SELECTOR --input FILE --run-dir DIR 经注册执行路径调用 handler 并写真实 run，退出成功依赖 terminal evidence。超时、取消或外部写不确定性不能靠隐式重试掩盖。

使用[自定义示例](../guides/customization_ZH.md)，不要直接调用私有 Registry helper。安装 Python 包不会自动让所有任务选择它；文档 metadata 不授予执行权限。

## DSH 宿主的受管工具目录
统一包包含 `cpn/plugins/managed_tools.py`，但该目录只由显式 DSH 宿主配置启用，不是 Basic、
Codex 或 OpenCode 的全局工具发现面。`ManagedToolSelector(name, selector)` 把模型可见名称映射
到精确的 `plugin/operation`。`ManagedPluginToolCatalog(plugin_catalog, allowlist)` 是显式
`PluginCatalog` 的不可变投影，不是自动发现服务。其 `provider_declarations` 属性返回
function 声明；`declaration(name)`、`binding(name)` 拒绝未选择的名称；`document()` 包含所选
目录和注册身份。

仅接受声明 `effect="pure"` 且输入 schema 为 object 的 operation。模型可见名称和插件 selector 均须唯一。DSH CLI 将 `--plugin-config ABSOLUTE_PATH` 与一个或多个 `--managed-tool NAME=PLUGIN/OPERATION` 同时使用；名称的每个组成部分均须匹配 `[a-z][a-z0-9_]{0,47}`。安装插件、选择 provider 或增加模型可见描述都不授予执行权限；这不是任意 DSH 插件或 MCP 挂载。

DSH backend 在通用 SDK 限额之外，额外要求**声明的结果上限不超过 65536 字节（64 KiB）**。SDK 默认值为 1048576 字节，因此在原生插件接口有效的插件，也可能在 DSH worker 启动前被拒绝。两者都不是推荐的业务值：`max_result_bytes` 应由 operation 有界的合法输出推导。当前 demo 约束输入域并声明 1024 字节，高于其最大序列化输出且低于 DSH ceiling。启动参数不能改写插件声明；修订插件时仍须遵守正常版本与精确身份规则。

owner-bound 受管调用服务核对调用方 execution、工具身份和参数，并记录 invocation/receipt 材料。inspector 在 worker 执行前校验参数；DSH 路径还将工具声明、最坏结果大小和下一次模型响应纳入整体 2 MiB frame 检查。已经持久记录的插件失败作为一条有精确关联的 `isError: true` 工具结果返回，不是成功值。只有 dispatch 而无持久终态观察时，需要 reconciliation，不能自动重试。每个模型响应只支持一次有关联的工具调用，再进入下一模型步骤；不隐含支持并行工具批次。

`pure` 是可信已安装代码的声明契约，不是 OS 安全边界。启动与恢复参数见
[DSH 指南](../guides/dsh_ZH.md)，持久 completion 结算见 [runtime 恢复](runtime-registry_ZH.md)。

## 适配接口
Codex 兼容层把客户端交互映射到既有 main-session/control/model-selection 边界；固定版本和不支持的 slash 命令属于实质支持范围。

DSH 中 `createApplication(config)` 创建真实上层宿主、Registry bridge、capability host、projection；`runHeadless(config, task, json)` 执行；`readHistory(config, id)` 仅用 owner client，不构造效果服务；`resumeSession(config, id)` 恢复已存活跃请求。Python `DshBackend` 持有会话 owner。离线数值模式使用精确确定性路由；配置文本和显式受管工具通过 `registered_llm/v1` 使用精确的共享 external-provider 或 local-process selection。请求／响应 frame 上限为 2 MiB；observation 在 products/Success 前仍是候选。

离线与配置 envelope 分别是 `application/rpnh_dsh_envelope/v1`、`/v2`，capability protocol 仍是 `rpnh/dsh/v1`，不代表任意上游/API 兼容。精确输出验证后登记的 completion 可支持有界的“只补结算”恢复；这不使不完整或未知的 provider 响应变得可重放。真实命令与支持边界见[适配指南](../guides/adapters_ZH.md)。

## 观察 API 与状态来源
`project_compiled_net(compiled, *, source, marking=None) -> dict` 构建临时 JSON-ready 投影。compiled 必须是 CompiledPetriNet，否则 TypeError；source.mode 必须为 initial_configured 或 registry_current，否则 ValueError。可选 marking 提供当前 epoch 未消耗 token 计数及 checkpoint；只有配置拓扑而无 marking，不是运行实况。

view schema 是 `rpnh/net_view/v1`，observation schema 是 `rpnh/net_observation/v1`。资源 place 来自真实 agent_resource/resource_lease token kind 与 lease pool。node/operation ID、executor key、schema、guard、budget binding 均保留机器身份。翻译仅改解释标签，不改身份字段和原始运行内容。

普通入口 `rpnh net` 保持 Registry 只读；--view 创建监听，--output 写投影文件，所以“Registry 只读”不等于“无进程/文件效果”。viewer 不能授予权限、推断最终结果、启动第二 writer 或为简化展示丢失核心执行语义。

代码：`cpn/plugins/{api,catalog,cli,managed_tools}.py`、`cpn/frontend/codex_app_server.py`、
`cpn/dsh/backend.py`、`integrations/dsh/src/app.ts`、
`cpn/rpnh/inspection.py:project_compiled_net`、`cpn/rpnh_cli.py:_net_command`。
这些共享契约位于当前统一代码树；离线验收不代表真实 provider 已验证。
