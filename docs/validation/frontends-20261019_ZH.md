---
name: rpnh-current-frontend-validation
description: "当前 Codex、DSH 与 OpenCode 的有日期兼容范围。"
metadata:
  document-kind: validation-record
  audience: operator-and-developer
  language: zh-CN
  counterpart: frontends-20261019.md
  revision: "2026-10-19.1"
  status: bounded-offline-validated
---

[English](frontends-20261019.md) | [中文](frontends-20261019_ZH.md)

# 当前前端适配 — 2026-10-19

有正式发布渠道时，本次选取当前稳定客户端，并单独注明开发版本。
模型选择、执行、工具、Registry 状态和 checkpoint 恢复仍由 RPNH 管理。

| 前端 | 本次上游版本 | 范围 |
|---|---|---|
| Codex | `@openai/codex` / `codex-cli 0.163.0` | 精确 remote TUI profile；官方原生生成的协议 schema |
| OpenCode | `opencode-ai 1.19.3` | 原生 attach TUI；不启用 OpenCode provider/tool backend |
| DSH | `687f282e8625c6b8d11ff58211a90524be2e4317`，`0.2.1-rc.2` | 准备后的源码宿主与明确的当前消息 dialect |

三个所选宿主均已完成有界原生离线检查，本次没有真实 provider 实验。
schema 检查与原生观察分开报告；下方测试窗口存在重叠，不能相加。

## 实现与实际验证

Codex 默认使用精确的 `pinned-0.163.0`。旧 0.155 与 0.161 candidate
保留显式测试入口，不自动按版本回退。当前 profile 支持 object item cursor
与显式 resume 默认字段。原生启动需要非 Bedrock advisory 响应，冷恢复另需
空 goal 查询和客户端退订。退订仅抑制该客户端的通知，保留 RPNH 执行与
Registry 历史；展示层 resume 可重新订阅。

实际 0.163.0 TUI 显示了确定性 local-process 答案，通过真实 Registry 中断第二个
合成 turn，并用全新客户端 home 和 server 进程冷恢复原已提交答案。
最终启动／冷恢复／退出窗口均为零 RPC 错误、exit 0。
最终受影响 Codex／协议／边界回归 **57 项通过**，耗时 32.66 秒。
较宽首轮为 254 通过，另一个动态源码检查因 handler 正在编辑而失效；
源码稳定后已复跑受影响文件，不将失效或中断窗口称为整套通过。

OpenCode 1.19.3 混合使用旧接口、八个带 location 范围的 `/api/*` 目录读取
与 Console 发现。当前协议／profile 回归 **236 项通过**，耗时 2.06 秒。
原生客户端的新会话／帮助／prompt／SSE 显示，以及两次真实 Registry 冷连接、
稳定消息身份和 PN 视图均通过。prompt／SSE 契约使用 application double；
冷读窗口使用真实 Registry 和确定性 fixture turn，测量期间新增模型、submit、
spawn 效果均为零。详见 [OpenCode 记录](opencode-20261019_ZH.md)。

DSH 使用当前 V4 一等 tool message。真实源码宿主完成确定性 headless turn：
**Sum: 12**，三次本地模型分派、两次原生工具分派、一个已提交 turn、无 active turn。
Python 有界选择 **49 项通过**，耗时 69.89 秒；TypeScript codec **56 项通过**。
原生选择覆盖相关联工具 DTO、准入前拒绝和非法／未匹配结果拒绝，其余用例明确未选。
较早 Python 窗口在 20 项通过后中断，不计整套通过。旧 revision 历史仍可读取，
执行继续要求原精确 revision。
原生完整首轮为 15 通过、两个旧夹具失败：工具往返仍用 V3，拒绝夹具仍用旧
3 MiB 预算。改用上游 V4 tool-result 构造器与既有生产 `LIMIT` 后，受影响检查通过。
未放宽生产准入 gate，原错误与 JUnit 均保留。

改动的双语文档已通过元数据／语言配对、本地链接／锚点和代码块语法检查。
CLI 帮助直接使用实际兼容 pin。

## 原失败与边界

本机 Codex wrapper 附加 `--add-dir`，被 0.163.0 remote 模式拒绝。
使用既有 `RPNH_CODEX_BIN` 指定原生 launcher 后通过，未改外部 wrapper 或配置。
初始合成 adapter 返回非 canonical JSON envelope，修正后才取得已接受的确定性结果；
原失败均保留。OpenCode 的旧 pin 拒绝、缺测试依赖、新 bootstrap 路由缺失和
viewport 断言失败分列在详细验证页。

这是 Linux／WSL2 离线验收，不代表所有原生命令、picker 组合、长期重连／恢复、
desktop／browser 前端、真实 provider 效果／计费或未来所有版本均已认证。
旧付费实验结果与图片保留原版本；更新展示宿主不迁移旧任务权威。

## 上游来源

- [Codex 0.163.0](https://github.com/openai/codex/releases/tag/rust-v0.163.0)
  与 [app-server 协议](https://learn.chatgpt.com/docs/app-server)。
- [OpenCode 1.19.3](https://github.com/anomalyco/opencode/releases/tag/v1.19.3)。
  观察到的开发版 `2.0.32` 不作为本次稳定客户端。
- [DSH 源码](https://github.com/deepseek-ai/deepseek-harness/tree/687f282e8625c6b8d11ff58211a90524be2e4317)。
  npm `latest` 为 `0.2.0-rc.2`，`next` 为 `0.2.1-rc.2`；集成绑定精确源码修订，
  不静默追随 npm tag。

启动命令见[安装](../guides/installation_ZH.md)、[Codex 与 DSH 适配](../guides/adapters_ZH.md)、
[OpenCode](../guides/opencode_ZH.md)与 [DSH](../guides/dsh_ZH.md)。
