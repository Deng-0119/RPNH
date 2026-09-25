# RPNH Harness

[English](README.md) | 中文

RPNH 是一个由 Registry 管理记录、使用类型化 PetriNet 执行的 agent harness。它提供对话式
主会话、独立 single-agent task、由 Designer 设计的图 workflow、用户自定义模型 provider、
可选宿主适配以及只读 PetriNet 看板。

Registry 管理持久身份、记录、checkpoint 和最终结果；PetriNet 的准入与结算管理执行结构。
前端、provider adapter、插件和看板只投影这些权威，不替代它们。

## 状态与平台

本仓库目前是本地公开候选准备树，尚未发布为软件包或托管服务。运行环境支持 Linux 和
WSL2，需要 Python 3.11 或更高版本；目前不支持原生 Windows 与 macOS。

统一产品目前包含：

- RPNH basic 终端前端；
- 固定版本的 Codex 兼容前端；
- 固定版本的 OpenCode 展示前端；
- 可配置的 local-process 与 external provider 路线；
- 原生受管插件和 workspace resources；
- 固定版本、按需启用的 DSH 集成；
- 只读 PetriNet 看板和宿主 run 选择适配器。

OpenCode 1.18.32 已完成本地离线验收，覆盖真实 Registry、安装后的 wheel，以及固定版 TUI
连接无 provider 的应用替身；不声称完成了真实 provider/model 调用。

## 不调用模型的安装

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
rpnh --help
rpnh config init
rpnh config build
rpnh config build --check
```

初始 provider/model catalog 为空。只添加用户有权使用的 provider 和 exact model；RPNH 不预设
新用户应该使用哪个模型。

参阅[安装](docs/guides/installation_ZH.md)和
[provider/model 配置](docs/guides/models_ZH.md)。

## 使用 RPNH

启动依赖较少的 basic frontend：

```bash
rpnh --frontend basic
```

安装文档指定的依赖后，启动固定 Codex 展示前端：

```bash
rpnh --frontend codex
```

单独安装 OpenCode 1.18.32 后，启动固定 OpenCode 展示前端：

```bash
rpnh --frontend opencode
```

使用前请阅读 [OpenCode 前端边界](docs/guides/opencode_ZH.md)。

在不取得 writer 权限的情况下查看已有 run：

```bash
rpnh net --run /path/to/run
rpnh net --run /path/to/run --show-resources
rpnh net --run /path/to/run --resources-only
rpnh net --run /path/to/run --view --no-open
```

默认视图隐藏 resource place。显式资源视图只展示真实 net 已声明的资源，不会伪造节点。

## 可选 DSH 集成

wheel 包含一份固定 DSH 集成和 `rpnh-dsh` 启动器。DSH 与其他宿主共用 RPNH provider input
port、Registry grant、managed tools 和结果记账，不实现私有 provider 栈。

```bash
rpnh-dsh --help
rpnh-dsh ../deepseek-harness-rpnh \
  --offline --root "$HOME/.rpnh/dsh" \
  --data-file numbers.json --task "Read the numbers and compute their sum"

rpnh-dsh ../deepseek-harness-rpnh \
  --execution /absolute/path/to/selection.json \
  --root "$HOME/.rpnh/dsh" --task "Reply with READY."
```

启用前阅读 [DSH 使用说明](docs/guides/dsh_ZH.md)和
[适配器指南](docs/guides/adapters_ZH.md)。

## 文档

- [中文文档导航](docs/index_ZH.md)
- [安装](docs/guides/installation_ZH.md)
- [Provider 与模型配置](docs/guides/models_ZH.md)
- [使用、task、workflow 与恢复](docs/guides/usage_ZH.md)
- [PetriNet 看板](docs/guides/viewer_ZH.md)
- [宿主适配](docs/guides/adapters_ZH.md)
- [OpenCode 前端](docs/guides/opencode_ZH.md)
- [自定义与插件](docs/guides/customization_ZH.md)
- [排障](docs/guides/troubleshooting_ZH.md)
- [架构](docs/architecture/design_ZH.md)
- [运行与 Registry 参考](docs/reference/runtime-registry_ZH.md)

[英文文档导航](docs/index.md)与中文入口保持对应。

## 开发与验证

自动测试保持确定性和离线。离线套件通过不代表用户自有 provider route 一定可达。真实模型
测试需要单独授权，并记录 exact profile/model/route、物理调用预算和私有证据目录。

不要提交凭据、生成的 profile、Registry 数据库、run 输出、provider transcript 或本地绝对
路径。参阅[开发说明](docs/guides/development_ZH.md)。

## 许可证

RPNH 使用 [MIT License](LICENSE)。捆绑或固定的第三方组件继续使用其原许可证；参阅
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)以及可选集成附带的许可文件。
