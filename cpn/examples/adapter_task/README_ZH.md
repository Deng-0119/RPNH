# 安装版真实适配器任务

[English](README.md)

本包让 Basic、Codex、DSH 和 OpenCode 执行同一个小型语义任务。它不包含 provider、
endpoint、凭据或模型选择；运行宿主前，用户必须自行选择并授权精确的 RPNH execution
profile。

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
