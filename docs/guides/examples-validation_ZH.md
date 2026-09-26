---
name: rpnh-examples-validation
description: "公开新用户案例的确定性验收记录。"
metadata:
  document-kind: validation-record
  audience: user-and-maintainer
  language: zh-CN
  counterpart: examples-validation.md
  revision: "2026-09-26.1"
  status: deterministic-offline-validated
  basis: "focused local acceptance; no real provider call"
---

[English](examples-validation.md) | [中文](examples-validation_ZH.md)

# 用户案例验证记录

基线是 `main@1c17366`，候选版本为包含本记录的提交。验证环境为 Linux、Python 3.13，
run 目录位于仓库外。脚本化 profile 使用 `local_process`，没有外部 provider 请求。

| 模式 | 状态 | 实际结果 |
|---|---|---|
| 原生加法 | PASS | `2 + 3` 返回 `5`，有终端证据，模型调用为零 |
| 指令资源 | PASS | 返回正文及真实资源/版本身份 |
| 默认混合图 | PASS | 三个 transition；总量 `36`、平均值 `12`、最小值 `9`、最大值 `15` |
| 混合图变体 | PASS | 总量变为 `39`，平均值变为 `13` |
| 独立任务 | PASS | 两套 task/run 身份、独立终端结果和单节点 net |
| 真实模型 | 未运行 | 本任务未授权 provider/model/route 与调用预算 |

Focused 命令：

```bash
python -m pytest -p no:cacheprovider -q tests/test_user_examples.py
python scripts/docs.py check
python -m pytest -p no:cacheprovider -q tests/test_docs_site.py
```

混合 fixture 的每个模型节点执行两次模型边界调用：一次通过 `read_file` 读取精确 Located
input，另一次返回 `write_file` 与 `complete_interaction`。这些是本地确定性子进程流量，
不是真实模型调用；登记的物理调用计数仍然可见，不把 fixture 描述为零执行。

本记录只覆盖案例验收，不代表完整发布套件或真实 provider 结论。生成的 Registry 和原始
adapter audit 没有提交。
