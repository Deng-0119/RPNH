---
name: rpnh-petrinet-viewer
description: "通过只读 PetriNet 看板查看当前和历史 Registry run。"
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: viewer.md
  revision: "2026-09-26.1"
  status: source-reviewed-pre-release
---

[English](viewer.md) | [中文](viewer_ZH.md)

# 只读 PetriNet 看板

看板只投影已有 Registry run，不获取 writer 权限，不执行 transition、不恢复任务、不创建
结果，也不修改 PetriNet。终端投影和浏览器看板读取同一份持久化 run 证据。

## 在终端查看 run

```bash
rpnh net --run /absolute/path/to/run
rpnh net --run /absolute/path/to/run --show-resources
rpnh net --run /absolute/path/to/run --resources-only
```

默认视图隐藏 resource place。`--show-resources` 只增加真实 net 已声明的 resource node；
`--resources-only` 只保留这些资源及其相关连接。

## 打开看板

```bash
rpnh net --run /absolute/path/to/run --view
rpnh net --run /absolute/path/to/run --view --no-open
rpnh net --run /absolute/path/to/run --view --no-open \
  --max-checkpoints 4096 --max-firings 5000
```

第二种形式只输出本地地址，不自动打开浏览器。两个可选正数上限默认分别为 2048 个 canonical
checkpoint 和 2000 条 firing；查看已知大型 run 时直接提高它们，不需要改源码。`--host` 只
接受显式 loopback IP，`--port` 范围为 0–65535。从 main、Codex 或 DSH run root 中选择时，
使用已安装宿主适配器提供的 selector；被选中的 run 始终是唯一事实来源。

## 视图与证据

Overview 突出 agent 关系；Detailed flow 与完整 PetriNet 保留 place、transition、arc、
resource 和执行状态。只有 Registry terminal evidence 与 final-result index 均支持时才显示
完成；进程停止本身不等于 run 完成。

捆绑 JavaScript 库由 lockfile 固定的本地软件包构建。看板不加载 CDN 脚本，也不调用
provider。

## 查看历史 workflow

把历史 workflow 的 run 目录传给 `--run`。主会话 Registry 可以保存子 workflow Registry
的链接，但各子 Registry 保持独立；查看或回退主会话不会删除子 Registry。

Codex selector 使用 `--root`、`--thread-id` 和 `--turn` 或 `--task-id`；DSH selector
使用 `--root`、`--session-id` 和 `--turn` 或 `--request-id`。它们的 `--view` 也支持
`--presentation`、`--show-resources`、`--no-open`、`--port`、
`--max-checkpoints` 与 `--max-firings`；`--describe`、`--json` 不启动 HTTP。
