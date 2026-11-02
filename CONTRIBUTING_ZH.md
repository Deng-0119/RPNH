---
name: rpnh-contributing
description: "参与 RPNH 开发"
metadata:
  document-kind: project-policy
  audience: user-and-developer
  language: zh-CN
  counterpart: CONTRIBUTING.md
  revision: "2026-11-02.1"
  status: maintained
---

[English](CONTRIBUTING.md) | [中文](CONTRIBUTING_ZH.md)

# 参与 RPNH 开发

欢迎针对 `main` 提交 issue 和 pull request。修改组件前，请阅读相关
[架构](docs/architecture/design_ZH.md)、[API 参考](docs/reference/api-index_ZH.md)
和[开发指南](docs/guides/development_ZH.md)。

## 开发环境

使用 Linux 或 WSL2，Python 3.11 或更新版本：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m pip install -r docs/requirements.txt
```

## 修改与测试

- 保持修改范围清晰，并保留版本化声明的兼容性。
- Registry、workspace、权限和恢复权威归共享运行时；前端使用已有应用服务。
- 保持 provider 中立，由用户选择 provider 和精确模型。
- 同步更新中英文文档。
- 执行覆盖修改边界的确定性检查，在 PR 中说明结果；广泛运行时修改可能需要完整测试套件。
- Provider 调用属于独立可选检查，自动测试不得使用真实凭据或付费服务。
  不添加自动触发的 GitHub Actions push 工作流。

PR 应说明问题、修改后的行为、已执行检查与相关限制。缺陷报告应包含安装版本、
平台及最小脱敏复现。

## 仓库内容

提交产品源码、测试、可复用 fixture、用户文档与许可证。开发 agent 指令文件、
会话交接、内部评审报告、本机环境笔记、原始日志、provider profile、凭据、
Registry、workspace 和构建产物应保留在公开仓库之外。公开基准摘要应说明输入、
条件和失败，不公开原始对话。忽略与打包规则用于维持这一边界。

安全问题请按照 [SECURITY.md](SECURITY.md) 处理。
