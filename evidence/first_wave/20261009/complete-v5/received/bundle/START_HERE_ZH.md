# RPNH 完整本地实施与验收包 v5

生成日期：2026-10-09 UTC。交给本地 agent 时，只需让其阅读本文件并按 `LOCAL_TASK_ZH.md` 执行，不需要另写长提示词。

## 先看结论

本 ZIP 累计包含 **12 个当前有效候选的原始 ZIP 字节**、V5 原计划 ZIP 与可直接阅读的计划、全部原代码/补丁/已有验证工具/证据，以及本包的任务、环境说明和回传模板。无需再下载或拼接旧附件。

这里的“完整”是交接材料齐全，**不是整个 RPNH 仓库、软件环境或完整 H7 已实现**。本地仍需要既有的完整 `Deng-0119/RPNH` checkout；本包的 `source/`、`replacement/`、`inputs/` 都是冻结证据或筛选子集，不能整目录覆盖仓库。软件依赖仅列要求与来源，不包含 venv、node_modules、模型客户端二进制、运行数据库、私有配置或凭据，不自动下载安装。

- 计划基线 main：`1f191645c4d60c8b190d42e9fad99c85e8981c03`
- 产品基线：`d92ff3704b6002bf5ecbccb3e6a3d1489809a805`
- H1/H2 已在主线，不重套补丁；保留主线两份技术报告
- 核心、RSI、Codex、DSH、OpenCode 各自独立工作树；后继阶段保存新身份
- Codex 0.161 的 1,044-file 精确锁必须保留，不能先混入核心/DSH/OpenCode 改动再关闭检查
- 单阶段通过不能汇总成组合通过；最终组合另立新 identity、gate 与回归结果

## 从接收开始

1. 解压本外层 ZIP 到仓库外的新目录，保持收到的 ZIP 原件不变。
2. 使用已有 Python 3.11+，在本目录运行只读检查：
   `python -B tools/verify_bundle.py`
   可加 `--bundle-zip /绝对路径/RPNH_Complete_Local_Bundle_v5_20261009.zip` 同时核外层 ZIP。它只读文件、计算摘要、检查 CRC/路径/清单，不执行产品、native、联网、安装或写 repo。
3. 阅读 `LOCAL_TASK_ZH.md`、`LANES_AND_PATCH_ORDER_ZH.md`、`ENVIRONMENT_AND_COMMANDS_ZH.md`。V5 原任务书位于 `plan-v5/LOCAL_HANDOFF_ORDER_ZH.md`，历史报告必须结合 V5 后继状态阅读。
4. 在候选应用前预检完整 checkout：
   `python -B tools/preflight_checkout.py --repo /绝对路径/RPNH`
   它仅用本地 Git 只读命令与标准库检查；不会 fetch/reset/apply。若 HEAD 已前进、脏文件或缺 fixture，停止依赖该条件的步骤，保留用户修改并审查，不自动恢复到旧提交。
5. 分别解压 `archives/` 内所需原 ZIP 到新建的、可写的实验目录（仓库之外）。原 H7 runner 会写该解压副本的 evidence；每次用新 run 名，禁止改封存 ZIP 或旧结果。每个候选入口与唯一可应用 patch 精确路径见 `CANDIDATE_INDEX.json`。
6. 按任务书做独立 lane 的静态、D0 与已有 native gate。依赖/工具/权限不足记 `NOT_RUN` 或 `BLOCKED`；不用临时安装、替换 transport 或降低 hash 守卫凑绿。回传用 `templates/`。

## 两类任务，严格分开

A. 可直接接包验证：S1、RSI R1v2→R2、Codex reader+effort→history→0.161、DSH、OpenCode，以及 H7 已实现的 core/history/lowering **有限离线切片**。这些候选也不自动等于 native 认证。

B. H7 后续工程：完整 public inventory 设计待修订；实际 native issuer、双向 peer/receipt、typed target reservation、两类运行 wrapper、child binding/parent completion 仍缺实现。参见 `H7_IMPLEMENTATION_BACKLOG_ZH.md`。这些不是“只差本地实验”，也没有一键全跑脚本。本包不引入未通过独审的新 public-material 设计。

## 实验边界

不调用真实模型 API、不发生费用、不启动 GitHub Actions；不下载安装或更改默认客户端版本。不触及真实用户 Registry/运行库或私有凭据。已有可信原生客户端的 adapter/native 测试只按包内 gate 和本地已批准范围执行，使用合成 fixture，缺工具即 `NOT_RUN`。commit/push/merge、发布、安装、登录、持久访问与新功能工程不由此包自动获权。

若旧原包文字建议安装、使用另一个旧 plan、重做已由后继完成的工作，以本入口的边界和 V5 顺序为准；冻结原文保留作证据，不默默改写。
