---
name: rpnh-source-provenance
description: "记录已核对的源码 tip 与 fresh-root 排除边界。"
metadata:
  document-kind: provenance
  audience: operator-and-developer
  language: zh-CN
  counterpart: PROVENANCE.md
  revision: "2026-09-25.1"
  status: source-reviewed-pre-release
---

# 源码来源

[English](PROVENANCE.md)

本公开候选采用 fresh-root 导出，不携带私有开发历史、已删除证据、本地 Registry 数据或
废弃远端分支。统一源码按以下精确前序 tip 核对：

- core main：`201e2e91709e4e4cceb3930d3b73eda920f73146`；
- Codex/native plugin：`6f3558390b44d970e54539f6d5ffaa19fbb8cd3c`；
- DSH integration：`c5fc189efeecb9b6fdda5be3c598c3fd4eaef570`；
- PetriNet viewer：`16ffa8b7527c29e549db725ee357d1a564d3b367`。

包含：RPNH core 与 Registry、通用 components、provider adapters、orchestration、Codex
compatibility、原生插件、可选 DSH 宿主、PetriNet projection/viewer、可移植 profiles 和
确定性测试。

不包含：论文／项目 workflow 与结果、历史 handoff、分支搬运工具、私有 runtime data、
凭据、cache、本地 Registry database 和真实 provider 证据。OpenCode 只有在独立实现通过
同一整合边界后才会加入。
