---
name: rpnh-getting-started
description: "从安装、无模型示例和证据检查开始，再进入已配置会话及恢复。"
metadata:
  document-kind: tutorial
  audience: operator-and-developer
  language: zh-CN
  counterpart: getting-started.md
  revision: "2026-10-13.3"
  status: source-reviewed
  basis: "public source b16ecce83ba603b3c15f1c770bce172d4f44f88e; not a new runtime validation"
---

[English](getting-started.md) | [中文](getting-started_ZH.md)

# 从这里开始：第一次 RPNH 运行

新交互 `rpnh` 会显示前端菜单。`rpnh init` 配置模型并选择至少一种前端，`rpnh --resume PATH` 沿用上次界面；见[前端配置与切换](frontends_ZH.md)。

[文档首页](../index_ZH.md) · [命令参考](../reference/cli_ZH.md) · [排障](troubleshooting_ZH.md)

本路线从已安装发行包出发，运行一个小型注册工具、查看其 PetriNet，再按需进入模型驱动会话。命令供你在自己的环境执行。本页已按链接中的源码核对，不是一次新的安装、运行或 provider 测试记录。

## 1. 选择并安装一份来源

使用 **Linux 或 WSL2，Python 3.11+**。按[安装指南](installation_ZH.md)在独立虚拟环境中安装选定且已认可的 checkout，或由它构建的 wheel。Basic 不需要 Codex 或 Node。当前源码候选不支持原生 macOS、Windows。

发行包名是 `rpnh-harness`，Python 导入名是 `cpn`，用户命令是 `rpnh`。源码候选声明 `0.1.0rc2`；还需记录精确 commit 或 wheel 摘要。较早发布的 rc1 wheel 不包含这里所有命令和命名导出。选择产物前先读[发布与版本说明](installation_ZH.md)。

在该环境执行：

```bash
rpnh --help
rpnh config --help
rpnh examples list
```

这些命令不调用模型。最后一条列出已安装的可导出示例，并标明仅源码提供的案例。若命令不存在，先核对可执行文件与导入包；参见[安装排障](troubleshooting_ZH.md)。

## 2. 不接模型，先跑一个小示例

`native_plugin` 导出包含可信 Python 插件及输入。它演示注册工具操作，不代表 agent 推理。列举和导出读取或复制文件；安装插件改变环境；`run` 执行插件并创建真实 Registry/运行状态。运行需要受支持的本地进程及 Unix-domain socket 权限。

导出目录和运行目录都必须尚不存在。先审阅导出的插件，再安装。保留当前 shell，让后续步骤可继续使用变量：

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-first.XXXXXX")"
EXAMPLE_ROOT="$DEMO_ROOT/native"
rpnh examples export --example native_plugin --output "$EXAMPLE_ROOT"
# 审阅导出源码后，安装到当前专用环境。
python -m pip install "$EXAMPLE_ROOT/examples/native_plugin"
PLUGIN_CONFIG="$EXAMPLE_ROOT/examples/native_plugin/plugins.json"
rpnh plugins --config "$PLUGIN_CONFIG" inspect
rpnh plugins --config "$PLUGIN_CONFIG" check
RUN_DIR="$DEMO_ROOT/add-run"
rpnh plugins --config "$PLUGIN_CONFIG" run demo/add \
  --input "$EXAMPLE_ROOT/examples/native_plugin/input.json" \
  --run-dir "$RUN_DIR"
```

`inspect` 读取已安装元数据和选择，不加载 factory。`check` 导入选定的可信 factory 并编译声明，不调用具体 operation。对于随附输入，检查响应中 `output.value` 为 `5`、`terminal_evidence_ref` 非空，且模型调用为零。shell 退出或界面显示一个数值，都不能单独证明终态。完整约定与 wheel 自检见[原生插件示例](../../examples/native_plugin/README_ZH.md)。

若导出提示目标已存在，请选新目录；导出会保护已有修改。若运行被进程或 IPC 权限阻止，保留错误，在受支持环境执行同一文档中的示例。不要修改 Registry 文件来制造完成状态。

## 3. 检查实际发生了什么

继续使用上一步的 `RUN_DIR`：

```bash
rpnh net --run "$RUN_DIR" --format json
rpnh net --run "$RUN_DIR" --show-resources
rpnh net --run "$RUN_DIR" --result-evidence
rpnh net --run "$RUN_DIR" --view --no-open
```

最后一条启动本地只读 Viewer；在浏览器打开它打印的 loopback URL。`--no-open` 仅禁止自动打开浏览器。Viewer 只读 Registry，但启动过程仍会创建服务进程和监听端口；查看完后停止该服务。

按 [Viewer 指南](viewer_ZH.md)切换全景 PN、agent Overview、firing 细节、资源及 checkpoint。完整 PN 表示可能的结构；所选运行的 firings 表示实际执行；版本比较是另一种视图。有 agent 时，业务 PN 表达 agent 的操作与交接；下级执行 PN 记录文件物化、workspace finalization 等 harness 机制。AgentLoop 日志本身不是执行 PN。

接下来若想运行带 agent 阶段但不调用 provider 的工作流，阅读[混合示例复制修改教程](examples_ZH.md)。其中脚本模型是确定性 fixture，不是真实模型测评。完整 agent 协作项目见[项目示例](../../examples/project_collaboration/README_ZH.md)及[操作指南](project-entry_ZH.md)。

## 4. 准备好后再配置模型

在交互式终端执行：

```bash
rpnh init
rpnh config list
rpnh config show
rpnh doctor
```

选择自己的 provider 和精确模型，在本地 shell 设置所需凭据变量。不要把密钥值放入仓库文件、命令参数或 issue。`doctor` 检查本地配置，不发送 provider 请求；ready 不代表凭据、网络或模型实际可用。需要结构化输出时使用 `rpnh doctor --json`。非交互配置使用[模型指南](models_ZH.md)中的 catalog 流程，不使用交互式 `init`。

[配置指南](configuration_ZH.md)解释 profile 优先级、运行限制和持久化身份；[Provider/model 配置](../PROVIDER_MODEL_CONFIGURATION_ZH.md)解释 catalog 字段。可选客户端按[适配器](adapters_ZH.md)、[OpenCode](opencode_ZH.md)或 [DSH](dsh_ZH.md)指南选择固定版本。

## 5. 启动会话并保留身份

以下命令及提交的任务可能执行模型和工具、产生 provider 费用。只有在所选路由和工作均获授权后执行：

```bash
SESSION_DIR="$DEMO_ROOT/session"
rpnh --frontend basic --session-dir "$SESSION_DIR"
```

在 RPNH 提示符输入 `/help` 查看控制命令。要创建子任务，输入 `/agent` 后接任务正文，保留返回的任务 ID。使用：

```text
/tasks
/task TASK_ID status
/task TASK_ID result
/task TASK_ID checkpoints
```

将 `TASK_ID` 替换为返回的精确 ID。`result` 读取注册结果，不会等待任务完成。`/quit` 离开前端，独立子任务仍可能继续。需要 checkpoint 停止时使用 `/task TASK_ID stop`，并核对最终状态后再认定已停止。

退出后重新打开**同一会话根目录**：

```bash
rpnh --frontend basic --resume "$SESSION_DIR"
```

恢复时省略 `--execution` 会使用该会话持久化的 profile。`/task TASK_ID resume` 继续 owner-stopped 子任务；`reopen CHECKPOINT` 则从一个精确旧切面追加新执行代际。如果调用为 `submission_unknown`，保留证据，不要盲目重复。按 [checkpoint 恢复](checkpoint-recovery_ZH.md)逐状态判断。[使用指南](usage_ZH.md)覆盖 main `/resume`、main `/rollback`、workflow 目标及前端切换。

## 下一步

- 修改输入、图或工具：[示例](examples_ZH.md)、[定制](customization_ZH.md)
- 复用依赖完整的包：[包教程](package-reuse-example_ZH.md)、[环境准备](../environment-preparation_ZH.md)
- 查询和比较已授权来源：[独立 Registry reader](independent-registry-reader_ZH.md)、[跨网比较](comparison-context_ZH.md)
- 接入 Python 或其他前端：[API 参考地图](../reference/api-index_ZH.md)、[源码地图](repository-layout_ZH.md)
- 排查失败：[排障](troubleshooting_ZH.md)、[安全恢复](checkpoint-recovery_ZH.md)

源码对应：[安装入口](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/pyproject.toml)、[CLI 与会话控制](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh_cli.py)、[示例导出](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/examples/cli.py)、[导出目录](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/examples/catalog.json)、[插件命令副作用](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/cli.py)。
