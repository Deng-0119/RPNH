---
name: rpnh-package-reuse-example
description: "用现成 native-add v2 包完成已有环境、新 venv 和本地操作说明三条接收方路径。"
metadata:
  document-kind: tutorial
  audience: operator-and-developer
  language: zh-CN
  counterpart: package-reuse-example.md
  revision: "2026-10-07.1"
  status: source-reviewed-pre-release
---

[English](package-reuse-example.md) | [中文](package-reuse-example_ZH.md)

# 复用现成的 native-add v2 包

从完整的 [native-add 包复用教程](../../examples/package_reuse/README_ZH.md) 开始。
在源码目录之外，使用已安装的 0.1.0rc2 或更新候选版本，运行
`rpnh examples export --example package_reuse --output DIR`。
导出包含其依赖的现有可信原生插件源码。

你会得到真实 v2 ZIP、精确包锁、完整 owner 请求、已有环境/新 venv 选择模板、
填写本地路径的工具、预期输出和结果校验命令。无需阅读测试、手工拼装 fixture，
也无需调用私有 Registry API。

教程覆盖：

1. 预览惰性包，并重算其精确包锁
2. 收集声明依赖的完整本地 wheel 闭包
3. 选择已有解释器、尚不存在的新 venv，或本地人员/agent 操作说明路径
4. 对同一个精确包目标执行检查、解析、规划，并审阅 setup 文档
5. 授权真正的安装与 HOST 装配，再检查其精确绑定
6. 单独授权业务运行，明确保存已注册的 JSON 结果，验证 `2 + 3 = 5`
7. 打开只读 PetriNet 查看器，再把同一个包用于自定义输入 `12 + 8 = 20`

三条路径只有接收环境选择不同。ZIP、manifest、包锁、入口和声明需求摘要保持一致。
只改输入需要新的 owner 请求和运行目录；修改图、插件、配置、schema 或资源字节，
则必须重新生成包材料并重新准备接收环境。

此纯原生示例不需要 provider 或数值计算包。可信 HOST 是已有 `rpnh-native/v1`，
操作是已有 `demo/add`，没有另外编造工作流或运行时。

准备完成与业务验收是两件事。如果受限主机阻止 AF_UNIX owner 控制，保留
`BUSINESS_TERMINAL_NOT_VERIFIED`，按教程到支持普通 owner socket 的本地 Linux/WSL2
继续。成功创建 venv 或编译 Module 不能冒充真正的终态结果。

相关约定：[环境准备](../environment-preparation_ZH.md)、
[v2 环境声明](package-environments_ZH.md)、[可移植包](portable-packages_ZH.md)、
[查看器指南](viewer_ZH.md)。
