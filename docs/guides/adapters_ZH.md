---
name: rpnh-optional-adapters
description: "Use versioned Codex presentation and the bounded managed DSH integration."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: adapters.md
  revision: "2026-09-29.2"
  status: source-reviewed-v0.1.0rc1
  basis: "core; adapter differences explicitly labelled"
---

[English](adapters.md) | [中文](adapters_ZH.md)

# Codex、DSH 与共存

## Codex 是展示层，不替换权威
固定客户端为 `codex-cli 0.155.0`，升级到新版本不等于通过兼容测试。该固定版本的安装命令：

```bash
npm install -g @openai/codex@0.155.0
codex --version
```

RPNH launcher 使用兼容服务和既有主会话权威。`rpnh --frontend codex` 启动会话，输入后可能执行模型，不是离线 smoke。RPNH 自己的任务控制用 `rpnh --frontend basic`。Codex model picker 是 RPNH 配置投影，不授权改写 Codex 供应商设置或静默换路由。可选 local-process subscription bridge 是独立供应商路径，选择 TUI 不自动选择它。

subscription bridge 为每个 firing 创建临时私有 Codex home 与 runtime 目录。认证状态
保持为对已配置来源的引用，不复制凭据；私有目录使用 `0700`，并随 firing 一起删除。
这能让本地状态在只读父策略下保持可写，但不会绕过 Codex sandbox。Linux owner 与
user namespace 环境仍必须支持所选 Codex sandbox；否则 adapter 会在取得 terminal
模型响应前失败，并保留私有失败审计。

Codex 与 Basic、OpenCode 接收同一个直接 MainSession root。先用一次
`--session-dir ROOT` 创建；当前 owner 退出后，另一个前端才可用 `--resume ROOT` 重开。
Codex 协议 thread ID 与 `.frontends/codex.json` 都是可重建的展示 metadata，Registry
会话历史才是权威。

## 托管 DSH 前提
本节仅适用于包含 `cpn/dsh`、`integrations/dsh` 的源码树或安装发行包。清单固定 `deepseek-ai/deepseek-harness` 上游 `ddefc45fbc7f8e46dd73185e68295696d1297887`（`0.1.6-alpha.2`），历史记录测试 Node 24，engine 为 `^22.19.0 || >=24.0.0`，pnpm 为 `11.7.0`。这些是集成 pin，不是上游最新版本声明。

取得精确上游 checkout，用固定包管理器和 frozen lockfile 安装依赖。使用独立可丢弃 checkout：prepare 会有意修补受检 factory，并复制 TypeScript 集成文件。分发集成时保留 `UPSTREAM.json`、`UPSTREAM_LICENSE`。

安装包含 DSH 的包后，把 `DSH_SOURCE` 指向固定上游：

```bash
: "${DSH_SOURCE:?Set the pinned upstream checkout}"
rpnh-dsh --help
rpnh-dsh "$DSH_SOURCE" --offline --help
```

第一条命令只显示 Python launcher 帮助。第二条会准备上游 checkout 并显示应用帮助，不解析已配置模型，也不启动任务。prepare 检查上游提交、Node 范围和 tsx，但**会写上游源码**。不带 `--offline` 的 `rpnh-dsh "$DSH_SOURCE" --help` 可能先解析已保存 profile，不是与模型配置无关的帮助检查。

优先使用已安装 launcher。需要从源码直接准备时，shell 脚本现在先接收绝对 Python 解释器路径，再接收 checkout：

```bash
: "${DSH_SOURCE:?Set the pinned upstream checkout}"
RPNH_PYTHON=$(python -c 'import sys; print(sys.executable)')
bash integrations/dsh/prepare.sh "$RPNH_PYTHON" "$DSH_SOURCE"
bash integrations/dsh/run.sh "$RPNH_PYTHON" "$DSH_SOURCE" --offline --help
```

该解释器必须属于预期的 RPNH 环境。launcher 会把自身 `sys.executable` 经 runner 传给 owner 进程。`--execution-path`、`--execution-profile`、`--python` 是 launcher 到应用的内部字段，不是第二套用户配置接口。配置执行应通过 `rpnh-dsh`，不要手工拼接这些字段。

## 显式离线一致性运行
以下执行确定性宿主并生成真实私有 Registry 数据，但不调用外部模型。使用全新输出目录：

```bash
: "${DSH_SOURCE:?Set the pinned upstream checkout}"
printf '[1,2,3]\n' > numbers.json
rpnh-dsh "$DSH_SOURCE" \
  --offline --root ./dsh-runs --data-file ./numbers.json \
  --task 'Compute the sum of the supplied numbers.' --json
```

精确离线路由为 `rpnh-offline/deterministic-v1`，暴露 `read_dataset`、`sum_values`，输入必须是有限数值数组。这是演示，不是通用分析 agent。`--json` 输出 upstream headless JSON 事件流，不是单个 JSON 对象。新运行的 `--deny` 可走声明的拒绝路径。离线模式不会回退到已保存 provider。

## 普通配置文本
配置路径已经实现，不再是“仅能处理数字”的待完成项。RPNH selection 的解析顺序为：显式 `--execution`、`RPNH_EXECUTION_CONFIG`、已保存的选择。launcher 不交互初始化、不自动回退离线，也不自动改写默认选择。先通过既有 RPNH 配置入口构建并选择 profile。

```bash
: "${DSH_SOURCE:?Set the pinned upstream checkout}"
: "${EXECUTION:?Set the existing approved execution selection file}"
rpnh-dsh "$DSH_SOURCE" --execution "$EXECUTION" \
  --root ./dsh-configured-runs --task 'Reply with READY.'
```

这是执行命令，可能联系所选 provider，不是零模型 smoke。external-provider 与 local-process selection 使用共享 Python adapter factory、精确路由、凭据、恢复策略和 Registry 记账。DSH 经 `registered_llm/v1` 转换宿主请求／响应 DTO，不另做 provider transport。

配置模式支持普通文本以及显式选择的纯 native-plugin operation。provider 配置不授予
工具。必须同时传入绝对路径的 `--plugin-config` 和一个或多个
`--managed-tool NAME=PLUGIN/OPERATION`；只声明和准入这些精确 operation。不会隐式
发现已安装工具，任意 DSH/MCP 工具仍不可用。配置模式拒绝 `--data-file`，`--offline`
与 `--execution` 互斥。内部 v2 envelope 可以携带空 `data` 数组，不意味着用户提交文本
时还必须提供数值快照。

launcher 传递公开 profile 摘要。配置模式的子 Registry 记录 route/model 归属及配置修订，不记录 endpoint、credential binding 或静态 header 值。这不保证任务文本或响应中没有敏感内容，运行数据仍需私下保存。请求／响应整体 frame 上限为 68 MiB；当前请求、历史和所选最大响应需通过派发前上界检查，仅把响应上限设为 68 MiB 并不能保证外层 frame 装得下。`max_response_bytes` 更大的通用 profile 对 core RPNH 仍然有效，但与 DSH 不兼容；应为同一 provider、credential route 和 exact model 显式构建留有 frame 余量的用户自有 DSH profile，不能静默改写所选 profile。

新 DSH session 的 module attempt 预算默认是 48。launcher 接受正整数
`--attempt-budget`，或显式值 `unmetered`；后者适用于 AutomationBench adapter 这类按已知
协议可能合理超过 48 个模型／工具回合的工作负载。resume 保持已持久化声明，不接受新预算。

Registry turn 准入前的拒绝现在会作为明确的 headless 错误和非零退出返回，不再表现为空
final。它仍未产生 provider 调用，不能记作模型失败。

## 历史与显式恢复
记录返回的 session ID。历史查询使用 `--history --root DIR --session-id ID`，不要求模型选择或凭据，不构造模型／工具效果宿主。已安装 runner 仍需要正常宿主环境，并可能准备 checkout。“Registry 权威只读”不等于没有文件系统或进程操作。

离线恢复使用 `--offline --resume --root DIR --session-id ID`。配置恢复要求 `--execution ORIGINAL_SELECTION --resume --root DIR --session-id ID`，不能静默采用当前默认配置。两种恢复都不接收新 task、data-file 或 deny 参数。

配置模式在模型尝试登记前干净停止已有本地恢复测试。共享 operation 层还会在输出验证后
记录一个精确 completion；如果进程在该事件之后、firing 结算之前停止，resume 会结算该
completion，不再次调用 provider 或工具。缺少该事件时会在 writer epoch 前移前失败闭合。
声明 HOST effects 的 outcome 仍不进入自动结算。绑定 workspace 的 outcome 只有在
completion 引用其精确不可变 workspace candidate，且该 candidate 由 `map_ready` 下级执行
checkpoint 持有时才具备资格；恢复会校验归档，不重新扫描 live tree。空 writer-epoch 间隔
无害，但后续 writer 一旦发布事实，旧 completion 即失效。registration 与不可变恢复材料会在
新 writer 打开前校验。受管工具参数在执行前按 schema 校验，声明结果上限使用共享的
16 MiB `PluginOperation` 上限，
持久登记的插件失败会形成精确关联的错误工具结果。inspector 还会在 worker 启动前确认
声明的最坏结果连同下一模型响应能够装入 frame。
不要删状态、换选择身份或套重试脚本。复用 provider 已配置的有界恢复策略，不等于能够
恢复持久化的中断 firing。原请求使用受管插件时，resume 必须重复同一插件目录与 allowlist。

## 闭环与共存

core、原生插件、Codex compatibility 与 DSH 已位于同一代码树，并共用一套 profile selector、
provider factory、Registry authority 和 registered-host 边界。DSH observation 在 sole owner
完成 registered products、Success 与 terminal evidence 结算前仍是候选。不要让多个 owner
同时写同一 Registry。

离线与 fake-port 测试只能证明 adapter 契约，不能证明真实 provider 可用。CLI 没有
`rpnh --frontend dsh`；请使用 [DSH 指南](dsh_ZH.md)说明的 `rpnh-dsh` launcher。
插件与 observer 契约参阅[扩展参考](../reference/extensions-observation_ZH.md)。
