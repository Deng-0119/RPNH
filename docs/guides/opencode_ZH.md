---
name: rpnh-opencode-frontend
description: "Use the pinned OpenCode TUI as an RPNH presentation frontend."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: opencode.md
  revision: "2026-09-26.2"
  status: locally-validated-integration-candidate
  upstream-version: "1.18.32"
---

# OpenCode 前端

[English](opencode.md)

## 目的与权威边界

执行路径为 `rpnh → OpenCode attach → 回环 HTTP/SSE → 前端中立 application service → 现有 MainSession/TaskControl → Registry/Petri net`。OpenCode 仅作终端展示，不是 RPNH 的执行后端。精确模型选择、provider 调用、资源、workspace、工具、独立任务、Designer 工作流、checkpoint 和终态证据仍由 RPNH 管理；不复制上游执行循环，也不启动 OpenCode server/provider/tool 后端。

这仍是集成候选，不是已发布版本。本地验收已覆盖完整 harness checkout、真实 Registry 路径、源码目录外 wheel、固定版真实 TUI 连接无 provider 的应用替身，以及一次另行授权、经该 TUI 使用 exact model 的真实 turn。结果及内容层警告见[验证记录](examples-validation_ZH.md)。

## 前置条件与入口

支持 Linux/WSL2、Python 3.11+。需要完整 harness 源码、已配置的 RPNH execution profile，以及单独安装的可信 **OpenCode 1.18.32** 客户端。协议基线为 `anomalyco/opencode` 的 `545f51d26cc39a907d2867492d498d9607ea5fa4`（tag `v1.18.32`）。先检查 `opencode --version`。启动器拒绝其它版本字符串，不自动下载安装，不验证二进制来源，也不修改全局 OpenCode 配置。

```bash
python -m pip install .
rpnh --help
rpnh --frontend opencode --session-dir ./my-rpnh-session
# 退出任意 RPNH 前端后，重开同一个 MainSession：
rpnh --frontend opencode --resume ./my-rpnh-session
```

启动前端/catalog 本身不提交问题；提交问题、启动任务或显式恢复可能调用已选 RPNH 模型/工具。本交互前端拒绝 `--prompt`，一次性执行仍使用 `--frontend basic`。basic/Codex 保持委托现有 CLI；不替换已有 runtime、Registry schema、provider 实现或默认前端。

传入路径就是直接的 canonical MainSession root，与 Basic、Codex 接收的路径相同。因此，
前一个前端退出后，其创建的会话可以由另两个前端查看并继续。OpenCode 只为该 root 投影
一个展示 session，不创建 `threads/ses_*` Registry 副本。共享的非阻塞 owner lease 会
拒绝第二个可写前端。协议 `ses_*` ID 与 `.frontends/codex.json` 都只是展示 metadata。

resume、list 与历史投影都是观察操作：不会调用模型，不会提交 terminal main turn，也不会
补偿启动 committed child。从其它前端继承的 terminal turn 会保持可见的 terminal 状态，
直到用户显式执行 `/rpnh-resume`；该明确动作才可以提交主答案并启动已经声明的 child。
缺少 OpenCode request evidence 的历史 turn 仍可见，但其逐 turn 模型证据会标为 unavailable，
不会根据当前默认 profile 或 sidecar 猜测补全。

## 对话、身份与重试

请求身份先由现有 MainThreadRegistry 登记，再交给 TaskControl 启动。HTTP 成功仅可表示输入已登记；成功 assistant 回复必须来自 committed Registry 答案。进程退出码零不是终态证据，子任务启动登记也不是子任务成功。SSE 重连只替换稳定 ID 的当前消息/part 投影，不重试 provider。已有内容但无法确认 owner 的 attempt 会明确进入待核对状态，不盲目重跑。子任务正在创建 Registry 时，已经出现但尚不能精确读取的 SQLite authority 会暂时投影为 `reconciliation_required`；它不会终止前端 owner，也不准入另一条提交。后续 tick 只能依据真实 Registry 证据完成结算。

固定版原生 TUI 并非每次都提供 messageID，因此 **同一会话中，没有显式 ID 的相同文本始终视为同一请求**，即使此前已经完成或中断。主动重复请使用 `/rpnh-send UNIQUE_ID TEXT`，并选择新 ID；响应丢失后的重试必须复用原 ID。HTTP 调用方也可使用 `messageID` 或 `Idempotency-Key`，同时提供时必须一致。这是 v1 可见限制，不声称能够自动推断“网络重试”和“用户再次提问”的区别。

## 控制命令

| 命令 | 对应 RPNH 行为 |
| --- | --- |
| `/rpnh-help` | 查看支持的控制面。 |
| `/rpnh-tasks` | 列出本会话的独立任务/工作流。 |
| `/rpnh-task ID status\|result\|stop\|resume` | 精确访问一个已有子任务 owner；resume 续接其当前停止切面。 |
| `/rpnh-task ID checkpoints` | 列出该 child run 的精确已提交 checkpoint 版本。 |
| `/rpnh-task ID reopen CHECKPOINT [:: REASON]` | 在同一 Registry／run 中把该精确切面重开为新代次，并可附带 owner 指引。 |
| `/rpnh-task ID message TEXT` | 向一个子任务 owner 发送已登记的控制消息。 |
| `/rpnh-task ID net [资源参数]` | 读取该子任务的真实注册网。 |
| `/rpnh-net [--show-resources\|--resources-only]` | 读取最新主回合 attempt 的真实网，复用已有资源过滤。 |
| `/rpnh-agent TEXT` | 启动独立 single-agent Registry/任务。 |
| `/rpnh-workflow TEXT` | 经主会话请求 Designer 工作流。 |
| `/rpnh-resume` | 通过精确 owner 恢复暂停的主回合。 |
| `/rpnh-rollback` | 回滚暂停的主会话，同时保留子 Registry。 |
| `/rpnh-send UNIQUE_ID TEXT` | 使用明确身份提交或重试。 |

使用前缀避免与 OpenCode 原生命令冲突。原生 OpenCode 仍可能展示它自身的命令；这些不是 RPNH 能力，其 effect route 会明确拒绝。命令输出明确标为临时 synthetic observation，不是 agent 答案或新终态证据，前端进程重启后不恢复；权威任务 ID、结果和网仍保留在 RPNH。网输出是 JSON 观察，不是 OpenCode 原生 Petri-net 可视化。

abort 只请求精确的主回合 owner 停止；同一 owner 生命周期内的重复请求会合并。请求被接受不等于 checkpoint 已完成，必须等待 Registry 的 `stopped_by_owner`。owner channel 尚未就绪时明确报错，不发送不安全的早期启动中断。退出 UI 时，所有活跃主回合必须先到达正常的 `stopped_by_owner` checkpoint，application owner 才关闭；独立子 worker 不会因此停止。恢复和回滚不从 OpenCode transcript 重建 checkpoint。task checkpoint reopen 也只使用用户选择的已提交 Registry 切面，不从 OpenCode message 推导状态，也不创建替代任务。

## 模型、资源与禁用能力

所有可选择的用户 RPNH profile 都展示在一个仅用于界面的 `rpnh` provider 下。创建 session
时可选择 profile，也可以在主回合之间切换；活跃回合中禁止切换。OpenCode 来源的 turn
保留其 request selection evidence；从 Registry 投影的其它历史不会被补写从未登记的证据。
未知 profile、凭据未就绪、非 RPNH agent 和 variant 均会被拒绝。在新执行前，以及任何可能
补偿启动的显式动作前检查 RPNH profile 身份。客户端不接收 provider key、凭据变量名、
endpoint、宿主 route 或私有 profile 路径。

本版 permission reply 与附件不可用：没有伪造审批、默认放行、直接 file URL 准入或附件转文本降级。OpenCode 原生 shell、fork、revert、share、summarize、初始化、provider 登录及配置写入均不支持，不会回落到其它执行后端。LSP/MCP/formatter/workspace 空目录仅是兼容投影，不宣称展示了 RPNH 的已注册扩展。

usage、cost 和 context 指标**不可用**。固定 DTO 必填数值使用零作为展示占位，不代表实际零消耗或免费。应查看 RPNH 注册调用计数及执行证据。当前 UI 仍可能把占位数显示为数字，因此尚不能将它视为准确用量统计界面。

## 隔离、容量与错误

启动器解析本机客户端，在临时 HOME/XDG/config/display 目录中检查版本，然后创建带临时认证的 `127.0.0.1` 服务。Basic-auth 临时口令不是模型凭据。客户端不继承 provider keys、全局 OpenCode 配置、插件加载参数、代理和 Python/Node 注入变量；RPNH 既有执行路径保留自己的原始环境。这不是针对恶意客户端二进制的操作系统沙箱。

服务限制为 32 个连接、8 个 SSE 连接、256 KiB 请求体、128 个排队应用请求和 4 MiB 响应/快照。慢客户端合并接收当前投影，不累积无限 delta 队列。大历史可能超出 v1 展示上限并明确报错。诊断只记录有界 route 形状和状态，不保存问题正文、请求头或任意 URL。未知结果、profile 漂移、缺失 owner 和不支持的请求都不能被解释为执行成功。

## 验证与代码定位

```bash
python -m pytest -q tests/test_opencode*.py
# 只能在完整 harness 源码中运行现有全量回归：
python -m pytest -q
# 使用已具备的依赖，在源码目录外构建并安装完整 wheel：
WORK=$(mktemp -d)
python tools/check_opencode_wheel.py --work-dir "$WORK/wheel"
```

可选 PTY 测试需要实际安装固定客户端，连接的是测试用应用替身，不是真实 provider。它要求实际完成 bootstrap route、只读命令和一次 prompt 提交，并在测试临时目录保存首个失败的终端/route 证据；跳过不算通过。wheel 验证器拒绝仅含改动的目录，并使用 no-index/no-deps。

`cpn/rpnh_cli.py` 负责选择入口；`cpn/rpnh/frontend_application.py` 负责中立应用 owner；`cpn/frontend/opencode_protocol.py` 负责固定 DTO 投影；`opencode_http.py` 负责有界 HTTP/SSE；`opencode_launcher.py` 隔离 attach；`opencode_compatibility.v1.json` 记录源码提取的 SDK 子集。不打包上游源码。

fixture 是人工核对的子集，不是上游生成器产物，也不证明已经执行 upstream SDK/TUI。参考源码包括固定版本的 generated SDK types/client、TUI 的 `context/sync.tsx`、`context/project.tsx`、prompt 组件、attach 命令及 TUI/core 配置 flags。变更版本前应重新核对。非站点 `docs/validation/` 记录包含本轮实际结果和剩余验收条件。
