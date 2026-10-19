---
name: rpnh-cli-reference
description: "查找安装命令、精确选项族、副作用及任务控制语义。"
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: zh-CN
  counterpart: cli.md
  revision: "2026-10-19.2"
  status: source-reviewed
  basis: "current source for unified DSH and preferences; older source links retain their revision; not a new runtime validation"
---

[English](cli.md) | [中文](cli_ZH.md)

# 命令行参考

[文档首页](../index_ZH.md) · [第一次运行](../guides/getting-started_ZH.md) · [API 地图](api-index_ZH.md)

本页按所链接源码中的已安装 parser 整理。完整参数帮助以对应产物的 `--help` 为准。shell 命令、Basic 斜杠命令和受控项目 HOST 是不同接口。下文大写参数都是占位符，不是实际任务 ID 或路径。

## 已安装入口

| 入口 | 用途 | 源码与指南 |
|---|---|---|
| `rpnh` | 用户会话、配置、示例、插件、包与 Viewer | [Parser/dispatch](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh_cli.py)、[使用](../guides/usage_ZH.md) |
| `rpnh-configure-providers` | Catalog/profile 生成辅助入口 | [入口声明](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/pyproject.toml)、[模型设置](../guides/models_ZH.md) |
| `rpnh-dsh CHECKOUT ...` | 启动高级旧版 registered headless host；使用独立 root／session 语义 | [Launcher](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/dsh/launcher.py)、[DSH](../guides/dsh_ZH.md) |

`python -m cpn` 是已有源码示例及投影工具使用的低层入口，见其 [parser](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/cli.py)。普通用户会话优先使用 `rpnh`。项目协作使用下文独立的显式模块入口；当前源码没有 `rpnh project` 命令。

## 会话

```text
rpnh [--execution PATH] [--save-default]
     [--session-dir NEW_DIR | --resume SESSION_DIR]
     [--prompt TEXT] [--frontend auto|basic|codex|opencode|dsh]
```

| 选项 | 含义 |
|---|---|
| `--execution PATH` | 显式 execution profile；恢复时必须匹配持久化的精确 profile |
| `--save-default` | 把显式提供的 profile 保存为默认，再启动 |
| `--session-dir NEW_DIR` | 新会话使用尚不存在的操作目录 |
| `--resume SESSION_DIR` | 重新打开原会话根目录；与 `--session-dir` 互斥 |
| `--prompt TEXT` | 执行一次 main-session turn 后退出输入循环；可能执行模型和工具 |
| `--frontend ...` | 显式参数优先于保存偏好；默认 `auto` 在交互终端选择兼容 Codex，否则用 Basic；OpenCode、DSH 需显式参数或保存偏好 |

新会话省略 `--execution` 时使用 `RPNH_EXECUTION_CONFIG` 或保存的默认值；恢复时省略则使用该会话的持久化选择。`--prompt` 使用 Basic；显式 OpenCode 或 DSH 与 `--prompt` 组合会在解析模型前被拒绝。客户端版本与支持平台见[安装](../guides/installation_ZH.md)及[适配器](../guides/adapters_ZH.md)，不要默认使用上游最新版。

打开会话、提交输入和显式继续暂停 turn 是不同操作。脚本化生命周期或切换可写前端前，请阅读[使用指南](../guides/usage_ZH.md)。

## 初始化与配置

| 命令 | 副作用 |
|---|---|
| `rpnh init` / `rpnh config setup` | 交互式模型设置/选择；需要终端，写本地配置 |
| `rpnh config add` | 交互式新增模型 |
| `rpnh config init [--catalog PATH]` | 创建空的规范 catalog |
| `rpnh config build [--catalog PATH] [--output-root DIR] [--check]` | 生成 profile；`--check` 仅验证已有生成状态，不改写 |
| `rpnh config list` / `rpnh config show` | 读取生成的 profile / 当前选择 |
| `rpnh config use PROFILE_OR_PROVIDER [MODEL] [--effort EFFORT]` | 持久化已声明 profile 或精确 provider/model/effort 选择 |
| `rpnh config frontend [auto\|basic\|codex\|opencode\|dsh] [--dsh-source CHECKOUT]` | 独立于模型设置查看或保存前端偏好；不调用 provider、不改变模型选择 |
| `rpnh doctor [--execution PATH] [--json]` | 离线本地检查，不发送 provider 请求 |
| `rpnh config doctor [--execution PATH] [--json]` | 相同的本地 doctor 边界 |

前端偏好保存在 `config_path()` 旁（模型配置由 `RPNH_CONFIG` 选择，默认
`~/.config/rpnh/config.json`），文件名为 `config_path().name + ".frontend.json"`，
例如 `config.json.frontend.json`。`DSH_SOURCE` 优先于保存的 `--dsh-source`；保存源码
路径只做路径归一化，不准备或检查 checkout。未保存偏好时使用 `auto`。

`doctor` 本地 ready 时返回 0，否则返回 1；ready 不证明在线端点可用。配置/parser 错误与 readiness 分开理解。见[配置](../guides/configuration_ZH.md)、[模型](../guides/models_ZH.md)及[实现](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh_cli.py)。

## 已安装示例

| 命令 | 副作用 |
|---|---|
| `rpnh examples list` | 读取已安装导出目录及仅源码示例的前提 |
| `rpnh examples export --output NEW_DIR [--example NAME]` | 复制到尚不存在的目录；默认 `adapter_task` |
| `rpnh examples verify --result FILE [--expected FILE]` | 按 stock 或显式期望验证仅答案的 batch-summary JSON |

导出不会安装依赖或运行示例。`verify` 是 batch-summary 任务验证器，不是全部案例的通用验证器。源码：[示例 parser](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/examples/cli.py)、[目录](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/examples/catalog.json)。每个任务的运行和评分请按[示例指南](../guides/examples_ZH.md)。

## 原生插件

```text
rpnh plugins [--config CONFIG] list
rpnh plugins [--config CONFIG] inspect [--diagnose-selection]
rpnh plugins [--config CONFIG] check
rpnh plugins [--config CONFIG] build PLUGIN/OPERATION
rpnh plugins [--config CONFIG] run PLUGIN/OPERATION --input FILE --run-dir NEW_DIR
```

`--config` 放在子命令**之前**；环境变量 `RPNH_PLUGIN_CONFIG` 是显式替代路径。`inspect` 读取惰性安装元数据和选择。`list`、`check`、`build` 加载选定的可信 factory；check/build 编译但不调用 operation。`run` 调用 handler 并写入运行证据。pure、无模型操作仍然执行代码。见[定制](../guides/customization_ZH.md)、[插件契约](extensions-observation_ZH.md)及 [parser](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/plugins/cli.py)。

## 只读网与结果检查

来源必须二选一：`--run RUN_DIR` 或 `--read-host-config FILE`。

| 模式 | 选项与副作用 |
|---|---|
| `rpnh net --run RUN_DIR` | 默认文本投影；`--format json`、`--show-resources`、`--resources-only`、`--node ID`、`--output FILE` 控制投影和输出 |
| `rpnh net --run RUN_DIR --result-evidence` | 读取已注册请求/结果证据与现有限制 |
| `rpnh net --run RUN_DIR --view` | 启动只读 Viewer；可用 `--show-resources`、`--no-open`、`--host`、`--port`、`--max-checkpoints`、`--max-firings` |
| `rpnh net --read-host-config FILE --preflight` | 打开、描述并关闭所选独立 read-HOST，不启动监听或查询对象 |
| `rpnh net --read-host-config FILE --view` | 打开已授权 read-session 比较 Viewer |

`--view`、`--preflight`、`--result-evidence` 互斥。`--view` 拒绝 `--resources-only`、`--node`、`--output` 及非默认 `--format`。检查模式拒绝资源/Viewer 投影选项；`--preflight` 必须配 read-HOST，`--result-evidence` 必须配 run。

Viewer 默认 `--host 127.0.0.1`、`--port 0`、`--max-checkpoints 2048`、`--max-firings 2000`；后两个限制用于 run-backed Viewer 路径。`--no-open` 禁止打开浏览器，不禁止监听；`--output` 写文件。保持 Viewer 绑定 loopback。见 [Viewer](../guides/viewer_ZH.md)、[独立读取](../guides/independent-registry-reader_ZH.md)及[模式校验](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh_cli.py)。

## 可移植包与接收端环境

```text
rpnh package preview ZIP
rpnh package resolve ZIP [--local-package ZIP ...] [--entry ENTRY_ID]
```

预览和解析仅处理数据，不执行包内容或安装依赖。解析默认 entry `main`，输出精确 lock 字节，应原样保留。它们与 `examples export`、Registry 子网交换是不同操作。

| 环境命令 | 通用包材料之外的必需输入 |
|---|---|
| `check-environment` | `--selection FILE` 或 `--binding FILE`；可选 `--resolved-selections FILE` |
| `resolve-environment` | Selection/binding 及 `--check FILE`；可选重复 `--wheel FILE` 和 `--allow-existing-changes` |
| `plan-environment` | Selection/binding、`--check FILE`、`--resolved-selections FILE`；可选重复 `--wheel FILE` |
| `prepare-environment` | Selection/binding、`--check FILE`、`--resolved-selections FILE`、`--plan FILE`；可选 `--state-dir DIR` |
| `run` | `--binding FILE`、`--resolved-selections FILE`、`--receipt FILE`、`--owner-request FILE`、`--run-dir NEW_DIR` |
| `setup-instructions` | `--plan FILE --output FILE`，可选 `--format text|json`；不使用通用包材料参数 |

前五行的通用材料是 `--lock FILE --archive ZIP`，可选重复 `--local-package ZIP` 及 `--entry ENTRY_ID`。它们可用 `--output FILE` 写私有完整文档，拒绝覆盖。`run --include-terminal-result` 还必须提供私有 `--output`。

检查/解析/计划不授予准备或执行权限。`prepare-environment` 要求确认精确计划身份；`run` 单独要求精确业务运行授权，确认路径需要交互式终端。环境命令退出码：0 表示所检查范围完成，2 输入/契约无效，3 要求未解决或不支持，4 授权/准备失败，5 binding 过期或取消准备。环境检查通过不等于业务完成。

请使用完整[包教程](../guides/package-reuse-example_ZH.md)与[环境准备](../environment-preparation_ZH.md)，不要猜测 lock/selection/receipt 文档。源码：[包 parser](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/package_cli.py)、[环境 parser](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/environment_cli.py)。

## Basic 终端控制

这些命令在 `rpnh --frontend basic` 内输入，不是在 shell 或 stock Codex/OpenCode 斜杠命令表中输入：

| 控制 | 含义 |
|---|---|
| `/help`、`/tasks`、`/current` | 帮助、子任务链接列表、当前焦点 |
| `/agent PROMPT`、`/workflow PROMPT` | 创建独立 agent / 请求 Designer workflow；可能执行模型和工具 |
| `/switch ID`、`/switch main` | 切换焦点，不停止子任务 |
| `/task ID status`、`/task ID result`、`/task ID checkpoints` | 检查任务状态、注册结果或精确提交切面 |
| `/task ID net [view [--show-resources] [--no-open]]` | 摘要或只读 Viewer |
| `/task ID message TEXT` | 向单 agent 排队输入 |
| `/task ID message TARGET :: TEXT` | 向显式 workflow 目标排队输入 |
| `/task ID stop`、`/task ID resume` | 请求 checkpoint 停止 / 继续当前 owner-stopped 切面 |
| `/task ID reopen CHECKPOINT [:: REASON]` | 从所选精确切面追加执行代际；可能执行模型和工具 |
| `/status`、`/result`、`/net`、`/message`、`/stop`、`/resume` | 操作当前子任务；`/resume` 对 main 有独立语义 |
| main `/resume`、main `/rollback` | 结算或继续暂停的 main turn；或返回上一个完成的主会话 turn |
| `/quit` | 退出前端，不停止独立任务 |

普通文本进入 main 或所选单 agent；所选 workflow 需要 `TARGET :: TEXT`。该 parser 没有短写 `/checkpoints`、`/reopen`，应使用 `/task ID ...`。stop 请求被接受不等于 checkpoint 完成。Resume、checkpoint reopen、rollback 的数据和副作用语义不同，按[恢复决策指南](../guides/checkpoint-recovery_ZH.md)与[未知副作用排障](../guides/troubleshooting_ZH.md)处理。

## DSH Web 控制

`rpnh --frontend dsh` 打开采用原生 DSH chat／composer／theme 组件及 AgentLoop/Session
的 RPNH Web 前端，不是完整 stock DSH Web shell 或 TUI；该固定上游没有随包终端 TUI。
浏览器未自动打开时访问打印的 loopback URL。`Ctrl+C` 退出宿主，仅对其持有的 main turn
请求 checkpoint stop。Basic、Codex、OpenCode 与 DSH Web 共用
`--session-dir`／`--resume`、Registry 历史和唯一可写 owner。重开历史不会自动继续暂停 turn。

在 Web 输入框内使用以下命令；完整参数见 `/rpnh-help`：

| 控制 | 含义 |
|---|---|
| `/rpnh-help`、`/rpnh-tasks` | 共享帮助与关联任务列表 |
| `/rpnh-task ID ...`、`/rpnh-net` | 任务操作与 net 检查 |
| `/rpnh-agent TEXT`、`/rpnh-workflow TEXT` | 启动独立工作，可能执行模型与工具 |
| `/rpnh-resume`、`/rpnh-rollback` | 显式继续暂停的 main turn，或将主对话回退到上一个已完成 turn |
| `/rpnh-send UNIQUE_ID TEXT` | 提交具有显式身份的 main 输入 |

Stop 按钮请求 checkpoint stop。模型／effort 控制来自 RPNH catalog；不加载 DSH 原生
provider、工具或 workspace filesystem。参阅 [DSH 设置与边界](../guides/dsh_ZH.md)和
[统一前端验证记录](../validation/dsh-unified-20261019_ZH.md)。`rpnh-dsh` 仍为独立的高级
旧版 headless host，其 `--root`、`--session-id`、frame 与 attempt-budget 选项见该指南。

## 受控项目 HOST

公开的本地项目入口是 `python -m cpn.rpnh.collaboration.project_local`，包含 `init`、`serve`、`call`。它承载显式受控主体的项目操作。Propose、review、merge、accept、adopt、read-plan、download 等 JSON 请求操作不是普通 shell 子命令，也不构成远程账号认证。字段、权限、精确引用、注册和实际任务执行见[项目操作指南](../guides/project-entry_ZH.md)。源码：[本地 HOST](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/project_local.py)、[请求契约](https://github.com/Deng-0119/RPNH/blob/b16ecce83ba603b3c15f1c770bce172d4f44f88e/cpn/rpnh/collaboration/project_entry.py)。
