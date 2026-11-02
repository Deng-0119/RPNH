# RPNH Harness

[English](README.md) | 中文

RPNH 是不绑定特定模型服务商的 Agent 执行框架，用于组合语言模型、原生工具与可复用工作流。

持久化的 **Registry** 记录执行与资源历史；类型化的 **PetriNet** 决定工作何时可执行：
已声明的输入与完成结算的输出使后续步骤具备执行条件，也支持并行分支与汇合。
前端和只读 Viewer 查看同一份运行状态，不另建执行或恢复逻辑。

## 可以做什么

- 将 Agent 与已有代码组合成显式工作流。
- 创建独立子任务，各自保留 Registry 和结果。
- 跟踪工作区输入、输出的版本，通过 PetriNet Viewer 查看运行。
- 续接 owner 主动停止的工作，或从指定的已提交 checkpoint 重开，并保留先前历史；
  这不会撤销外部效果。

## 快速开始

需要 **Linux 或 WSL2、Python 3.11+**，以及允许本地进程和 Unix socket 的 shell 环境。
以下从源码安装并运行原生加法工具，不需要模型或 API 凭据；安装依赖时可能访问包索引。

```bash
git clone https://github.com/Deng-0119/RPNH.git
cd RPNH
git rev-parse HEAD
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python -m pip install ./examples/native_plugin
rpnh --help
RUN_DIR="$(mktemp -d)/add-run"
rpnh plugins --config examples/native_plugin/plugins.json run demo/add \
  --input examples/native_plugin/input.json --run-dir "$RUN_DIR"
rpnh net --run "$RUN_DIR" --view --no-open
```

工具返回 `output.value: 5` 和终态证据引用；最后一条命令输出本地只读看板地址。
保留生成的运行目录，即可再次查看。

如需模型对话，先按[模型指南](docs/guides/models_ZH.md)配置自己的 provider 和精确模型，
再运行 `rpnh --frontend basic`；系统不预选 provider。
[工作流示例](examples/workflow_patterns/README_ZH.md)可在无模型凭据时演示并行与汇合。

## 兼容与发布边界

当前源码仍标为 `0.1.0rc3`，但 10 月 30 日发布的
[v0.1.0rc3 wheel 与 DSH 插件](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3)
**不包含** 11 月 1 日的源码修复；详见[源码与发布包边界](docs/validation/host-adapter-source-20261101_ZH.md)。

- **Codex：**默认 0.163.0；0.166.0 是需要显式选择的候选版本。
- **OpenCode：**默认 1.19.4；2.2.5 仅为显式启用的 REST／认证／非持久化事件子集，不是完整原生前端。
- **Stock DSH 插件：**与 DSH 0.2.2-alpha.2 手动配对，不代表通过托管升级验收；
  使用方式见[operator 指南](integrations/dsh/operator/README_ZH.md)。

这些集成只覆盖限定的兼容范围，不代表支持任意版本。

## 更多资料

[文档](docs/index_ZH.md) · [案例](examples/README_ZH.md) ·
[架构](docs/architecture/design_ZH.md) · [参与贡献](CONTRIBUTING_ZH.md)

## 许可证

[MIT](LICENSE)。第三方组件保留各自许可证，见[第三方声明](THIRD_PARTY_NOTICES.md)。
