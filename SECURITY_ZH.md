---
name: rpnh-security-policy
description: "说明受支持的预发布版本和私密漏洞报告入口。"
metadata:
  document-kind: project-policy
  audience: user-and-contributor
  language: zh-CN
  counterpart: SECURITY.md
  revision: "2026-09-29.1"
  status: v0.1.0rc1
---

[English](SECURITY.md) | [中文](SECURITY_ZH.md)

# 安全策略

## 支持版本

当前只为 `0.1.0rc1` 预发布版本准备安全修复。更早的源码快照和开发分支不是受维护的
发布线。

## 报告漏洞

使用本仓库 Security 页面中的 **Privately report a security vulnerability**。未披露漏洞
不得使用公开 issue；报告中不得包含 credential、provider token、私有 endpoint、原始
Registry database 或模型 transcript。

请提供受影响的 RPNH 版本或 commit、受支持平台、最小脱敏复现、预期与实际边界，并说明
问题属于 core runtime、provider adapter、前端／宿主集成还是只读 Viewer。维护者会通过
private advisory 完成分类处理。

不涉及保密影响的普通缺陷可以使用公开 issue tracker。
