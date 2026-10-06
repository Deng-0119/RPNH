---
name: rpnh-configuration-reference
description: "配置全部受支持的用户策略，并区分固定协议边界。"
metadata:
  document-kind: reference-guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: configuration.md
  revision: "2026-10-06.1"
  status: source-reviewed-pre-release
---

[English](configuration.md) | [中文](configuration_ZH.md)

# 配置与上限总表

RPNH 有三类用户自有配置入口：provider/model catalog 管理模型和标准 Agent 运行策略，
plugin catalog 管理已安装原生 operation，workflow/module declaration 管理应用拓扑与业务预算。
命令行参数只选择 session、run 或视图，不建立第二套策略。生成的 adapter/execution profile
是派生文件，不能手工修改。

## 文件、选择器与优先级

| 配置面 | 默认位置 | 覆盖或选择方式 |
|---|---|---|
| Provider/model catalog | `~/.config/rpnh/provider_models.json` | `RPNH_PROVIDER_CATALOG`，或 `rpnh config build --catalog PATH` |
| 生成的 profile | `~/.config/rpnh/profiles/` | build 的 `--output-root`；`RPNH_PROFILE_DIR` 指向其中的 `execution` 目录 |
| 保存的当前 profile | `~/.config/rpnh/config.json` | `RPNH_CONFIG` |
| 本次 execution selection | 保存的当前 profile | 依次为 `--execution`、`RPNH_EXECUTION_CONFIG`、保存的 profile |
| 原生 plugin catalog | 默认无 plugin | `rpnh plugins --config PATH` 或 `RPNH_PLUGIN_CONFIG` |
| Codex 二进制查找 | `codex` | `RPNH_CODEX_BIN`；仍严格检查受支持版本 |

执行 `rpnh config init` 后只编辑 catalog，再依次运行 `rpnh config build`、
`rpnh config build --check`、`rpnh config use ...` 与 `rpnh config show`。`show`
会显示 provider/model、恢复策略、context 策略与不含凭据的 runtime policy。
`--save-default` 保存显式 `--execution`。`ready` 只代表指定凭据变量存在，不代表网络、
模型、授权或计费已验证。
若模型声明了 effort，`rpnh config use ... --effort EFFORT` 从该模型配置的值中选择；
省略时使用其配置的默认值。

## Provider 与精确模型字段

catalog 根对象包含固定的 `schema_version` 与 `providers` 数组。每个 provider 项包含
`provider`（用户自有的稳定 provider 名称）、`display_name`（展示文本）和非空 `models`
数组。每个 model 项支持以下公开字段。未注明可选且没有默认值的字段必须提供。

| 字段 | 含义与校验 |
|---|---|
| `profile` | 唯一、适合文件名的小写 profile 名。 |
| `model_condition` | 原样发送的精确模型标识；RPNH 不限制模型名单。 |
| `reasoning_efforts` | 可选对象，包含非空且无重复的 `supported` 列表，以及属于该列表的 `default`。这些值是用户自有的精确模型元数据；RPNH 不内置供应商对照表。 |
| `adapter` | 下述唯一一条 `external_provider` 或 `local_process` route。 |
| `timeout_seconds` | 正数，正式请求超时；交互初始化值为 900。 |
| `max_output_tokens` | 正数，输出 token 请求上限；交互初始化值为 8192。 |
| `max_response_bytes` | 正数，返回 envelope 字节上限；交互初始化值为 16777216（16 MiB）。 |
| `context_window_tokens` | 可选的精确模型容量；提供后启用主动 context-pressure compaction。 |
| `context_compaction_retained_tokens` | 可选的最近完整消息尾部；同时提供 window 时必须更小。 |
| `runtime` | 可选的完整运行策略对象；省略时使用下一节默认值，生成 profile 总会写入解析后的完整对象。 |

`external_provider` adapter 包含以下字段：

| 字段 | 含义 |
|---|---|
| `adapter_kind` | 固定为 `external_provider`。 |
| `route_id` | 该生成 adapter 内适合文件名的 route identity。 |
| `backend` | 用户自有的 backend/provenance 标签。 |
| `protocol` | 固定为 `openai_chat_completions/v1`。 |
| `endpoint` | 精确 chat-completions endpoint；远程 route 必须为 HTTPS，明文 HTTP 只允许 loopback。 |
| `credential` | `null`，或包含 `environment`、`header`、`prefix` 的对象；只保存变量名与 header recipe，不保存 secret。 |
| `headers` | 静态非 secret HTTP header mapping，可为空。 |
| `recovery` | 下一节说明的完整同 route 恢复对象。 |

`local_process` adapter 包含以下字段：

| 字段 | 含义 |
|---|---|
| `adapter_kind` | 固定为 `local_process`。 |
| `argv` | 正式请求使用的非空命令/参数向量。 |
| `probe_argv` | 非空的本地 readiness 命令向量。 |
| `env` | 传给子进程的静态环境增量，可为空。 |
| `inherit_env` | 明确从 launcher 继承的环境变量名，可为空。 |

两个本地命令向量中的 `{model}` 都会替换为精确模型。若声明了 `reasoning_efforts`，
正式 `argv` 必须包含 `{reasoning_effort}`，并在每个生成的不可变 variant 中替换。
本地进程和 probe 仍可能有外部副作用或费用。

## 标准 Agent runtime policy

在 model 项中写入完整 `runtime` 对象即可修改下列值，无需改 Python。生成的 execution
profile 会记录解析后的值，因此 run 及其 resume 使用同一份精确策略。

| 字段 | 默认值 | 作用 |
|---|---:|---|
| `max_turns_per_node` | 12 | 每个 single-agent stage 或 workflow node 的模型/工具回合预算，同时限定对应 module bucket。设为 `null` 时会登记显式的无计量 authority，不设置人工的 node、工具回合或全任务累计调用上限。 |
| `max_parallel_nodes` | 4 | workflow 同时 in-flight 的 node 上限；single-agent 始终串行。 |
| `main_history_message_limit` | 20 | 新 main Designer prompt 包含的最近完整 role/body 消息数；Registry 仍保存全部已提交历史。 |
| `context_pressure_trigger_ratio` | 0.90 | 达到已声明 context window 的该比例时主动压缩；为输出 token 预留空间可能更早触发。 |
| `context_tool_output_byte_limit` | 10000 | 每条模型可见 tool result 投影的最大字节数，包括紧邻下一 turn 与压缩历史；Registry 完整证据不变且仍可分页读取，最小 128。 |
| `workspace.timeout_seconds` | 120 | firing-private workspace 命令上限；tool 请求可选择更小值。 |
| `workspace.memory_bytes` | 4294967296 | workspace 进程地址空间上限。 |
| `workspace.process_limit` | 64 | workspace 进程数上限。 |
| `workspace.source_size_bytes` | 16777216 | workspace script/source 最大字节数。 |
| `workspace.input_size_bytes` | 16777216 | workspace 文件发布/输入最大字节数。 |

示例片段：

```json
{
  "timeout_seconds": 900,
  "max_output_tokens": 8192,
  "max_response_bytes": 16777216,
  "context_window_tokens": 131072,
  "context_compaction_retained_tokens": 20000,
  "runtime": {
    "max_turns_per_node": 12,
    "max_parallel_nodes": 4,
    "main_history_message_limit": 20,
    "context_pressure_trigger_ratio": 0.9,
    "context_tool_output_byte_limit": 10000,
    "workspace": {
      "timeout_seconds": 120,
      "memory_bytes": 4294967296,
      "process_limit": 64,
      "source_size_bytes": 16777216,
      "input_size_bytes": 16777216
    }
  }
}
```

## 恢复、attempt 与幂等

外部 provider transport 恢复在 `adapter.recovery` 下由用户配置：

| 字段 | 约束 | 含义 |
|---|---:|---|
| `strategy` | 固定 `bounded_same_route_health_probe/v1` | 不切换 provider/model/credential route。 |
| `max_probe_attempts` | 1–3 | 一次健康序列的 probe 上限；首次成功立即停止。 |
| `probe_timeout_budget_seconds` | 正数 | 该 probe 序列共享的墙钟预算。 |
| `max_probe_success_formal_failure_cycles` | 1–3 | 同一 logical call 中“probe 成功但正式请求失败”的循环上限。 |

这些数值统计 provider-private 物理恢复调用，不是 Agent turn，也不能把未知提交变成可安全重放。
logical call ID、物理 attempt ID、Registry idempotency key、普通 turn 的单 attempt 语义、
有界 compaction retry 与 unknown-submission reconciliation 都是协议不变量，因此没有通用的
“重试 N 次”旋钮：修改它们会改变证据与 exactly-once 边界，而不是普通性能调节。
probe 提示 `Reply with READY.` 及 8-token 上限同样是固定协议细节。

编译后 Agent operation 中的 `provider_attempt_limit` 是固定的 Registry admission envelope，
不是自动重试次数。它只容纳当前 Agent 协议规定的 attempt 形状（普通调用一次，或另行限定的
compaction 路径），因此不会复制到 catalog。公开可配置的 transport recovery 控制只有上表
三个 external-provider recovery 字段。

## Workflow 与 plugin declaration

Designer 或 module 作者在 declaration 中配置图策略，而不是修改全局源码。
`max_rework_cycles` 是非负 workflow graph 预算；零表示禁用 feedback/rework。所选 runtime
的 `max_turns_per_node` 决定各 node attempt bucket，`max_parallel_nodes` 决定调度并发。
每个 node 的模型 profile 和 tool allowlist 仍属于 graph。`null` 回合限制不会关闭
owner-stop、provider 重试处理、workspace 资源约束、context-window 处理或
Registry/PetriNet 的准入与 settlement。

原生 `PluginOperation` 声明 `timeout_seconds`（1–7200，默认 60）和
`max_result_bytes`（1–16777216，SDK 默认 1048576）。DSH managed tool 使用同一
16777216-byte 上限；应按 operation 合法输出确定声明值，不能直接照抄上限或默认值。plugin 配置和环境
plugin catalog 根对象固定使用 `schema_version: rpnh/plugins/v1` 和 `plugins` 数组。每个
被选择项严格包含 `name`、`entry_point`、`version`、`config`、`environment`。
`config` 必须符合已安装 plugin 自己发布的 schema；`environment` 只写变量名，secret 留在
进程环境中。没有 catalog 就表示没有选择 plugin。

Designer workflow graph JSON 声明 `nodes`、`arcs`、`ingress`、`egress`、
`max_rework_cycles`。node 配置包含 `node_id`、`instruction`、输入/输出 port 与
`execution`；在 schema 允许的位置，execution 可选择受支持的 `role`、排序后的 tool
allowlist、已配置的 `profile_id` 或一个原生 `plugin` selector。port 使用 `port_id` 与
`artifact_id`；arc 使用 `arc_id`、source/target endpoint 与 `kind`。这些是 graph-specific
声明；profile runtime 对象提供共享的 turn 与并行上限。通用 module 配置面见
[声明参考](../reference/declarations_ZH.md)。

## Session、前端与 viewer 参数

`rpnh` 支持 `--execution`、`--save-default`、`--session-dir`、`--resume`、
`--prompt`、`--frontend {auto,codex,basic,opencode}`。Basic 的 task 控制、停止、恢复、
回退与 workflow 命令见[使用指南](usage_ZH.md)。Codex `cwd` 只是展示元数据，不选择执行
workspace policy。

终端 net 投影支持 `--format`、`--show-resources`、`--resources-only`、`--node`、
`--output`。浏览器视图支持 `--no-open`、显式 loopback `--host`、`--port 0..65535`、
`--show-resources`、`--max-checkpoints`（默认 2048）和 `--max-firings`（默认 2000）。
对于已知的大型历史 run，可提高后两个值而无需改源码。history HTTP endpoint 每次分页 1–100
个 checkpoint。

宿主 selector 共用同一 viewer：Codex 使用 `--root`、`--thread-id` 和 `--turn` 或
`--task-id`；DSH 使用 `--root`、`--session-id` 和 `--turn` 或 `--request-id`。两者都支持
`--describe`、`--json`、`--view`、`--presentation`、`--no-open`、
`--show-resources`、`--port` 及两个 dashboard 上限。

## 固定兼容与安全边界

以下内容不是用户策略：schema/protocol 版本、Registry identity 形状、socket path 与 HTTP
状态合法性、credential 大小校验、IPC frame、Codex/OpenCode/DSH 版本固定、OpenCode 的
256 KiB 请求与 4 MiB response/snapshot 上限、DSH 的 68 MiB frame 与 16 MiB managed-tool
上限、workspace 命令 1 MiB captured-output contract，以及当前固定的 Codex permission 投影
（`danger-full-access`、`approvalPolicy=never`、user reviewer）。OpenCode
permission/workspace reply API 仍不可用。只读投影在 Registry 读取期间发生推进时还会执行少量
固定的一致性重读；这不是执行重试，也不会修改 Registry。这些值定义经过测试的 wire 或
authority contract；修改源码属于需要兼容测试的实现变更，不是受支持的配置操作。

配置案例见[模型配置](models_ZH.md)，声明/plugin 见[自定义](customization_ZH.md)，证据边界见
[看板](viewer_ZH.md)。

## Codex local-process bridge：声明context与诊断

显式选择registered profile时，将Registry profile metadata的`context_window_tokens`
与bridge参数`--model-context-window`对齐；后者传给CLI配置键`model_context_window`。
只配CLI不会填入Registry context-pressure策略。这些是本地声明容量，不是官方已验证模型上限。

CLI分派前，bridge按最终渲染请求估算
`ceil((prompt_UTF8_bytes + endpoint_instructions_UTF8_bytes + output_schema_file_bytes) / 4) + configured_output_reserve`。
prompt包括渲染后的history与tool schemas。`configured_output_reserve`来自bridge参数
`--model-max-output-tokens`，是canonical输出预留，不是强制CLI输出cap。历史repair条件
使用context 272000、输出预留128000；它们仅属于该次条件，不是默认值或通用公式中的固定常量。
字节估算不是准确tokenizer计数或provider保证。超出声明窗口时以`context_budget_exceeded`
拒绝；unknown submission记账仍保守，不自动授权回放。

unsupported event/item诊断仅在私有stderr审计metadata保留最多64字符的有界ASCII协议类型。
该诊断不保留raw payload、不放宽工具allowlist。Registry failure code不保留这些类型，失败
路径不保证都有request-budget摘要。旧unsupported item不再出现不等于已确定或修复未知根因；
容量拒绝不是context溢出证明。[公开结果](../../examples/automationbench/PUBLIC_RESULTS_20261006_ZH.md)
将配置、canonical usage及provider未知字段分开。
