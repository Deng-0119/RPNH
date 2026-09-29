---
name: rpnh-contributing
description: "说明如何提出并验证聚焦的 RPNH 变更。"
metadata:
  document-kind: project-policy
  audience: contributor
  language: zh-CN
  counterpart: CONTRIBUTING.md
  revision: "2026-09-29.1"
  status: v0.1.0rc1
---

[English](CONTRIBUTING.md) | [中文](CONTRIBUTING_ZH.md)

# 参与 RPNH 开发

RPNH 接受针对 `main` 的聚焦 issue 与 pull request。修改代码前，请阅读
[AGENTS.md](AGENTS.md)、[开发指南](docs/guides/development_ZH.md)和相关架构或接口页面。

## 开发环境

使用 Linux 或 WSL2，以及 Python 3.11 或更高版本：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m pip install -r docs/requirements.txt
```

凭据、provider profile、生成的 Registry、用户 workspace 和原始模型 transcript 必须保存在
仓库之外。

## 变更与测试范围

- Runtime authority 保留在 RPNH 中；前端或宿主集成不得另行实现 provider、Registry、
  workspace 或恢复机制。
- 保持 provider neutral，不内置 endpoint、credential 或偏好的 exact model。
- 英文与中文用户文档同步更新。
- 行为变化需要确定性回归测试，并运行覆盖受影响边界的最小测试集合。只有发布级候选或可能
  影响无关模块的变更才适合运行完整套件。
- 真实 provider 检查需要明确授权，不能进入自动测试套件。
- 不添加 GitHub Actions `push` trigger。

Pull request 应说明观察到的边界、修改内容、实际运行的命令和剩余限制；不能作出超出证据
范围的验收声明。

安全敏感问题按照 [SECURITY_ZH.md](SECURITY_ZH.md)报告，不使用公开 issue。
