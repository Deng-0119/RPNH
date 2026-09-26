---
name: rpnh-provider-model-configuration
description: "配置任意用户自有 provider route 与 exact model。"
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: PROVIDER_MODEL_CONFIGURATION.md
  revision: "2026-09-26.1"
  status: source-reviewed-pre-release
---

# Provider 与 model 配置

[English](PROVIDER_MODEL_CONFIGURATION.md)

本页详细说明 provider transport。Agent、workspace、viewer、plugin 及固定兼容上限的
完整清单见[配置总表](guides/configuration_ZH.md)。

## 引导式配置

在终端执行 `rpnh init` 或 `rpnh config setup`，可一次完成连接信息填写、配置生成和默认
模型选择。`rpnh config add` 添加新的命名配置，不覆盖已有同名配置。
`rpnh doctor` 提供离线配置检查，`rpnh doctor --json` 适合脚本读取。上述命令不调用模型。

向导默认请求时限为 900 秒，输出上限 8192 token，响应上限 16 MiB，均可调整。
服务商及模型 ID 由用户填写。API 密钥通过环境变量提供，不写进模型目录。
Ctrl-C 或拒绝最后的保存确认会取消新增配置。已有配置也可以直接在菜单中选择。

## 手动或脚本配置

RPNH 不预置任何 provider 或 model 选择。安装包中 `cpn/config/` 下的 JSON 只是空模板；
实际 catalog 和生成的 profiles 默认属于用户，位于 `~/.config/rpnh/`。

```text
~/.config/rpnh/provider_models.json   # 用户只编辑这份 catalog
                    │
                    └── profiles/
                        ├── adapters/*.json
                        ├── execution/*.json
                        ├── profiles.json
                        └── generated_provider_files.json
```

创建 catalog、编辑、构建并选择一个 profile：

```bash
rpnh config init
${EDITOR:-vi} ~/.config/rpnh/provider_models.json
rpnh config build
rpnh config build --check
rpnh config list
rpnh config use PROFILE
rpnh config show
```

`provider` 和 `model_condition` 是用户控制的不透明标识。RPNH 不维护 provider/model
白名单，也不要求通过某个 validation flag 才显示已经配置的 profile。`profile` 只是用于
文件名和前端选择的安全本地 slug。当精确 pair 只对应一个 profile 时，也可以按 pair 选择：

```bash
rpnh config use 'provider identifier' 'exact model identifier'
```

RPNH 仍保持 exact-model 权威：生成的 outbound model 与 `model_condition` 完全一致；调用
失败时不会静默切换 provider、route 或 model。

## OpenAI-compatible HTTP transport

内置 HTTP adapter 可连接任意提供 OpenAI-compatible chat-completions endpoint 的
provider 和 exact model。远程 endpoint 必须使用 HTTPS；明文 HTTP 仅允许连接同机
loopback 服务：

下例数值用于展示高容量 profile，不是另一组默认值；交互默认值以上文为准。

```json
{
  "schema_version": "rpnh/provider_model_catalog/v2",
  "providers": [
    {
      "provider": "provider identifier",
      "display_name": "My provider",
      "models": [
        {
          "profile": "my-model",
          "model_condition": "exact/model-identifier@version",
          "adapter": {
            "adapter_kind": "external_provider",
            "route_id": "primary",
            "backend": "user-defined backend",
            "protocol": "openai_chat_completions/v1",
            "endpoint": "https://api.example.invalid/v1/chat/completions",
            "credential": {
              "environment": "MY_PROVIDER_API_KEY",
              "header": "Authorization",
              "prefix": "Bearer "
            },
            "recovery": {
              "strategy": "bounded_same_route_health_probe/v1",
              "max_probe_attempts": 3,
              "probe_timeout_budget_seconds": 300,
              "max_probe_success_formal_failure_cycles": 3
            },
            "headers": {}
          },
          "timeout_seconds": 900,
          "max_output_tokens": 32768,
          "max_response_bytes": 16777216,
          "context_window_tokens": 262144,
          "context_compaction_retained_tokens": 32768
        }
      ]
    }
  ]
}
```

endpoint 不能包含凭据、query 或 fragment。除 hostname 为 `localhost` 或 loopback IP
地址外必须使用 HTTPS；远程明文 HTTP 会在配置校验时失败。凭据处理是通用映射：把一个
环境变量映射到 provider 要求的 HTTP header 和可选 prefix。无需凭据时使用
`"credential": null`。secret value 不会写入 catalog、生成的 profile、用户选择、日志或
公开 Registry policy。

`context_window_tokens` 是可选字段；填写时必须是所配置精确模型的真实容量。该值会写入
execution target，并由 `rpnh config show` 展示，从而在当前 90% pressure 边界主动执行
compaction；如果输出 token 预留要求更早压缩，则采用更早边界。
`context_compaction_retained_tokens` 可选地约束最近完整消息尾部，且必须小于声明的窗口。
省略窗口时 RPNH 不猜测 provider 专属容量，而是禁用主动 pressure 判定。压缩后完整历史
turn 仍保存在 Registry 中。

同一 model 项可增加完整的可选 `runtime` 对象，用于配置每 node 回合预算、workflow
并发、main history prompt 尾部、context pressure/reduction 与 workspace 资源上限。
省略时 build 会解析为文档默认值并写入生成 execution profile。全部字段和默认值见配置
总表；不要手工修改生成 profile。

非秘密静态 header 可写入 `headers`，但不能覆盖 credential header，也不能覆盖
`Content-Type`、`Host`、`Content-Length` 等 transport-owned header。

## 有界同 route 恢复

每个 external-provider adapter 都必须声明当前 `recovery` 策略。上例是推荐基线：正式
provider 或 transport 调用中断后，RPNH 在一个共享的 300 秒短测预算内最多提交三次最小
健康短测。短测使用完全相同的 provider、backend、endpoint、credential route 和 exact
model，固定提示为 `Reply with READY.`，输出上限为 8 tokens。第一次得到可解析的 2xx
响应后立即停止短测序列，并允许使用新的物理调用身份重新提交原正式 prompt。

如果正式重试仍然中断，adapter 可以开始下一轮有界短测；
`max_probe_success_formal_failure_cycles` 把这种循环限制为最多三轮。短测次数、共享短测
预算或循环轮数任一耗尽后，adapter 向 AgentLoop 返回一次分类失败。这里没有 sleep loop，
也不会切换 provider、route 或 model。

健康短测是 provider-private 的物理调用。每次短测和正式重试都在 append-only 私有 adapter
audit 中拥有不同的 request/attempt identity。短测不创建 AgentLoop turn、Registry model
success、workflow operation 或实验结果。现有 external-provider catalog 必须加入必填的
`recovery` 对象并运行 `rpnh config build`；当前 schema 不会静默读取旧结构。

## 本地进程 transport

任何 provider/model 也可以由本地可执行程序包装：

```json
{
  "profile": "my-local-model",
  "model_condition": "exact local model",
  "adapter": {
    "adapter_kind": "local_process",
    "argv": ["my-llm-command", "--model", "{model}"],
    "probe_argv": ["my-llm-command", "--version"],
    "env": {},
    "inherit_env": []
  },
  "timeout_seconds": 900,
  "max_output_tokens": 32768,
  "max_response_bytes": 16777216,
  "context_window_tokens": 262144,
  "context_compaction_retained_tokens": 32768
}
```

构建器会把 `{model}` 替换为精确 model 标识；local-process adapter 支持的运行时占位符
仍然可用。每个 local-process 请求都把目标 Registry run root 作为当前工作目录，因此
bridge 临时数据留在用户自有 run 内，不要求已安装的源码树或 `/tmp` 可写。

## 自定义位置

可用 `RPNH_PROVIDER_CATALOG`、`RPNH_PROFILE_DIR` 和 `RPNH_CONFIG` 分别移动 catalog、
生成的 execution 目录和 active-profile 文件，也可直接传绝对路径：

```bash
rpnh config init --catalog /absolute/path/provider_models.json
rpnh config build \
  --catalog /absolute/path/provider_models.json \
  --output-root /absolute/path/generated-profiles
export RPNH_PROFILE_DIR=/absolute/path/generated-profiles/execution
```

生成索引只允许后续构建删除同一构建器先前生成而现在已过期的文件，不会删除无关文件。

## 操作者验证

RPNH 不替新用户决定应使用哪个 provider 或 model。route 测试由操作者负责：先构建和
检查，再在确有需要且单独授权时进行一次有界真实调用。所有已声明的 credential 环境
变量存在时，profile 会显示为 `ready`；这只表示配置就绪，不代表 provider 质量或网络
可达性已经得到保证。

## 检查配置

`rpnh doctor` 检查配置文件、必需的密钥变量、本地可执行文件和生成文件一致性；
不运行适配器命令、不导入其 Python 模块，也不验证密钥有效性或连接服务商。
本地包装器须从 stdin 接收 RPNH 请求 JSON，并向 stdout 输出
`llm_response_envelope/v1` 响应。普通交互聊天 CLI 不能直接当作该包装器。
请求头的高级设置仍可通过上述模型目录配置。不要将密钥放进 URL、模型名称、
命令参数或固定环境值；使用密钥环境变量映射。
