---
name: rpnh-configure-models
description: "Configure one canonical catalog, exact identities and bounded transport recovery."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: models.md
  revision: "2026-09-29.2"
  status: source-reviewed-not-final-candidate-acceptance
  basis: "core; adapter differences explicitly labelled"
---

[English](models.md) | [中文](models_ZH.md)

# 供应商与精确模型配置

[配置与上限总表](configuration_ZH.md)完整列出所有用户可调 runtime 字段和固定协议边界；
本页重点说明模型接入。

## 目标和权威
维护一份用户 catalog，由它派生所有可选 profile。provider/model 是不透明的精确用户输入，不是推荐，也不是任意协议兼容承诺。初始 catalog 为空。配置合法、凭据存在和物理调用成功是三件不同的事。

默认文件是 `~/.config/rpnh/provider_models.json`、`~/.config/rpnh/profiles/{adapters,execution}/`、`profiles/profiles.json` 和 `~/.config/rpnh/config.json`。覆盖变量分别有 `RPNH_PROVIDER_CATALOG`、`RPNH_PROFILE_DIR`（指向 **execution** 目录）、`RPNH_CONFIG`、`RPNH_EXECUTION_CONFIG`；CLI `--execution` 可指定执行配置。排障时不要同时改变多个选择来源。

## 不调用模型的配置顺序

```bash
rpnh config init
rpnh config build
rpnh config build --check
rpnh config list
```

空 catalog 没有可用模型，这些命令不证明连通性。编辑 catalog 后重复 build/check/list。使用 `rpnh config use PROFILE` 或 `rpnh config use PROVIDER MODEL` 选择，再用 `rpnh config show` 检查；这些大写单词必须替换为自己 catalog 中的值。

当前 main 包也提供 `rpnh init`、`rpnh config add`、`rpnh doctor --json`。交互配置需要终端，脚本使用 init/build/use；doctor 只是离线检查。

## 完整外部供应商示例
以下是 **schema 示例，不是可调用服务**。取得授权后才替换 `.invalid` 地址和 `EXACT_MODEL_ID`。配置中只写凭据变量名，不写密钥值。

```json
{
  "schema_version": "rpnh/provider_model_catalog/v2",
  "providers": [{
    "provider": "example",
    "display_name": "Example provider",
    "models": [{
      "profile": "example-model",
      "model_condition": "EXACT_MODEL_ID",
      "adapter": {
        "adapter_kind": "external_provider",
        "route_id": "example-route",
        "backend": "example-backend",
        "protocol": "openai_chat_completions/v1",
        "endpoint": "https://provider.example.invalid/v1/chat/completions",
        "credential": {
          "environment": "RPNH_EXAMPLE_API_KEY",
          "header": "Authorization",
          "prefix": "Bearer "
        },
        "headers": {},
        "recovery": {
          "strategy": "bounded_same_route_health_probe/v1",
          "max_probe_attempts": 1,
          "probe_timeout_budget_seconds": 5,
          "max_probe_success_formal_failure_cycles": 1
        }
      },
      "timeout_seconds": 60,
      "max_output_tokens": 1024,
      "max_response_bytes": 1048576,
      "context_window_tokens": 131072,
      "context_compaction_retained_tokens": 16384,
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
    }]
  }]
}
```

`profile` 是适合文件名的小写标识，`model_condition` 原样成为 outbound model。每个可选外部 profile 只有一条精确路由，凭据采用环境变量到 header/prefix 的映射或 `null`。endpoint 不得包含 userinfo/query/fragment；远程路由必须使用 HTTPS，明文 HTTP 仅限 `localhost` 或 loopback IP 上的同机 OpenAI-compatible 服务。静态认证/连接类 header、重复凭据 header 会被拒绝。各整数执行限额为正数。`context_window_tokens` 是用户为精确模型声明的可选容量；提供后，RPNH 会在调用已选路由前主动执行 context-pressure compaction。`context_compaction_retained_tokens` 可选地控制最近完整消息尾部，且必须小于窗口。未提供窗口时，RPNH 不猜测模型容量，只能在实际观察到 response-length 边界后处理。完整的可选 `runtime` 对象控制 task、并发、压缩和 workspace 策略；省略时解析为文档默认值，生成 profile 仍会明确记录。探测次数与恢复循环次数由 schema 限定在一至三。

## 传输、恢复和外部效果
catalog 对外暴露的协议是 `openai_chat_completions/v1`，不是所有宣称兼容的 API。另一种 `local_process` 必须声明 `argv`、`probe_argv`、`env`、`inherit_env`；`{model}` 替换保留所选模型。子进程及其 probe 仍可能调用付费模型，不能把“本地进程”等同“离线”。

build 生成 `external_provider_adapter_config/v2` 或 `local_process_adapter_config/v1`，以及 `llm_execution_selection/v1` 和 `rpnh/provider_profiles/v2` 清单。不要单独编辑派生文件。catalog 或生成器变化后执行 `build --check`。profile/manifest、注册身份/路由身份不一致时应报错，不自动换供应商。

授权执行中发生的健康探测和正式重试都是可能计费的物理调用；recovery 配置本身不是调用许可。授权须覆盖精确路由、模型、超时与调用预算。提交结果未知时，不得直接认定可安全重试，应保留未知效果记录并先核查。

## 验证与恢复
profile 的 `ready: true` 只代表所需变量有值，不能证明 key 正确、路由可达、模型可用或费用情况。密钥不进入 catalog、git 或共享日志。配置漂移时备份唯一 catalog，在受控目录重建，再把 `RPNH_PROFILE_DIR` 指向其 `execution` 子目录。不要静默改写已有运行的路由；切换当前 profile 不迁移历史 run 权威。

代码：`cpn/rpnh/provider_setup.py`、`user_config.py`、`provider_catalog.py`、`onboarding.py`，
以及 `cpn/schemas/runtime/provider_model_catalog.v2.schema.json`。另见
[排障](troubleshooting_ZH.md)和[组件参考](../reference/agents_ZH.md)。
