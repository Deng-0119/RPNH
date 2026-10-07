---
name: rpnh-changelog
description: "记录 RPNH 发布中的用户可见变更。"
metadata:
  document-kind: project-record
  audience: user-and-developer
  language: zh-CN
  counterpart: CHANGELOG.md
  revision: "2026-10-07.1"
  status: v0.1.0rc1
---

[English](CHANGELOG.md) | [中文](CHANGELOG_ZH.md)

# 变更记录

## 未发布

- 新增显式选择的 v2 分享包环境声明、精确本机准备合同和所选解释器 HOST
  入口；既有 owner 签发的独立 Registry 固定 cut 读会话与显式正文权限；
  类型化定义交换；以及定义、配置、材料、运行态四轴分离的跨网比较。
  见[分享包环境](docs/guides/package-environments_ZH.md)和
  [独立读取](docs/guides/independent-registry-reader_ZH.md)。这些接口处于实施候选阶段；
  准备完成、单元/API 测试和渲染模拟不能证明业务 terminal 或真实浏览器生命周期
  验收通过。注明日期的证据继续保留尚未解除的本机 blocker。

- 导入修订的索引查询仅读取所请求的索引及谓词字段；显式正文读取支持严格 UTF-8，
  保留按字节计算的限额和当前权限复核。跨网比较解除已被替代的分页忙状态，
  清除失权后的选择数据，并在完整双网视图后恢复已验证的推荐显示模式。

- 打开比较或离开页面取消上一个网的读取时，同步解除导航控件的忙状态，并继续保留
  保存历史 capture 的暂停读取边界。

- 新增 opt-in 离线声明包预览与 exact 本地依赖锁（`rpnh package`）、纯数据
  HOST 声明诊断，以及同一 Registry 两个 checkpoint 的只读比较。这些准备和
  观察入口不安装代码、不授予执行权，也不实现 candidate 到运行时桥接或双 owner 执行。

- 可移植包解析现在对共享依赖的每条路径校验深度限制，并为 ZIP 文件名非法 UTF-8
  返回结构化错误；checkpoint 比较在浏览器导航后恢复所选实时刷新行为，同时清空旧比较。
  缓存页面恢复现在保留 renderer 和 resize observer，仅恢复符合条件的实时轮询，
  并取消旧在途工作；普通离开页面仍释放 renderer。完整启动的模拟生命周期测试
  覆盖此修复；真实浏览器和本地 socket 多检查点验证在本次云端检查中仍为 blocked。

- 文档构建现支持并校验链接的结果页与下载文件；可选Node测试保留HTTP覆盖，wheel审计纳入source-observation viewer模块。

- 集成显式opt-in作者/Assembly/graph、typed-v9、P4/P6、有界I02、Workset与normal-child
  root能力及HTTP/Node只读观察；保持各自有限接受边界，真实browser仍因root/sandbox BLOCKED。
- Bridge在最终渲染后检查本地声明context预算，并保留有界event/item类型诊断；不放宽工具
  allowlist、不改变unknown提交语义、不保证准确token计数或强制CLI输出cap。
- 增加2026-10-06公开AutomationBench first18与repair4精简结果及双语文档；旧分数和条件保持分离。
  详见[有限验证](docs/guides/release-validation_ZH.md)，不宣称全HOST/I00–I10/advanced25接受。

- 增加显式启用、来源权威的 Assembly v2，组合 closed 普通 v3 graph 与 plain-v1
  成员，保留 exact source／fragment 来源、显式共享预算、final-context carrier 检查、
  完整请求恢复及只读 exact-pair 验证。legacy v1 合同保持原义；open／递归组合、
  更多 graph 版本、merge 和 runtime adoption 仍属后续工作。

- Agent runtime profile 现在可将 `max_turns_per_node` 设为 `null`，从而不设置
  人工的逐 node、工具回合和全任务累计模型调用上限。Registry 会把它记录为显式的
  无计量 authority；provider 故障重试、owner-stop、workspace 资源约束和 PetriNet
  settlement 控制仍然有效。正整数配置保持原有的有界行为。
- Agent task 与 workflow 现在可以按 node 或 stage，把显式登记的原生插件 operation
  绑定到模型可见工具名。绑定会明确准入 effect、保留逐调用回执与结构化结果；未配置
  此功能时，旧任务和普通 action 记录保持原有行为。
- native 与 managed plugin worker 现在会收到由 harness 所有的逐调用
  `PluginContext.call_id`，使外部效果桥能够区分对同一 operation 的两次合法调用与重放。
- 用户自有 provider catalog 现可为精确模型声明可选 reasoning effort。Basic、DSH、
  Codex 与 OpenCode 使用同一份固定的 model/effort selection；生成的 local/external
  adapter 会把所选值传到物理请求，同时不在 RPNH 内置供应商模型对照表。
- 新增可复现的 JB 临床 packet 与 3-DOF 动力下降案例。两者都使用用户选择的精确
  profile，由主 agent 自行设计图，提供独立业务验证和真实验收 PetriNet 图，同时不发布
  原始 Registry 或私有输入。
- 新增独立 checkpoint 恢复流程，用于从最近 owner-stopped 切面或用户选择的较早
  checkpoint 继续同一个 child Registry。
- dashboard 捕获现在验证当前 authority 引用的 terminal/final-result 对，也支持含有早期
  execution generation 的 append-only Registry。

## 0.1.0rc1 — 2026-09-29

统一 RPNH 仓库的首个公开预发布版本。

### 包含内容

- 由 Registry 管理的执行机制，以及类型化 PetriNet 声明、marking、checkpoint 与终态证据。
- 对话式主会话，以及相互独立的子 task/workflow Registry。
- 用户选择 checkpoint 的 reopen、owner-stop resume，以及不改写既有 Registry 证据的对话压缩。
- 版本化 workspace 发布、逐路径变更历史和下级文件执行网。
- 用户自有 provider/model catalog、共享 external/local provider adapter，以及有界同 route
  transport recovery。
- Basic、Codex、OpenCode 展示前端和可选 DSH 宿主。
- 原生插件、串行／并行／文档／长流程案例，以及支持历史 checkpoint 导航的只读 PetriNet 看板。

### 发布边界

- 支持 Linux 与 WSL2；不支持原生 Windows 和 macOS。
- 不内置 provider 或 exact-model route；真实访问取决于用户自己的配置和授权。
- Codex、OpenCode 与 DSH 集成绑定特定版本，安装前应阅读对应指南。
- 这是预发布版。公开声明契约有文档说明，但私有实现模块不是稳定 SDK。

验证范围与已知限制见[发布验证](docs/guides/release-validation_ZH.md)和
[案例验证](docs/guides/examples-validation_ZH.md)。
