# RPNH Harness

[English](README.md) | 中文

RPNH 是一个用于组合语言模型、原生工具和可复用工作流的 Agent 执行框架。
它提供对话式主会话、独立任务、版本化资源，以及贯穿执行过程的统一记录。

你可以用它将已有软件接入 Agent 应用，组织多步骤工作，并查看每次执行所依据的输入、
资源和结果。这套基础也适用于由人工持续调整，或通过应用自行定义的优化循环改进的系统。

## RPNH 为应用提供什么

- **组合 Agent 与已有代码。** 让语言模型节点负责推理与沟通，让原生插件完成计算或执行
  已有业务逻辑，并在同一工作流中声明各自的输入与输出。
- **关联工作过程与结果。** 登记的资源版本将输入、产物与具体执行关联起来。
  工作区结算合并兼容的修改，并记录并行修订之间的冲突。
- **独立于对话管理任务。** 启动、查看、发送消息、停止和恢复独立任务。
  每个任务保留自己的 Registry、执行状态与结果，主会话提供统一入口。
- **查看实际驱动执行的结构。** 只读 PetriNet 看板展示工作流结构、执行状态与已声明资源，
  并提供同一运行的概览和详细视图。
- **复用组件，自行选择模型与宿主。** 工作流与原生插件共用执行生命周期。
  模型提供商单独配置，交互入口可以选择 basic 终端、受支持的展示前端或可选宿主集成。

## 适用场景

| 应用 | RPNH 的作用 |
|---|---|
| 数据分析与报告生成 | 将模型理解、程序化计算与登记的输出组合起来。 |
| 涉及文件和共享资源的多步骤任务 | 在执行过程中明确依赖、资源访问和工作区修订关系。 |
| 可复用的专用 Agent | 将已登记组件组织成可查看、可调整的工作流。 |
| Agent 优化与递归自我改进（RSI）研究 | 为应用自行定义的候选、评价器和选择策略，提供执行记录、资源版本与受控工作流修改机制。 |

可以先从一个简单的原生工具任务开始，再按应用需要增加模型步骤、独立任务和更丰富的
资源声明。任务逻辑、评价标准和领域策略由应用组件定义。

## 执行如何组织

**Registry** 管理持久身份、记录、资源版本、检查点和登记的结果。
**类型化 PetriNet** 表达哪些操作具备启动条件、需要哪些输入和资源，以及结算后的结果
如何推动流程继续。

已声明的操作先经过准入，再交给执行器运行。返回的产物经过登记与结算后供后续步骤使用，
最终结果由已声明的终端绑定确定。前端与看板使用同一套记录进行交互和展示。

参阅[架构说明](docs/architecture/design_ZH.md)和
[运行参考](docs/reference/runtime-registry_ZH.md)，了解执行模型、工作区结算与工作流修订机制。

## 从源码开始

RPNH 支持 Linux 和 WSL2，需要 Python 3.11 或更高版本。仓库目前处于发布候选准备阶段，
当前使用入口为源码安装。

在仓库根目录执行：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
rpnh --help
rpnh config init
rpnh config build
rpnh config build --check
```

以上准备命令不调用模型。初始 provider/model catalog 为空；按照
[配置指南](docs/guides/models_ZH.md)添加所选提供商与具体模型。
环境和构建说明见[安装指南](docs/guides/installation_ZH.md)。

### 不配置模型，先运行一个原生工具

仓库自带的 `demo/add` 插件通过 RPNH 执行路径完成两数相加。
将它安装到同一环境，然后创建一次新的运行：

```bash
python -m pip install ./examples/native_plugin
rpnh plugins --config examples/native_plugin/plugins.json check
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-demo.XXXXXX")"
RUN_DIR="$DEMO_ROOT/run"
rpnh plugins --config examples/native_plugin/plugins.json run demo/add \
  --input examples/native_plugin/input.json --run-dir "$RUN_DIR"
rpnh net --run "$RUN_DIR" --show-resources
```

示例输入是 `{"left": 2, "right": 3}`。返回的 JSON 中，`output.value` 为 `5`，
并包含运行目录与终端证据引用。这一步会执行本地插件并创建运行数据。
[案例指南](docs/guides/examples_ZH.md)继续演示登记指令资源、模型—程序—模型工作流和两个
独立任务；[自定义指南](docs/guides/customization_ZH.md)解释插件契约。

### 通过支持的宿主运行一个真实任务

安装包内含一套 provider-neutral 任务，可分别通过 Basic、Codex、DSH 与 OpenCode 运行。
以下导出操作不会调用模型：

```bash
EXAMPLE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-example-parent.XXXXXX")/adapter-task"
rpnh examples list
rpnh examples export --output "$EXAMPLE_ROOT"
```

导出的各宿主指南使用同一任务和预期语义结果，不会配置 provider 或选择模型；只有用户明确
传入已授权 execution profile 并提交任务后才产生调用。任务包分别提供各宿主的脱敏验收证据，
原始 run 仍保持私有。详见[案例指南](docs/guides/examples_ZH.md)。

### 开始对话

完成模型配置后，启动依赖较少的前端：

```bash
rpnh --frontend basic
```

使用 `/agent` 创建独立任务，使用 `/workflow` 请 Designer 设计工作流，使用 `/tasks`
列出子任务。`/switch` 切换当前任务；`/task ID status` 和 `/task ID result` 查看进度与结果。
对话和任务执行使用已配置的模型与工具。消息、停止与恢复方式见
[使用与恢复指南](docs/guides/usage_ZH.md)。

### 查看一次运行

将 `RUN_DIR` 设为一个已有运行目录，例如上面的插件运行目录：

```bash
: "${RUN_DIR:?Set an existing run directory}"
rpnh net --run "$RUN_DIR"
rpnh net --run "$RUN_DIR" --resources-only
rpnh net --run "$RUN_DIR" --view --no-open
```

最后一条命令启动本地只读看板并输出访问地址。概览、详细流程和完整 PetriNet 视图，
便于从不同层次查看同一次运行。参阅[看板指南](docs/guides/viewer_ZH.md)。

## 前端与宿主集成

| 入口 | 用途 |
|---|---|
| `rpnh --frontend basic` | 内置终端，提供主会话和独立任务控制。 |
| `rpnh --frontend codex` | 固定版本的 Codex 展示前端，配置见[适配器指南](docs/guides/adapters_ZH.md)。 |
| `rpnh --frontend opencode` | OpenCode 1.18.32 展示前端，配置与交互功能见[OpenCode 指南](docs/guides/opencode_ZH.md)。 |
| `rpnh-dsh` | 可选的固定版本 DSH 宿主集成，安装及离线、已配置模型两种运行方式见[DSH 指南](docs/guides/dsh_ZH.md)。 |

这些集成共用 RPNH 的模型选择与受管执行机制。
各专题指南说明相应依赖与支持的交互功能。

## 文档

[English index](docs/index.md) · [中文文档](docs/index_ZH.md)

| 主题 | 指南 |
|---|---|
| 入门 | [安装](docs/guides/installation_ZH.md)、[案例](docs/guides/examples_ZH.md)、[模型](docs/guides/models_ZH.md)、[使用](docs/guides/usage_ZH.md) |
| 构建应用 | [自定义与插件](docs/guides/customization_ZH.md)、[声明参考](docs/reference/declarations_ZH.md) |
| 理解运行过程 | [看板](docs/guides/viewer_ZH.md)、[架构](docs/architecture/design_ZH.md)、[运行与 Registry](docs/reference/runtime-registry_ZH.md) |
| 使用维护与参与开发 | [排障](docs/guides/troubleshooting_ZH.md)、[开发](docs/guides/development_ZH.md)、[发布验证](docs/guides/release-validation_ZH.md) |

## 开发

代码修改使用有针对性的确定性离线测试。需要调用提供商的检查单独授权，并记录模型配置
和调用预算。凭据与生成的运行数据保存在本地运行目录。
[开发指南](docs/guides/development_ZH.md)说明测试与贡献流程。

## 许可证

RPNH 使用 [MIT License](LICENSE)。捆绑或固定的第三方组件继续使用各自许可证；参阅
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)以及可选集成附带的许可文件。
