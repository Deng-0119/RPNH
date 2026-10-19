---
name: rpnh-dsh-guide
description: "安装和使用可选 DSH 宿主适配器，同时保持 RPNH 的唯一权威。"
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: dsh.md
  revision: "2026-10-19.1"
  status: source-reviewed-pre-release
---

[English](dsh.md) | [中文](dsh_ZH.md)

# DSH 宿主适配器

DSH 是可选的展示与工具宿主。provider route、exact model、Registry 记录、PetriNet
准入、managed tool grant 和最终结果记账仍由 RPNH 负责。适配器不会维护第二份
provider catalog，也不会成为第二个 Registry writer。

## 准备固定版本宿主

集成目标是 `integrations/dsh/UPSTREAM.json` 声明的精确上游 revision。准备过程需要
Node.js 与 pnpm，并在独立的上游 checkout 中执行：

```bash
RPNH_PYTHON=$(python -c 'import sys; print(sys.executable)')
bash integrations/dsh/prepare.sh "$RPNH_PYTHON" /absolute/path/to/deepseek-harness
bash integrations/dsh/verify.sh /absolute/path/to/deepseek-harness
```

当前宿主为上游 `687f282e8625c6b8d11ff58211a90524be2e4317`
（2026-10-17），root 为 `@deepseek-ai/dsh-root@0.2.1-rc.2`，CLI 为
`@deepseek-ai/dsh@0.2.1-rc.2`，使用 pnpm `11.28.5`。2026-10-19 核对
[官方 npm 元数据](https://registry.npmjs.org/@deepseek-ai%2fdsh)：
`latest=0.2.0-rc.2`、`next=0.2.1-rc.2`、`alpha=0.2.1-alpha.5`。
默认 npm 安装与最新已发布预发布版本因此不同。
[该源码提交](https://github.com/deepseek-ai/deepseek-harness/commit/687f282e8625c6b8d11ff58211a90524be2e4317)
是源码集成目标；仅下载 CLI 包并不提供打补丁后的 factory。本次核对 GitHub 无 latest release。

要求 Node `^22.19.0 || >=24.0.0`（本次验证使用 `22.22.1`）。准备前先在
checkout 内执行 `pnpm install --frozen-lockfile --ignore-scripts`。共享工作区运行
验证时应指定隔离的 HOME、cache 和临时目录。`RPNH_DSH_RESULTS` 可指定验证输出
目录，默认位于上游 checkout 的 `rpnh-results`。验证实际加载上游 AgentLoop factory、
Session、LlmRuntime、ToolRuntime 与 headless runner，响应由本地确定性替身提供。
这是源码宿主验证，不代表 desktop/browser UI 或远程 provider 已验收。

准备脚本核验固定 revision，并应用 RPNH backend 所需的窄 factory seam；它不会把凭据
复制进仓库。

## 离线运行

```bash
rpnh-dsh /absolute/path/to/deepseek-harness \
  --offline --root "$HOME/.rpnh/dsh" \
  --data-file numbers.json \
  --task "Read the numbers and compute their sum"
```

离线模式只验证宿主接线，不调用 provider。

## 使用已配置路线

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

DSH 对完整 frame 施加 68 MiB 限制，其中包括历史、工具声明、请求正文和配置的最大响应。
如果通用 profile 仅响应额度就占满或超过该 frame，系统会在模型 attempt 准入前拒绝它。
用户应显式创建 DSH 专用 profile：保持同一 provider route、凭据和 exact model，只为宿主
声明留有实际余量的响应预算。这是宿主预算，不是 provider 或 model fallback。

新 session 的 module attempt 预算默认是 48。`--attempt-budget` 可指定其他正整数上限；仅当
工作负载协议确实需要时，`--attempt-budget unmetered` 才移除这一项 module-attempt 上限。
所选值会随 run 持久化；resume 使用原声明，因此不接受替换预算。

需要覆盖 provider 可见工具 description 或 input schema 的应用集成，必须同时提供显式 plugin
catalog 和绝对路径 `--managed-bindings` 文档。该文档使用
`rpnh/dsh_managed_bindings/v1` schema：非空 `tools` mapping 中每项严格包含 `selector`、
`description` 和 object `input_schema`，并提供非空且无重复的 `admitted_effects`。DSH 会核验每个
已选已安装 operation 的真实 effect 是否在该列表中。旧的 selector-only
`--managed-tool NAME=PLUGIN/OPERATION` 仍只准入 `pure`，并使用 plugin 自身的 description 与
schema；它不会隐式放开外部 effect。

## 恢复边界

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

## 限制

固定版本适配目前仅支持 Linux/WSL2。离线测试不能证明真实 provider 可用；RPNH wheel
也不包含准备后的上游 checkout。准入或 driver 故障会以明确的非零错误暴露；空 final
response 不代表成功完成。
