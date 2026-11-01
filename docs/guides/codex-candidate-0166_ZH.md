---
name: rpnh-codex-0166-candidate
description: "检查显式 Codex 0.166 前端协议候选，不改变正式默认版本。"
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: codex-candidate-0166.md
  revision: "2026-11-01.1"
  status: source-candidate
  basis: "固定上游 schema；确定性离线协议检查"
---

[English](codex-candidate-0166.md) | [中文](codex-candidate-0166_ZH.md)

# Codex 0.166 前端候选

正式默认前端保持精确 **0.163.0**。`candidate-0.166.0` 仅可通过可信宿主调用
`CodexAppServer`、`resolve_codex_binary`、`run_codex_frontend` 的显式 Python
keyword 选择；不是自动版本范围、环境变量、CLI 偏好或客户端自选升级。
二进制版本探测与 `codex-tui` initialize 握手仍须匹配所选精确版本。

候选复用原 RPNH session／Registry 历史投影。所选请求、响应和通知合同读取自
官方 `rust-v0.166.0`，commit `6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`。
[夹具来源说明](../../tests/fixtures/codex/0.166.0/README.md)记录38份schema子集
及与0.163的对比。已测子集无需修改必填响应投影；增加profile不代表完整原生客户端
已认证。未实现的memory、process、MCP及attachment方法仍拒绝；空能力发现不等于
已安装能力。

重开前端历史不会恢复暂停的业务任务，也不会重发prompt。原owner绑定、不可变Registry
历史、安全纯文本投影、精确item游标及中断／自然终态竞争行为保持不变。
`:danger-full-access`只是wire标签，不扩大Registry授权或运行隔离。

独立的 `codex_subscription_bridge` **exec JSONL模型端点未修改**。
`RPNH_CODEX_BIN`选择前端，不替换配置中的后端可执行文件，也不开放后端工具。
后端仍只允许agent-message与reasoning条目，拒绝内置执行条目。

最初离线窗口使用真实临时Registry历史与模拟transport／lifecycle边界。中断／终态用例
使用确定性的假session结算时点，不是实际Registry或原生客户端竞态测试。该最初窗口没有模型API、
安装、stock 0.166二进制、原生UI或完整Unix-socket会话实测。原生资格验收及默认版本
提升仍需另行证据与授权；本候选不是发行版。

## 后续有界原生证据

随后已用正式0.166.0对闲置已提交Registry历史执行两次真实PTY会话。恢复入口现向客户端传递`resume`和精确绑定thread ID；首次启动不变。初始化、历史、冷重连及unsubscribe保持同一thread、两turn、四item，无新turn或模型调用。这不是活动任务存续、业务checkpoint恢复、默认0.163原生再认证或完整客户端资格。首失败与限制见[源码验证说明](../validation/host-adapter-source-20261101_ZH.md)。
