---
name: rpnh-dsh-guide
description: "安装和使用可选 DSH 宿主适配器，同时保持 RPNH 的唯一权威。"
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: dsh.md
  revision: "2026-09-26.1"
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

DSH 对完整 frame 施加 2 MiB 限制，其中包括历史、工具声明、请求正文和配置的最大响应。
如果通用 profile 仅响应额度就占满或超过该 frame，系统会在模型 attempt 准入前拒绝它。
用户应显式创建 DSH 专用 profile：保持同一 provider route、凭据和 exact model，只为宿主
声明留有实际余量的响应预算。这是宿主预算，不是 provider 或 model fallback。

## 恢复边界

宿主可以重新连接持久化的 RPNH 状态，但不能自行判断未确认的物理 provider 提交是否完成。
除非共享 provider adapter 能够 reconciliation，否则该 attempt 保持
`submission_unknown`。managed tool 结果与其他宿主一样，通过统一 Registry 与 operation
契约结算。

## 限制

固定版本适配目前仅支持 Linux/WSL2。离线测试不能证明真实 provider 可用；RPNH wheel
也不包含准备后的上游 checkout。准入或 driver 故障会以明确的非零错误暴露；空 final
response 不代表成功完成。
