# 安装版真实适配器任务

[English](README.md)

本包让 Basic、Codex、DSH 和 OpenCode 执行同一个小型语义任务。它不包含 provider、
endpoint、凭据或模型选择；运行宿主前，用户必须自行选择并授权精确的 RPNH execution
profile。

## 实际验收 Registry 视图

| Basic | Codex 0.155.0 |
|---|---|
| ![Basic 验收运行](assets/basic-petrinet.png) | ![Codex 验收运行](assets/codex-petrinet.png) |

| 固定版本 DSH | OpenCode 1.18.32 |
|---|---|
| ![DSH 验收运行](assets/dsh-petrinet.png) | ![OpenCode 验收运行](assets/opencode-petrinet.png) |

这些图片是各展示入口背后的实际已结算 PetriNet，并移除了 run 专属 checkpoint 身份。
它们不是 TUI 截图，也不能替代每个宿主的 Registry 与 provider audit 核对。

从任意已安装的 `rpnh-harness` 发行包导出一份新副本：

```bash
EXAMPLE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-example-parent.XXXXXX")/adapter-task"
rpnh examples export --output "$EXAMPLE_ROOT"
cd "$EXAMPLE_ROOT"
```

每次只按一个宿主目录的说明操作，并为每次执行使用新的运行根目录。只把 assistant 返回的
JSON 对象复制到 `answer.json`，然后在本地验证：

```bash
rpnh examples verify --result answer.json
```

验证器只检查任务语义。Registry 终态证据和 provider-private 物理调用记录仍是执行权威；
不能只凭 TUI 文本或退出码判定成功。生成的 run 和原始模型记录属于私有运行数据，不能提交。

每个宿主目录还分别包含 2026-09-26 验收运行的脱敏 `evidence.json`。摘要将 Registry 权威、
语义验证和物理响应计数分开记录，不包含 run ID、本地路径、endpoint、凭据或原始 transcript，
也不能替代用户对自己所选路线的验证。

## 修改任务与预期答案

默认验证器始终检查安装版原始任务。修改 `task.txt` 与 `expected.json` 后，须明确指定本地预期文件：

```bash
rpnh examples verify --result answer.json --expected expected.json
```

预期文件须按顺序恰好包含 `count`、`total`、`mean`、`minimum`、`maximum`；值须为有限 JSON 数字，count 为正整数，且汇总算术一致。验证不会执行任务，也不能证明 Registry 结算。升级时导出到新目录；已有目标目录会被拒绝，以保留用户修改。用 `rpnh examples list` 查找其他可复用代码案例，再用 `export --example NAME --output DIR` 获取。
