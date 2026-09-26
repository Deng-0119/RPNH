---
name: rpnh-examples-validation
description: "公开新用户案例的确定性与授权真实调用验收记录。"
metadata:
  document-kind: validation-record
  audience: user-and-maintainer
  language: zh-CN
  counterpart: examples-validation.md
  revision: "2026-09-26.6"
  status: focused-live-validation-complete
  basis: "focused local acceptance across basic, Codex, DSH and OpenCode hosts"
---

[English](examples-validation.md) | [中文](examples-validation_ZH.md)

# 用户案例验证记录

基线是 `main@6c675cb`，候选版本为包含本记录的提交。验证环境为 Linux、Python 3.13，
run 目录位于仓库外。确定性检查使用 `local_process`。经授权的真实检查显式选择用户 profile
`codex-terra`、catalog provider `codex` 和 exact model `gpt-5.6-terra`；已保存的默认
profile 未被修改。

| 模式 | 状态 | 实际结果 |
|---|---|---|
| 原生加法 | PASS | `2 + 3` 返回 `5`，有终端证据，模型调用为零；声明的 1 KiB 结果上限覆盖完整合法输出域 |
| 指令资源 | PASS | 返回正文及真实资源/版本身份 |
| 确定性混合图 | PASS | 三个 transition；总量 `36`、平均值 `12`、最小值 `9`、最大值 `15` |
| 混合图变体 | PASS | 总量变为 `39`，平均值变为 `13` |
| Basic 主会话 | PASS | 精确返回 `BASIC_TERRA_READY`，具备 Registry 终态证据与 final result |
| Codex 0.155.0 宿主 | PASS | 精确返回 `CODEX_TERRA_READY`，具备 Registry 终态证据与 final result |
| 固定版本 DSH 宿主 | PASS | 同一 adapter route 与 exact model，使用显式 1 MiB DSH 响应预算；一次受管摘要工具调用返回数量 `3`、总量 `36`、平均值 `12`、最小值 `9`、最大值 `15` |
| OpenCode 1.18.32 宿主 | PASS（含内容警告） | 一次独立 replacement turn 经固定版 TUI 到达 Registry 终态证据和 final result。exact model 返回了散文 `Completed.`，而非要求的标记；RPNH 仅将其作为协议无效的散文答案接纳。 |
| 真实混合图 | PASS | 读取／纠正／写入／完成流程到达终态，产出数量 `3`、总量 `36`、平均值 `12`、最小值 `9`、最大值 `15` |
| 独立任务 | PASS | 两套 task/run 身份、独立终端结果和单节点 net |

Focused 命令：

```bash
python -m pytest -p no:cacheprovider -q tests/test_user_examples.py
python -m pytest -p no:cacheprovider -q tests/test_opencode_application_boundary.py tests/test_opencode_cli.py tests/test_opencode_frontend.py tests/test_opencode_registry_integration.py tests/test_opencode_pty.py
python scripts/docs.py check
python -m pytest -p no:cacheprovider -q tests/test_docs_site.py
```

混合 fixture 的每个模型节点执行两次模型边界调用：一次通过 `read_file` 读取精确 Located
input，另一次返回 `write_file` 与 `complete_interaction`。这些是本地确定性子进程流量，
不是真实模型调用；登记的物理调用计数仍然可见，不把 fixture 描述为零执行。

已完成的真实检查共有 27 次物理模型响应成功，没有使用健康短测。首次失败与修正后的独立
replacement run 均保存在仓库外。首个 OpenCode run 的成功响应计数为零，并保持
`submission_unknown`；它既不计作成功，也未被重放。另行授权的 replacement 使用一次新的
物理调用。该 replacement 还暴露并验证了子 Registry 瞬时读取时的前端对账修复；重开同一
前端根目录后，在没有再次调用模型的情况下提交了已有终态结果。

本记录是候选版本的 focused 证据，不是完整发布套件。宿主路径 PASS 表示 transport、
Registry 结算和前端投影完成，并不把 OpenCode 的模型内容警告改写为精确遵循提示。
生成的 Registry、原始 adapter audit、凭据和私有路线细节均未提交。

## 安装版跨宿主语义任务增补

随后从隔离 wheel 候选中导出 `batch-summary-v1`，并通过每个宿主各运行一次。与前面的
ready 标记不同，每个宿主收到同一个语义任务，每个已提交答案都通过了严格的纯 JSON
验证器。

| 宿主 | 逻辑 turn | 成功物理响应 | Probe | 路线／模型切换 | 权威 | 内容 |
|---|---:|---:|---:|---:|---|---|
| Basic | 1 | 2 | 0 | 0 | PASS | PASS |
| Codex 0.155.0 | 1 | 2 | 0 | 0 | PASS | PASS |
| 固定版本 DSH | 1 | 1 | 0 | 0 | PASS | PASS |
| OpenCode 1.18.32 | 1 | 2 | 0 | 0 | PASS | PASS |

本次增补在明确选择的 `local_process` / `gpt-5.6-terra` 路线上产生七次成功正式响应，
与上文较早的 27 次响应批次分开计数。OpenCode 在结算尚未完成时显示了保守的
reconciliation 提示，随后显示精确的已提交 JSON 和正常的“无子任务 decision”注释；任务
没有重新提交。安装版任务包分别携带各宿主的脱敏摘要，其中不包含原始 Registry、标识、
本地路径、endpoint、凭据和 transcript。

增补的 focused 检查：

```bash
python -m pytest -q tests/test_adapter_task_examples.py tests/test_docs_site.py
python -m pytest -q tests/test_config_and_net_cli.py tests/test_dsh_distribution.py
python scripts/docs.py check
python scripts/check_doc_examples.py
python scripts/check_installed_docs.py --python "$WHEEL_VENV/bin/python" --source-root "$SOURCE_ROOT"
```

## 原生网操作真实任务

当前可执行的原生网操作范围还通过了一次经授权的真实 provider 任务。Extract 与 Branch
选择两个 Agent 定义；Instantiate 与 Compose 构建初始图；第一个 Agent 结算
`outputs/seed.txt` 后，全网 Replace 在静止 checkpoint 采用后继网。后继 Agent 既通过
workspace，也通过精确登记资源路径读取继承文件，再登记 `outputs/result.txt`，最终到达
terminal outcome `complete`。

本次选择的路线为 `volcano` / `deepseek-v4-pro`。五次正式物理响应成功，health probe、
路线／模型切换和超限调用均为零。运行还暴露并修复了一项 replacement binding 缺陷：后继网
必须复用本 run 的精确 execution environment/profile，不能重复登记第二套权威。确定性端到端
回归在无网络条件下复现该边界。可复用任务与脱敏真实结果位于 `examples/net_operations/`；
原始 Registry、provider audit、身份、本地路径和 transcript 均未提交。

## 工作流模式与 viewer 案例库

源码案例库随后完成了四个新的确定性 Registry run。这些是 local-process fixture 流量，
不是 provider 测试批次：

| 场景 | Fixture 调用 | Transition | Place | Edge | Registry 终态 |
|---|---:|---:|---:|---:|---|
| 串行 | 3 | 3 | 6 | 15 | PASS |
| 并行 fan-out/join | 4 | 4 | 9 | 26 | PASS |
| 文档流程 | 4 | 4 | 8 | 21 | PASS |
| 六阶段长流程 | 6 | 6 | 12 | 33 | PASS |

每个 Agent 都通过受管工具边界读取精确 Located input，并发布一个登记输出。并行 run 的
join 在两个分支产物均存在后才结算；长流程为 viewer 时间轴教程提供 canonical checkpoint。
脱敏记录位于 `examples/workflow_patterns/validation.json`，原始 run 保持在仓库之外。
