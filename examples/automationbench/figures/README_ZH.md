# 原生 actor 声明

[English](README.md) | 中文

这是官方 Viewer 呈现的原生 actor 声明。单个 Agent transition 承载 managed api_search、api_fetch 与 base64_encode 调用，这些工具不是各自独立的 PN 流程阶段。图使用随附的合成验收 prompt 与插件工厂契约，HOST 绑定不连接服务；不含历史 pilot 运行或评分。

[![原生 actor 声明](initial-petri.svg)](initial-petri.svg)

这是自包含的[官方 Viewer SVG 导出图](viewer-export-provenance.json)；只调整最外层视口以完整保留已渲染的图。下方静态 Viewer 支持选中节点和弧查看详情。

在仓库根目录、已有 RPNH 依赖的环境中打开随附静态 frame：

```bash
python examples/view_declaration.py automationbench --no-open
```

打开命令打印的本机回环 URL，选择 PetriNet；使用缩放、平移和节点/弧详情。此命令只读静态声明，不创建 Registry、不执行示例、不选择 execution profile。

[精确源码、图结构与范围](source-provenance.json) · [静态 frame](declaration-frame.json) · [返回示例](../README_ZH.md)
