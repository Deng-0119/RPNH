---
name: rpnh-releases
description: "版本与发行说明"
metadata:
  document-kind: guide
  audience: user-and-developer
  language: zh-CN
  counterpart: releases.md
  revision: "2026-11-22.1"
  status: maintained
---

[English](releases.md) | [中文](releases_ZH.md)

# 版本与发行说明

RPNH 当前处于预发布阶段。Release tag 标识固定源码快照；`main` 与发行 tag 可能指向不同的源码版本。

## 选择安装方式

| 方式 | 用途 |
|---|---|
| [已发布版本](https://github.com/Deng-0119/RPNH/releases) | 安装同一 Release 附带的文件，并使用该版本说明 |
| 指定源码版本 | 使用发布后的修复；记录所选 commit，在隔离环境构建 |
| 当前 `main` | 跟进当前开发，内容可能不同于最新已上传 wheel |

[RPNH v0.1.0rc8](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc8) 是 GitHub 预发行；[`rpnh-bootstrap@0.1.0-rc.8`](https://www.npmjs.com/package/rpnh-bootstrap/v/0.1.0-rc.8) 已发布到 npm。在 WSL2 x86_64／CPython 3.13.12 的 Basic 范围内，GitHub 和 npm 两条匿名路径均通过安装、激活和版本回读。使用 Node.js 20+（含 npm）、PATH 中的 `uv` 和支持 `venv` 的持久 CPython。请固定使用 `npm install -g rpnh-bootstrap@0.1.0-rc.8`，然后运行 `rpnh-bootstrap`。npm 首发时的 `next` 与 `latest` 均指向此预发行，标签不代表稳定版。

历史 RC6 保留为 GitHub 验收记录；其 A4 回执不用于证明 RC8 或跨版本升级。

10 月 30 日发布的 [v0.1.0rc3](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3) 包含当时的 Python wheel 与 stock DSH operator 插件；该配对不属于 rc6。

源码或 wheel 安装见[安装指南](installation_ZH.md)，配套插件安装见
[stock DSH operator 指南](../../integrations/dsh/operator/README_ZH.md)，客户端
版本与已知限制见[兼容性](compatibility_ZH.md)。手动安装不会迁移任务数据或重启
worker，更换环境时应保留已有任务目录。

## RC8 预发行范围

RC8 的 Python 版本为 `0.1.0rc8`，npm 包版本为 `0.1.0-rc.8`。
新增 Codex `0.171.0` 和 DSH Web `0.2.2-rc.5` 显式 opt-in。默认版本仍为
Codex `0.163.0`、OpenCode `1.19.4` 和 DSH Web `0.2.2-alpha.2`；既有 opt-in
继续保留。RC8 不包含新的 OpenCode 候选支持。

RC8 同时包含共用 Owner reconciliation 修复，以及限定的文档导航、元数据和 Viewer
资源验证更新。此前前端任务结果在[兼容性](compatibility_ZH.md)中保留精确源码、
模型、输入与重开范围。上文的独立匿名安装不证明原地升级、回滚、其他操作系统或所有可选前端均已验收。

## 仓库内容

公开仓库包含产品源码、schema、静态资源、测试、可运行示例、用户／开发文档与
许可证。`CHANGELOG.md` 记录用户可见变更。公开基准结果各自标明输入与源码版本，
不代表当前 `main` 已通过完整认证。

开发 agent 指令、会话交接、私人环境笔记、原始运行日志、Registry 数据库及生成的
构建产物不纳入版本控制和源码发行包。开发流程见[贡献指南](../../CONTRIBUTING_ZH.md)，
包装检查见[开发指南](development_ZH.md)。

## 许可证

RPNH 使用 [MIT 许可证](../../LICENSE)。发行物需保留
[第三方声明](../../THIRD_PARTY_NOTICES.md)、DSH 上游许可证及 Viewer 依赖许可证。
