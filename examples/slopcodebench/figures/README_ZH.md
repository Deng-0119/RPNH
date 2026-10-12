# 单 checkpoint 求解声明

[English](README.md) | 中文

这张官方 Viewer SVG 导出图是单个 checkpoint 的原生 solve 声明。session_command 是该 Agent 内的 managed tool；shell command 不是额外的顶层 PN 阶段。声明不调用 Session、不修改代码、不调用模型，也不进行 benchmark 评测。

[![单 checkpoint 求解声明](initial-petri.svg)](initial-petri.svg)

这是自包含的[官方 Viewer SVG 导出图](viewer-export-provenance.json)；只调整最外层视口以完整保留已渲染的图。下方静态 Viewer 支持选中节点和弧查看详情。

在仓库根目录、已有 RPNH 依赖的环境中打开随附静态 frame：

```bash
python examples/view_declaration.py slopcodebench --no-open
```

打开命令打印的本机回环 URL，选择 PetriNet；使用缩放、平移和节点/弧详情。此命令只读静态声明，不创建 Registry、不执行示例、不选择 execution profile。

[精确源码、图结构与范围](source-provenance.json) · [静态 frame](declaration-frame.json) · [返回示例](../README_ZH.md)
