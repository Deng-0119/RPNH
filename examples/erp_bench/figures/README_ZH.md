# ERP 原生 actor 声明

[English](README.md) | 中文

官方 Viewer 展示原生 executor 的请求、结果与中断返回结构。erp_python 和 validate_plan 是 Agent 内的 managed tools。这份初始声明不执行 ERP 事务、不启动 Odoo、不调用模型，也不是已完成任务的记录。

[![ERP 原生 actor 声明](initial-petri.svg)](initial-petri.svg)

这是自包含的[官方 Viewer SVG 导出图](viewer-export-provenance.json)；只调整最外层视口以完整保留已渲染的图。下方静态 Viewer 支持选中节点和弧查看详情。

在仓库根目录、已有 RPNH 依赖的环境中打开随附静态 frame：

```bash
python examples/view_declaration.py erp-bench --no-open
```

打开命令打印的本机回环 URL，选择 PetriNet；使用缩放、平移和节点/弧详情。此命令只读静态声明，不创建 Registry、不执行示例、不选择 execution profile。

[精确源码、图结构与范围](source-provenance.json) · [静态 frame](declaration-frame.json) · [返回示例](../README_ZH.md)
