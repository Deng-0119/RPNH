---
name: rpnh-releases
description: "版本与发行说明"
metadata:
  document-kind: guide
  audience: user-and-developer
  language: zh-CN
  counterpart: releases.md
  revision: "2026-11-02.1"
  status: maintained
---

[English](releases.md) | [中文](releases_ZH.md)

# 版本与发行说明

RPNH 当前处于预发布阶段。Release tag 标识固定源码快照；`main` 包含最新
tag 之后的开发变更。

## 选择安装方式

| 方式 | 用途 |
|---|---|
| [已发布版本](https://github.com/Deng-0119/RPNH/releases) | 安装同一 Release 附带的文件，并使用该版本说明 |
| 指定源码版本 | 使用发布后的修复；记录所选 commit，在隔离环境构建 |
| 当前 `main` | 跟进当前开发，内容可能不同于最新已上传 wheel |

最新上传的软件预发布为
[v0.1.0rc3](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3)，包含
`rpnh_harness-0.1.0rc3-py3-none-any.whl` 和
`dsh-rpnh-operator-0.1.0-rc.3.tgz`，均属于 10 月 30 日发布。
当前源码沿用相同版本元数据，但已有后续修改，不能仅用版本字符串区分源码版本。

源码或 wheel 安装见[安装指南](installation_ZH.md)，配套插件安装见
[stock DSH operator 指南](../../integrations/dsh/operator/README_ZH.md)，客户端
版本与已知限制见[兼容性](compatibility_ZH.md)。手动安装不会迁移任务数据或重启
worker，更换环境时应保留已有任务目录。

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
