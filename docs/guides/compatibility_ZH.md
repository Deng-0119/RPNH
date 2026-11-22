---
name: rpnh-compatibility
description: "兼容性与已知限制"
metadata:
  document-kind: guide
  audience: user-and-developer
  language: zh-CN
  counterpart: compatibility.md
  revision: "2026-11-22.1"
  status: maintained
---

[English](compatibility.md) | [中文](compatibility_ZH.md)

# 兼容性与已知限制

RPNH 支持 Linux 和 WSL2，要求 Python 3.11 或更新版本。暂不支持原生 Windows
和 macOS。可选客户端需要匹配精确版本。

## 前端版本

| 集成 | 选择的版本 | 支持范围 |
|---|---|---|
| Basic | 随 RPNH 提供 | 终端会话与任务控制，无需外部 UI 客户端 |
| Codex | 默认 0.163.0 | 通过 RPNH app-server 适配器提供文本、模型选择和已提交会话历史 |
| Codex 候选 | 0.166.0，显式 Python 选择 | 指定版本的协议候选，见[选择方式与限制](codex-candidate-0166_ZH.md) |
| Codex 候选 | 0.171.0，显式选择 `candidate-0.171.0` | 2026-11-22 已通过 WSL2 真实 batch-summary-v1 与同根非空重开；精确模型和调用范围见下文 |
| OpenCode | 默认 1.19.4 | 通过 RPNH HTTP/SSE 适配器连接 attach 客户端，见[配置指南](opencode_ZH.md) |
| OpenCode 候选 | 2.2.5，显式协议选择 | 仅 REST／认证及非持久连接与状态事件，不含完整原生前端或持久消息流 |
| OpenCode 候选 | 2.2.6，既有显式选择 | 绑定单一普通文本会话、profile 选择、历史和 checkpoint 控制；不含原生工具执行或完整客户端认证 |
| 集成 DSH Web | 默认 0.2.2-alpha.2；显式候选 0.2.2-rc.1／0.2.2-rc.5 | `rpnh --frontend dsh`；绑定单一 MainSession，支持文本、模型／effort 选择及 RPNH 控制 |
| Stock DSH operator 插件 | DSH 0.2.2-alpha.2 | 单独安装 operator 插件及配套 Python 包，采用手动安装 |

Codex 0.171.0 可通过 `rpnh frontend setup codex --codex-bin /absolute/path/to/stock/codex --codex-compatibility-profile candidate-0.171.0` 显式选择。
以下 2026-11-22 结果属于此前前端验收源码，不是对本次 RC8 候选的重跑或安装验收。
此前原生 fresh／reopen 零模型检查已通过；独立 wheel 的实际 `frontend setup`
与 `frontend list` 也读到 `available=true`。另于 2026-11-22 在 WSL2 使用
`gpt-5.6-terra`／`high`，经 RPNH official Codex subscription CLI 路线完成真实
`batch-summary-v1`：一次原生输入、两次 Registry 已结算调用，数量／总和／均值／最小／最大
正确为 `3/36/12/9/15`；同根非空重开回读原答，无新增调用。该结果仅覆盖此精确路线和任务。
DSH Web alpha.2 使用 32 个官方 Web 包，两个 rc profile 各使用 33 个；rc.1 是既有 opt-in。
2026-11-22，rc.5 已在 WSL2 通过 frozen 安装、原生 BFF／投影回归 17/17、零 skip
（含三个真实 Chrome 浏览器用例，使用脚本化后端），以及真实 Python Owner 与 Web
同根空会话冷重开。上述脚本化／零模型检查与首次已授权真实提交不同：驱动误把
`running` 加 `reconciliation_required` 当作终态，主动关闭 Owner；结果为
`stopped_by_owner`、零次已结算调用、`submission_unknown`，没有已登记答案；该首次未知提交
单独保留为历史证据。修复后，另行授权、使用新输入集的独立真实任务在源码 `b65f6682`
通过真实浏览器完成，模型为 `gpt-5.6-terra`／`high`：一次输入、两次已结算调用，
本次用时 81.464 秒，数量／总和／均值／最小／最大正确为 `3/45/15/10/21`，语义 verifier
退出码为 0。同根只读重开在 4.486 秒通过，零次原生提交；浏览器会话、Registry 引用及计数
均未改变，实际 DOM 原回答可读。该精确真实业务结果与脚本化后端测试分开记录。
精确工具、选择方法及独立的旧版 0.2.1-rc.2 host 见 [DSH 指南](dsh_ZH.md)。

两种 DSH 集成的安装路径不同，请分别参阅[集成 Web 指南](dsh_ZH.md)或
[stock operator 指南](../../integrations/dsh/operator/README_ZH.md)。Basic、Codex
和 OpenCode 使用 RPNH 管理的会话、provider、工具、Registry 与恢复机制。
前端版本不会授予额外执行权限。

Codex 兼容表还为旧调用方保留显式 `pinned-0.155.0` 和 `candidate-0.161.0`
profile，均不是当前默认值。客户端发现响应不表示已安装插件、MCP 服务、记忆、
附件或任意进程执行能力；不支持的操作会被拒绝。

## 源码与发布包

[RPNH v0.1.0rc8](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc8) 是 GitHub 预发行；[`rpnh-bootstrap@0.1.0-rc.8`](https://www.npmjs.com/package/rpnh-bootstrap/v/0.1.0-rc.8) 已发布到 npm。在 WSL2 x86_64／CPython 3.13.12 的 Basic 范围内，GitHub 和 npm 两条匿名路径均通过安装、激活和版本回读。见[发行范围](releases_ZH.md)。10 月 30 日发布的
[v0.1.0rc3](https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3)
提供由 `bc7755d0a0111a0045c86ed4508b11e1cb4a38bc` 构建的 Python wheel 和
stock DSH operator tarball。`main` 后续修改不包含在这些已上传文件中。
要使用后续修复，请安装选定的源码版本；更新 `main` 不会自动升级已有安装。

后续源码修复包括 workspace 调用关联、DSH 桥接取消、Codex 重开绑定 thread，
以及 OpenCode 消息冲突／死锁处理。默认客户端版本保持不变。Stock 插件尚无经
确认的自动升级或跨版本回滚路径。安装选择见[版本与发行说明](releases_ZH.md)。

## 运行限制

- 前端重连恢复会话视图，不会自动恢复已停止的业务任务；请使用[检查点控制](checkpoint-recovery_ZH.md)。
- 任务停止、外部效果未知与业务完成是不同状态。重开检查点不会撤销外部效果。
- 集成 DSH Web 只支持绑定会话，不支持原生上传、任意会话、workspace 编辑或 stock DSH 工具执行。
- Codex 0.166.0／0.171.0 与 OpenCode 2.2.5／2.2.6 仍为可选候选，不承诺完整客户端兼容。
  [OpenCode 候选参考](../reference/opencode-v2-candidate_ZH.md)列明支持的子集。
- Provider 可用性取决于用户配置；离线测试与示例成绩不能证明其他 provider、模型、
  操作系统或客户端版本可用。

配置与恢复问题见[故障排查](troubleshooting_ZH.md)。
