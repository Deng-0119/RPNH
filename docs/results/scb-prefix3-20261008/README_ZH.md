---
name: rpnh-result-scb-prefix3-20261008
description: "SlopCodeBench 适配前 3 点结果"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: zh-CN
  counterpart: README.md
  revision: "2026-10-09.1"
  status: historical-curated-result
---

[English](README.md) | 中文

# SlopCodeBench 适配前 3 点结果

历史 `code_search` 运行仅完成**五个 checkpoint 中的前三个**，属于 adapted development prefix；第四、第五点**未运行**。[完整用例/状态投影](results.json)保留全部原始 evaluator 用例、分类、状态、源码快照身份及 runtime 计数，省略断言文本、solver 源码和模型/工具文本。公开任务已用于开发检查，不是保留评测集。

## 源码与判分器

安装字节对应 RPNH `74fad32d369876841686d10d33361c016e3d3648`：781 个 core payload 与 11 个 SCB payload。历史适配器基点为 `ae09445fe1d9b973502bc5d2c961976c1d2c0163`，发布于 `dbad00458e9b356fcaf0bb97ceb90258ed9b1de0` 不代表重跑。Runner/evaluator 固定为 [slop-code-bench 31ceea3](https://github.com/SprocketLab/slop-code-bench/tree/31ceea3add480edb33431e70475c4c70597e6b31)，题库/任务为 [scb-problems 9cd9ca3](https://github.com/gabeorlanski/scb-problems/tree/9cd9ca3a51c3d3e2a99d2488a25baf73a2204451)，`code_search` 配置 blob 为 `a329353763b33a2d57e9186a957e2a3665333c49`。投影记录三个 checkpoint 的前后快照摘要和连续性；解答 workspace 由上游持有，`native_workspace_reuse=false`。

| Checkpoint | 原始用例 | Core | Functionality | Regression | Error | Evaluator exit | Runtime | 真实调用 |
|---|---|---|---|---|---|---|---|---|
| 1 | **13/13** | 7/7 | 4/4 | 0/0 | 2/2 | 0 | complete | 5 |
| 2 | **25/25** | 5/5 | 5/5 | 13/13 | 2/2 | 0 | complete | 5 |
| 3 | **40/47** | 6/8 | 7/12 | 25/25 | 2/2 | 1 | complete | 14 |
| 4 | 未运行 | — | — | — | — | — | — | — |
| 5 | 未运行 | — | — | — | — | — | — | — |

第三点失败用例为 `optional_metavar`、`multiple_metavars`、`multiline_python_if_blocks`、`language_filtering`、`literal_dollar_sign`、`multiple_files_sorted`、`special_chars_in_captures`。三点均为 `infrastructure_failure=false`。用例包含回归，不能累加为独立任务。`any-case` 下外层 CLI exit 0 不代表全部原始用例通过。15 条容器命令 exit 0 不覆盖 grader 结果；另有两次非法参数动作拒绝，与七项失败未建立单一因果关系。

## 条件与可移植配方

实际模型为 `codex/gpt-5.6-terra`。每点最多 48 次调用、owner 等待 7200 秒。共 24 次真实调用、超限 0 次，耗时 998.822568 秒。上游 cost/net-cost/step cap 为 0，即关闭。规范化任务 token/USD 不可得，不能当作实测零；逐调用 usage 是另一个层次。原 tuple 表示 settled calls/post-limit excess，不是真实/fake 次数。

Solver network=none，每点使用新容器，源码通过快照延续；构建/评分使用 host 网络。同版本下载适配使用 proxy/pipefail/retry，上游 tests/grader 不变。原 evaluator 已执行，官方 `AgentRunner`、完整五点与 quality judge 未执行；没有向 solver 回灌评分、retry 或 resume。

在历史安装源码、精确上游、已准备的 solver 镜像及已获授权私有 selection 下，可移植命令形状为：

```sh
python -m examples.slopcodebench.run   --runner-source "$SCB" --problems-source "$PROBLEMS"   --environment "$SCB/configs/environments/docker-python3.12-uv.yaml"   --template "$SCB/configs/prompts/just-solve.jinja"   --execution "$EXECUTION" --codex-binary "$OFFICIAL_CODEX"   --condition examples/slopcodebench/condition.example.json   --output "$NEW_OUTPUT" --prefix 3 --pass-policy any-case   --acknowledge-development-model-run
```

这只是执行说明，没有新运行，也不是 solver 镜像的完整字节重建。[示例指南](../../../examples/slopcodebench/README_ZH.md)列出准备与隔离要求。原 pytest 省略的输出和未保留 vendor wire 不重构；精确字节回收不解除已有 fresh-reader provenance 失败。不据此证明 harness 因果优势或 V6 验收。


## 较早的合成原生窗口

README 中的 59 项合成测试与原生夹具另属于 `6f8ee2e406f3c70edb73206f861e56a0202b9f15` 加未提交 SCB overlay；其历史适配器基点为 `ae09445fe1d9b973502bc5d2c961976c1d2c0163`。`results.json` 标识 11 个 package 文件、pyproject、相关测试变更和 helper 字节。na01–na04 的原 exit 1 失败保留；na05 subprocess 与 na06 固定 Docker Session 的 complete/stop 四场景为 PASS_FINITE，每个 complete/stop 分别 3/2 次脚本提交，真实 provider 为 0。complete 的 owner exit 0、有 terminal；stop exit 2、无 terminal，分别保留原 stream exit 与静止状态，不把 Docker stop 的 -1 当作制造的进程退出码。

每场景调用上限 8、owner 时限 90 秒、命令 60 秒、native IPC 180 秒。镜像为最小 Python 合成 fixture、network=none，精确 image ID 与 runner/version 身份见投影；它不是一般 SCB base。complete 观察原 Snapshot 与源码延续，stop 不产生 accepted snapshot。完整 CheckpointPilot.run/CLI、timeout 场景、原 grader、官方 AgentRunner 与真实 benchmark 未跑。较早 helper 完整源码未保存，59 项 terse pytest 输出没有用例 ID，不能补造。另行提供投影标识的历史 helper、installed site 和冻结 manifest 后，可按以下参数化配方分别设置 BACKEND 为 subprocess 或 docker；本次文档任务没有执行：

```sh
python -B "$HELPER" --backend "$BACKEND" --site "$INSTALLED_SITE"   --frozen-manifest "$FROZEN_MANIFEST" --output "$NEW_OUTPUT"
```


[分发字节清单](MANIFEST.json)记录本摘要与投影的新字节身份，不能当作原始材料字节。
