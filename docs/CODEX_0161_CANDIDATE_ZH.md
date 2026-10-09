---
name: rpnh-codex-0161-candidate
description: "显式选择 Codex 0.161 协议及其有限范围。"
metadata:
  document-kind: guide
  audience: developer
  language: zh-CN
  counterpart: CODEX_0161_CANDIDATE.md
  revision: "2026-10-09.2"
  status: maintained
---

[English](CODEX_0161_CANDIDATE.md) | [中文](CODEX_0161_CANDIDATE_ZH.md)

# Codex 0.161 协议候选

本增量是经源码/schema核对的候选，原生认证仍待完成。普通 RPNH CLI 与既有调用方保持精确 Codex 0.155.0 默认值；选择该 profile 不会安装客户端或改变 Registry 权限。

既有 `cpn/frontend/codex_compatibility.v1.json` 是唯一能力表。默认 `pinned-0.155.0` 与显式 `candidate-0.161.0` 由 `CodexAppServer`、`resolve_codex_binary`、`run_codex_frontend` 的可选 `compatibility_profile` keyword 选择。它是受控候选 gate 入口，不是普通 CLI 新参数，也不会经环境变量自动升级。未知 selector、任意 version 字符串和 CLI/initialize 版本不一致均拒绝。launcher 只核指定 binary，不下载、安装、寻找替代版本或改全局配置。server 持有单个不可变 profile，客户端自报版本不能选择能力。

0.161 profile 为 `thread/items/list` 增加 object anchor `{"type":"item","itemId":"..."}`，必须带有界非空 `turnId`。精确 committed turn 与现有两个安全 slot 之一须属于同一个新捕获 cut。adapter 做 refs-only 定位后转入既有 exclusive native anchor，所有输出续页仍为既有 `rpnh-history-v1` string。旧 string 保持原 cut，在同 source/root 的两个精确 profile 下重开都重新校验。object 不含旧 cut，不能假装继承上次 resume 的 cut。

每个 committed turn 恰有两个安全 item：原始用户文本和安全 main-decision 回复。exclusive object anchor 后最多剩一个，所以 `nextCursor` 为 null；非空结果仍有原 reverse/inclusive string cursor。不增加 slot、跨 turn 搜索、新 Registry/store、正文 reader、grant、身份别名或持久 cursor 表。failed/pending/active、tool/child item、多余 object 字段和不存在的 ID 均 fail closed。

0.161 history entry 明确返回 `startedAtMs: null`、`completedAtMs: null`；resume 明确返回 `collaborationMode: null`、`disabledPluginIds: []`。它们表示未记录时间、default-only模式和空插件范围，不编造历史事实，不宣称恢复 Plan mode 或插件状态。0.155 wire 输出保持原样。live 通知既有必需整数时间戳不变，不能按历史 optional 规则省略。

使用已经安装且精确报告 `codex-cli 0.161.0` 的 binary，并传入
`compatibility_profile="candidate-0.161.0"`；默认 profile 则要求精确
`codex-cli 0.155.0`。binary 路径依次从显式参数、`RPNH_CODEX_BIN`、
PATH 中的 `codex` 选择。缺失或版本不符会抛出兼容错误。选择 profile
只选择适配器协议，安装的 binary 必须独立匹配。一般前端配置见
[适配器指南](guides/adapters_ZH.md)。

[2026-10-09 有限验证](results/product-validation-20261009/README_ZH.md)
记录了源码/协议检查，stock 客户端为 NOT_RUN，完整 native fixture 绑定为 BLOCKED；
这些结果不认证已安装的 0.161 客户端。纯 JSON/Registry 测试与源码审核不等于
Rust 编译、真实 provider 验证或完整 API 覆盖。确定性协议用例位于
`tests/test_codex_0161_candidate.py`；object-cursor RPC fixture 不能证明 stock TUI
实际发送过该请求。
