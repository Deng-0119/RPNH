---
name: rpnh-examples-validation
description: "公开新用户案例的确定性与授权真实调用验收记录。"
metadata:
  document-kind: validation-record
  audience: user-and-maintainer
  language: zh-CN
  counterpart: examples-validation.md
  revision: "2026-09-26.3"
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
