---
name: rpnh-source-provenance
description: "记录已核对的源码 tip 与 fresh-root 排除边界。"
metadata:
  document-kind: provenance
  audience: operator-and-developer
  language: zh-CN
  counterpart: PROVENANCE.md
  revision: "2026-09-29.2"
  status: source-reviewed-pre-release
---

# 源码来源

[English](PROVENANCE.md)

本发布候选在当前受访问控制的 canonical 仓库中以 fresh-root export 维护，不携带私有开发
历史、已删除证据、本地 Registry 数据或废弃远端分支。最初统一源码按以下精确前序 tip
核对；当前开发只沿 canonical `main` 历史继续：

- core main：`201e2e91709e4e4cceb3930d3b73eda920f73146`；
- Codex/native plugin：`6f3558390b44d970e54539f6d5ffaa19fbb8cd3c`；
- DSH integration：`c5fc189efeecb9b6fdda5be3c598c3fd4eaef570`；
- PetriNet viewer：`16ffa8b7527c29e549db725ee357d1a564d3b367`。

包含：RPNH core 与 Registry、通用 components、provider adapters、orchestration、Codex／
OpenCode 展示兼容、原生插件、可选 DSH 宿主、PetriNet projection/viewer、可移植 profiles
和确定性测试。

不包含：论文／项目 workflow 与结果、历史 handoff、分支搬运工具、私有 runtime data、
凭据、cache、本地 Registry database 和原始真实 provider 证据。历史前序分支只用于来源
记录，不是安装 overlay 或活跃开发根。
