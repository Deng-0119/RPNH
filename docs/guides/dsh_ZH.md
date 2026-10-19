---
name: rpnh-dsh-guide
description: "使用绑定 RPNH 主会话的官方 DSH Web，或高级旧版宿主。"
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: dsh.md
  revision: "2026-10-19.3"
  status: source-reviewed-pre-release
---

[English](dsh.md) | [中文](dsh_ZH.md)

# 官方 DSH Web 与旧版宿主

用 `rpnh --frontend dsh` 打开官方 DSH Web，与 Basic、Codex 和 OpenCode 共用同一个
RPNH 主会话。先通过常规 RPNH 设置配置模型，并按下文准备固定版本 DSH checkout，
再保存前端偏好：

```bash
rpnh config frontend dsh --dsh-source /absolute/path/to/deepseek-harness
rpnh config frontend
rpnh --session-dir ./main-session
```

仅本次启动时，可对全新目录使用 `rpnh --frontend dsh --session-dir ./main-session`。
`DSH_SOURCE` 优先于保存的源码路径。launcher 打印 loopback URL 并尝试打开浏览器；
未能自动打开时，手动访问该 URL。在 launcher 终端按 `Ctrl+C` 退出宿主，只请求对其
持有的 main turn 进行 checkpoint stop；独立子任务保留自己的生命周期。

## 前端偏好与模型设置独立

`rpnh config frontend [auto|basic|codex|opencode|dsh] [--dsh-source CHECKOUT]`
查看或保存展示偏好，不解析模型，也不改变模型选择。偏好文件位于所选模型配置旁，路径为
`config_path().with_name(config_path().name + ".frontend.json")`；例如 `config.json`
对应 `config.json.frontend.json`。源码路径保存为绝对路径；该配置命令不准备或验证 checkout。

显式 `--frontend` 优先于保存的偏好；未设置时默认为 `auto`。`auto` 仍在交互终端选择
兼容 Codex，否则使用 Basic，不自动选择 DSH。保存 `auto` 可恢复该行为。一次性
`--prompt` 使用 Basic；显式 `--frontend dsh` 与 `--prompt` 组合在解析模型前拒绝。

## 共享会话与控制

用 `--session-dir ROOT` 创建会话；当前前端退出后，以
`rpnh --frontend dsh --resume ROOT` 或另一个受支持前端重开完全相同的 root。
共享 owner lease 仅允许一个可写 owner，Registry 历史仍是权威。Web 的 session 与
workspace 列表仅展示 launcher 绑定的 MainSession 及其上下文，不提供任意 DSH 会话或
可编辑 workspace。

重新查看历史不会执行或继续暂停 turn。用原生 Stop 控制请求 checkpoint stop，再从原生
slash 菜单选择 `/rpnh-resume` 继续，或 `/rpnh-rollback` 回退主对话到上一个已完成 turn。
`/rpnh-help` 列出共享 RPNH 任务控制，包括 `/rpnh-tasks`、`/rpnh-task`、`/rpnh-net`、
`/rpnh-agent` 和 `/rpnh-workflow`；请使用菜单及其参数输入，不把命令作为普通文本提交。
启动或恢复工作可能经 Python 调用所选模型与工具。原生模型与 reasoning effort 选择器
展示共享 RPNH catalog。

## Web 界面的实际范围

launcher 通过 DSH 原生 Web transport 提供官方 `index.html` 和 client plugins。
Typed Remote BFF 将官方浏览器契约接入 Python 共享 MainSession；生成的绑定代码打开该
会话，不修改官方 UI。这替代了 `e9fa433` 已发布的组件拼装 Web 阶段。

支持文本输入、原生模型／effort 选择，以及原生 slash 菜单中的 RPNH 控制。只支持绑定的
MainSession；不支持原生文件上传、任意创建会话、workspace 编辑或 DSH 原生本机工具执行。
官方 client plugin 的存在不代表已开放相应后端能力。不加载 stock DSH AgentLoop、LLM、
工具和 persistence runtime；执行、Registry 历史、workspace 与恢复仍由 Python RPNH
负责。该固定上游没有随包终端 TUI。

[DSH 统一前端验证记录](../validation/dsh-unified-20261019_ZH.md)区分此前组件阶段与官方
Web 检查。官方 Web 验收以其独立结果为准；组件阶段不能证明本路径、真实 provider、
全部 stock DSH 能力、全平台或 wheel 已验收。

## 准备固定版本源码

使用 `integrations/dsh/UPSTREAM.json` 固定的独立可写 checkout：上游
`687f282e8625c6b8d11ff58211a90524be2e4317`（2026-10-17），root 为
`@deepseek-ai/dsh-root@0.2.1-rc.2`，CLI 为 `@deepseek-ai/dsh@0.2.1-rc.2`。
要求 Node `^22.19.0 || >=24.0.0`（此前宿主检查使用 `22.22.1`）与 pnpm `11.28.5`。
启动前先在 checkout 中执行 `pnpm install --frozen-lockfile --ignore-scripts` 安装依赖。

首次启动从 npm 下载精确 `0.2.1-rc.2` 的 31 个官方包，缓存到
`SOURCE/rpnh-frontend/node_modules`。完整缓存可无网络复用；缺失或不完整的包需要下载。
准备和资源预检均在获取 MainSession 写入权之前完成，因此下载或预检失败不会取得
会话 owner。

Setup 生成 `rpnh-frontend/official.json`、`tsconfig.json`、typed Remote descriptor 副本
和 `generated/bound-client`，将官方 UI 接入绑定会话；不重建或修改官方 Web UI，不修补
旧版 AgentLoop factory，也不启动模型调用。后续启动需继续保留可写 checkout 和缓存。
RPNH wheel 不包含准备后的 checkout 或下载的官方包。

2026-10-19 核对的[官方 npm 元数据](https://registry.npmjs.org/@deepseek-ai%2fdsh)为
`latest=0.2.0-rc.2`、`next=0.2.1-rc.2`、`alpha=0.2.1-alpha.5`，当时默认 npm 安装与
最新已发布预发布版本不同。这里使用精确的
[源码提交](https://github.com/deepseek-ai/deepseek-harness/commit/687f282e8625c6b8d11ff58211a90524be2e4317)
和固定 Web 包，不使用默认 CLI 安装；该次核对时 GitHub 无 latest release。

## 旧版 headless host 准备

以下可选检查仅适用于 `rpnh-dsh`，不验证官方 Web：

```bash
RPNH_PYTHON=$(python -c 'import sys; print(sys.executable)')
bash integrations/dsh/prepare.sh "$RPNH_PYTHON" /absolute/path/to/deepseek-harness
bash integrations/dsh/verify.sh /absolute/path/to/deepseek-harness
```

旧版 prepare 核验固定 revision 并应用窄 AgentLoop factory seam；仅 CLI 包不提供该
patched factory。它不会把凭据复制进仓库。共享工作区运行验证时应指定隔离的 HOME、
cache 和临时目录。`RPNH_DSH_RESULTS` 可指定输出目录，默认是上游 checkout 中的
`rpnh-results`。验证加载真实上游 AgentLoop factory、Session、LlmRuntime、ToolRuntime
及 headless runner，响应由本地确定性替身提供。这是源码宿主检查，不是官方 Web 或
真实 provider 验收。

## 旧版 registered headless host：离线运行

`rpnh-dsh` 保留为高级 registered headless host，使用独立的 `--root`、`--session-id`
语义；它不打开统一 Web 前端，也不使用直接 MainSession root。以下 frame、预算和恢复
细节仅适用于该旧版宿主。

```bash
rpnh-dsh /absolute/path/to/deepseek-harness \
  --offline --root "$HOME/.rpnh/dsh" \
  --data-file numbers.json \
  --task "Read the numbers and compute their sum"
```

离线模式只验证宿主接线，不调用 provider。

## 旧版宿主：使用已配置路线

先通过 `rpnh config build` 构建用户自有 provider profile，再传入 execution selection
的绝对路径：

```bash
rpnh-dsh /absolute/path/to/deepseek-harness \
  --execution /absolute/path/to/selection.json \
  --root "$HOME/.rpnh/dsh" \
  --task "Reply with READY."
```

该命令可能产生真实模型调用。使用前必须记录选中的 profile、provider、exact model 和调用
预算。RPNH 不会静默切换路线或模型。

旧版宿主对完整 frame 施加 68 MiB 限制，其中包括历史、工具声明、请求正文和配置的最大响应。
如果通用 profile 仅响应额度就占满或超过该 frame，系统会在模型 attempt 准入前拒绝它。
用户应显式创建 DSH 专用 profile：保持同一 provider route、凭据和 exact model，只为宿主
声明留有实际余量的响应预算。这是宿主预算，不是 provider 或 model fallback。

旧版宿主新 session 的 module attempt 预算默认是 48。`--attempt-budget` 可指定其他正整数上限；仅当
工作负载协议确实需要时，`--attempt-budget unmetered` 才移除这一项 module-attempt 上限。
所选值会随 run 持久化；resume 使用原声明，因此不接受替换预算。

需要覆盖 provider 可见工具 description 或 input schema 的应用集成，必须同时提供显式 plugin
catalog 和绝对路径 `--managed-bindings` 文档。该文档使用
`rpnh/dsh_managed_bindings/v1` schema：非空 `tools` mapping 中每项严格包含 `selector`、
`description` 和 object `input_schema`，并提供非空且无重复的 `admitted_effects`。DSH 会核验每个
已选已安装 operation 的真实 effect 是否在该列表中。旧的 selector-only
`--managed-tool NAME=PLUGIN/OPERATION` 仍只准入 `pure`，并使用 plugin 自身的 description 与
schema；它不会隐式放开外部 effect。

## 旧版宿主：恢复边界

当前会话使用上游 V4 独立 tool 消息。codec 保留旧 V3 与此前已检查 V4 revision
的只读解码。执行及恢复要求原始精确 revision；升级宿主不会迁移、改写版本标识或
重开旧会话执行。旧会话须保留原受支持宿主。workspace 与 checkpoint 的唯一权威
仍为 RPNH。

宿主可以重新连接持久化的 RPNH 状态，但不能自行判断未确认的物理 provider 提交是否完成。
除非共享 provider adapter 能够 reconciliation，否则该 attempt 保持
`submission_unknown`。managed tool 结果与其他宿主一样，通过统一 Registry 与 operation
契约结算。

如果进程丢失前精确登记输出及其 operation completion 已持久化，共享恢复可以不重放 host、
provider 或 tool 而完成结算。绑定 workspace 的恢复还要求 `map_ready` 下级执行 checkpoint
持有精确不可变 candidate；声明 HOST effects 的 outcome 仍被排除。这是 Registry 证据恢复，
不是 DSH 专用重试路径。

## 平台与验证限制

两种启动路径目前均只支持 Linux/WSL2。离线测试不能证明真实 provider 可用；RPNH wheel
也不包含准备后的上游 checkout。准入或 driver 故障会以明确的非零错误暴露；空 final
response 不代表成功完成。
