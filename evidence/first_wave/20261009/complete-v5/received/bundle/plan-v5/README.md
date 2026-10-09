# RPNH 持续优化实施计划 v5

更新：2026-10-09 UTC。此目录是新的独立计划版本，不覆盖已交付 v4，也不改公开技术报告。内容为实施任务与静态接包核查，没有执行产品测试、native、安装或远端写入。

## 直接从这里开始

1. 读 [实施计划](RPNH_HARNESS_OPTIMIZATION_PLAN_20261008.md)：当前能力、下一包、云端与真实环境分工。
2. 读 [接包任务书](LOCAL_HANDOFF_ORDER_ZH.md)：直接交给实施者使用，无需再补一段提示词。
3. 组合候选前读 [冲突与 hash 锁核查](PATCH_CONFLICT_AUDIT_ZH.md)。不能把多个包的 `source/` 覆盖到同一仓库。
4. lowering 的唯一动态状态入口为 [LOWERING_STATUS.md](LOWERING_STATUS.md)。它没有达到这里记载的最终门槛前，停止该增量的应用与后续冻结。

## 当前抓手

- 保留 main `1f191645c4d60c8b190d42e9fad99c85e8981c03`，产品 `d92ff3704b6002bf5ecbccb3e6a3d1489809a805`。H1/H2a 已在主线，不重套。
- 核心链：main → S1 → H7 core → H7 acceptance history → lowering。最后一项以独立状态页为准。
- RSI、Codex、DSH、OpenCode 使用各自独立工作树和冻结阶段。最终组合另立源码身份与影响回归。
- H7 下一项可继续离线推进的是原 Registry 下的公开 execution-material inventory 契约。native issuer、peer/receipt、reservation、Popen、两类 wrapper 与父端终态闭环仍有实现工作，不能写成“只差本地测试”。

## 证据

`evidence/IDENTITY_AND_INTERSECTIONS.json` 含 12 个候选补丁的 SHA-256、实际 touched paths、非空交集、Codex 1,044-file 锁中的冲突路径与逐文件摘要、现有 ZIP 内精确 patch 路径和摘要。另附小型原报告快照，便于直接阅读能力边界；完整代码与原始运行日志继续以各候选包为准。

这些原报告是其各自阶段的历史记录。后继新增能力以本计划与状态页为准，不改写旧报告：例如 core 旧文档里的 history 待办已经由后继 acceptance-history 包完成。

本目录不含候选产品源码或执行脚本，不授权安装、登录、native 进程、模型、费用、Actions、push、合并或发布。
