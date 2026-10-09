---
name: rpnh-source-provenance
description: "记录初始源码导出与当前公开分发边界。"
metadata:
  document-kind: provenance
  audience: operator-and-developer
  language: zh-CN
  counterpart: PROVENANCE.md
  revision: "2026-10-09.2"
  status: source-reviewed-v0.1.0rc1
---

# 源码来源

[English](PROVENANCE.md)

最初的公开预发布版由已审阅产品源码作 fresh-root export 建立。这是初始导出的
历史描述，不是对所有后续 Git 提交的声明：后续公开历史中曾出现内部材料。
当前公开树分发已审阅的产品材料，与私有开发和证据仓库分离。
本次文档更正不重写历史提交。

最初统一源码按以下精确前序 tip 核对；产品开发沿 canonical `main` 历史继续：

- core main：`201e2e91709e4e4cceb3930d3b73eda920f73146`；
- Codex/native plugin：`6f3558390b44d970e54539f6d5ffaa19fbb8cd3c`；
- DSH integration：`c5fc189efeecb9b6fdda5be3c598c3fd4eaef570`；
- PetriNet viewer：`16ffa8b7527c29e549db725ee357d1a564d3b367`。

包含：RPNH core 与 Registry、通用 components、provider adapters、orchestration、Codex／
OpenCode 展示兼容、原生插件、可选 DSH 宿主、PetriNet projection/viewer、可移植 profiles
和确定性测试。

当前分发边界排除内部交接和待办、私有 runtime data、凭据、cache、本地 Registry
数据库及原始真实 provider 证据。已审阅公开结果摘要和可运行示例仍属产品材料。
该边界不表示早期 Git 历史从未包含这些排除项。历史前序分支只用于来源记录，
不是安装 overlay 或活跃开发根。
