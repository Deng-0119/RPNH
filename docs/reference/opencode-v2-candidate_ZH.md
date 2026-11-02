---
name: rpnh-opencode-v2-candidate
description: "说明显式 OpenCode 2.2.5 协议候选及未验证的原生边界。"
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: zh-CN
  counterpart: opencode-v2-candidate.md
  revision: "2026-11-01.1"
  status: offline-protocol-candidate
  basis: "fixed upstream source; no native certification"
---

[English](opencode-v2-candidate.md) | [中文](opencode-v2-candidate_ZH.md)

# OpenCode 2.2.5：显式协议子集，不是正式前端

已安装入口仍固定 **1.19.4**。独立 `OpenCodeV2Protocol` 必须显式解析2.2.5候选
profile；旧协议会拒绝该profile，不能把旧wire结构假装成新版。没有环境选择、默认升级，
也没有从生产launcher开放候选。**不是完整原生启动或实时聊天认证。**

官方固定目标为 `v2.2.5`，commit `9bbb8ce18c80f099e0e448775f59d965ead40a21`。
已核源码：`packages/cli/src/commands/commands.ts` 的 directory + `--server`；
`packages/cli/src/services/server-connection.ts` 的 `OPENCODE_PASSWORD`、固定
Basic用户名 `opencode`、初始 `/api/info`，显式server不得回退个人daemon；
`packages/client/src/promise/generated/client.ts`、`types.ts` 的REST包装、
SessionInfo、SessionInboxUser、历史、interrupt、事件；以及
`packages/client/src/solid/connection.ts` 的首事件 `server.connected`、keepalive
与重连。重连不授权重发输入。

## 已实现离线子集

只有原 `FrontendGateway` 拥有应用权威。候选覆盖 `/api/info`、`/api/session`
创建/列表、`/api/session/active`、session读取、prompt、message历史、interrupt、
`/api/event`连接/状态。原输入id映射既有request identity；相同重试复用已有请求，
冲突由owner拒绝。忙会话仍拒绝：wire inbox的 `delivery: queue` 仅标记已接收输入，
**不增加待执行队列**。显式delivery、checkpoint resume、files/agents/skills、非空
metadata、权限更改、原生工具、PTY、schedule和未支持catalog均明确拒绝。model/effort
只在创建时通过既有RPNH profile选择。

历史仅投影安全的已注册回答或暂停观察，不暴露provider原文。必填数字使用明确标注的
零显示占位，不是实测成本/token。未实现cursor分页；需要部分页时拒绝，不假称历史完整。

`frontend_launch_spec(..., profile=...)` 只给隔离认证工具构造精确版本argv和本次认证，
不会启动客户端；生产launcher仍只传1.19.4默认profile。候选HTTP只接受本次 `opencode`
凭据，不兼容放行旧 `rpnh` 用户名；原loopback Host/Origin与输入界限保持不变。

## 明确未完成

仅投影官方**非持久化** `server.connected`、`session.status`。原生持久消息事件要求
上游aggregate事件序列，Registry turn ordinal并不等价；不能另建事件账本或伪造durable
metadata。调用方须刷新REST历史。原生UI实时消息刷新、catalog/启动完整性、附件接入、
精确原生TUI、真实owner PTY/IPC及真实provider/model仍未验证或未支持。
本机loopback HTTP/SSE已用脚本应用gateway验证，不等于原生客户端或真实Registry闭环。离线测试未安装
原生前端/软件，未调用任何provider或model。

schema夹具为按固定源码提取的受限子集，**不是上游schema生成器输出**，不构成原生认证。

### 消息标识与历史证据

上游运行时 schema 要求消息 ID 以 `msg_` 开头。新显式 ID 还须满足原 RPNH
请求 ID 字符规则，接受后原样回显。生成的用户/助手 ID 分别使用
`msg_rpnh/{session}/{ordinal}/user` 和 `/assistant`；斜杠是显式请求 ID
禁止的字符，因此与调用者 ID（包括旧 `.rpnh-answer` 后缀）严格分离。
这只是既有引用的可逆展示投影，不新增事实、hash 或标识存储，冷重建稳定。
其他前端已存的不兼容显式 ID 在历史投影时报 409，不重写记录、不冒充兼容。
保留模型证据及展示依据 metadata；旧记录的当前配置回退不冒充确切历史模型。

### 旧版 1.19.4 标识窄保护

默认旧版保留所有无碰撞 ID，逐字不改。message/prompt_async 派发前用独立
协议实例准入互斥锁覆盖新快照、角色 ID 交叉检查和 owner submit，不等待模型执行。
新显式用户或派生助手 ID 与其他 turn 冲突时在 submit 前拒绝；同 key 重复
仍走原 owner 去重/内容冲突检查。既有历史碰撞（含中断 turn 保留的 ID）
明确报 409，不重写 ID、不发歧义事件。此措施不是 owner 全域身份机制；
其他协议实例或 command/其他前端入口仍可能引入冲突，读取时拒绝投影。
