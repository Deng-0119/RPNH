---
name: rpnh-opencode-20261019-validation
description: "记录稳定版 OpenCode 1.19.3 的协议和原生离线验证。"
metadata:
  document-kind: validation
  audience: operator-and-developer
  language: zh-CN
  counterpart: opencode-20261019.md
  revision: "2026-10-19.1"
  status: passed-limited-offline-validation
  upstream-version: "1.19.3"
---

# OpenCode 1.19.3 验证 — 2026-10-19

[English](opencode-20261019.md) · [使用指南](../guides/opencode_ZH.md)

## 版本与范围

目标为 [opencode-ai 1.19.3](https://www.npmjs.com/package/opencode-ai/v/1.19.3)，通过
`npm install --prefix <task-install> --no-audit --no-fund opencode-ai@1.19.3` 获取。
本任务版本发现已核对 npm `latest` 和 [GitHub 稳定 release v1.19.3](https://github.com/anomalyco/opencode/releases/tag/v1.19.3)。
tag 对应 `e3c4eb242585dcd77be1a867391b7c25fd47ce52`。
实际 Linux x64 ELF 的版本输出精确为 `1.19.3`，退出码 0、stderr 为空。
npm `.bin/opencode` 指向 `opencode-ai/bin/opencode.exe`，后者与已测
`opencode-linux-x64/bin/opencode` 是包管理器建立的内部硬链接。生产启动器经该 PATH
入口、在隔离环境中执行的精确版本探测也已通过。没有全局安装、登录或账户配置修改。

RPNH 起点为公开 `32b566da0a8c6249b1e42c0c23896b6c94753483`，叠加本页所述本地改动。
原生测试环境为 Ubuntu 24.04.4、WSL2 Linux 6.6.87.2、x86_64、glibc 2.39，
36×120 PTY、`xterm-256color`。当前只接受 1.19.3；旧 1.18.32/1.18.35 结果保持历史属性，
本轮不认证开发版 2.0.32 或其它版本。

## 协议变化

稳定客户端同时使用旧 session API 和带 location 的新目录。release commit 的参考源码包括
[SDK types](https://github.com/anomalyco/opencode/blob/e3c4eb242585dcd77be1a867391b7c25fd47ce52/packages/sdk/js/src/v2/gen/types.gen.ts)、
[TUI DataProvider](https://github.com/anomalyco/opencode/blob/e3c4eb242585dcd77be1a867391b7c25fd47ce52/packages/tui/src/context/data.tsx)、
[sync 启动](https://github.com/anomalyco/opencode/blob/e3c4eb242585dcd77be1a867391b7c25fd47ce52/packages/tui/src/context/sync.tsx)
和 [attach](https://github.com/anomalyco/opencode/blob/e3c4eb242585dcd77be1a867391b7c25fd47ce52/packages/opencode/src/cli/cmd/attach.ts)。

- `/experimental/console` 返回无受管 provider、无可切换组织。
- `/api/location`、`/api/agent`、`/api/model`、`/api/provider`、`/api/command`
  投影同一 RPNH 显示目录、agent、精确 profile 和控制命令。
- `/api/integration`、`/api/skill`、`/api/reference` 返回空目录响应。
- SDK deep-object `location[directory]`/`location[workspace]` 查询仍受当前隔离显示目录约束，
  其它 workspace 和歧义查询会失败。
- 新 API 的写入、原生凭据/工具/session 效果仍不支持；不暴露 provider endpoint、key、
  request override，也不建立平行 Registry。

包内兼容 schema 记录当前源码提取子集，不是上游 SDK 生成物，也不代表完整 v2 API 支持。
旧 1.18.35 候选已从接受集合移除；没有用版本范围、环境覆盖或伪造版本输出替代协议适配。

## 实际验证

| 窗口 | 结果 | 含义 |
| --- | --- | --- |
| 首批 OpenCode 定向回归 | 244 passed，209.68 秒 | protocol/profile/gate、application、CLI、Registry integration；此时尚未加入新 v2 启动目录 |
| 最终 protocol/profile/gate/current-interface | 236 passed，2.06 秒 | 当前 schema、旧/新目录 profile 对应、location 限制、原生写拒绝、精确版本拒绝及传输 |
| 实际客户端已建会话 contract | 1 passed，7.67 秒 | 中间诊断窗口，真实 ELF 启动/help/prompt/SSE 答案 |
| 实际客户端新建会话 contract | 1 passed，8.31 秒 | 原版客户端，新建 session、全部启动路由、help、单次 submit、fixture committed 答案显示 |
| 实际客户端 committed Registry 读取 | 1 passed，73.07 秒 | 两次冷连接、历史、PN 变体、逻辑状态不变及 owner 检查 |
| 生产 PATH 解析/版本探测 | 通过 | npm `.bin` 解析到同一已测 ELF；隔离生产探测精确接受 1.19.3 |

回归窗口有重叠，不相加。每次显式 native lane 只选一个测试，另一个 deselect，未把 skip 计作通过。
contract 后端为 `ApplicationDouble`，一次 submit、零物理模型调用。
Registry fixture 准备两个 committed turn，恰好两次确定性 fake-port 调用；测量读取窗口新增
fake-port/provider/submit/spawn 效果为零，原 gateway tick 执行 34 次。
`--session` 和 `--continue` 都显示两轮历史，消息 ID 稳定；原 owner lease 拒绝第二 writer，并在退出后释放。

`/rpnh-net`、`--show-resources`、`--resources-only` 的完整结构均与原 RPNH projection/filter 一致。
最终原生窗口 HTTP 路由均无错误。真实客户端仅负责展示；模型、工具、Registry、workspace、checkpoint
权威继续属于 RPNH。

## 保留的失败与修正

1. 原严格 1.18.32 probe 拒绝真实 1.19.3 客户端，符合预期。
2. 原生 fixture 首次因缺 scipy 阻断。仅在任务依赖目录安装 scipy 1.18.1，未改既有环境，随后重跑受影响检查。
3. 加入 Console 响应后，实际客户端仍读取 `/api/agent` 和 `/api/integration`，收到 404。
   一次诊断重试确定路径，再按稳定 DataProvider 源码确认八个目录读取并补齐真实 DTO。
4. 首轮 Registry 原生窗口已经得到正确 PN，却因等待长 PN 视口上方的旧标题而超时。
   测试改为观察实际可见的 `Rpnh-Observation` 角色标签，仍保留完整 PN 结构对照，修正后通过。

原失败、终端摘录、路由和后续成功记录保留在任务本地证据中。未执行任务侧 hash、source-tree gate，
也未用全字节审计替代。Git 版本和工作树信息仅用于描述条件。

## 复现与限制

[指南](../guides/opencode_ZH.md#显式原生离线检查)列出两个显式 native 命令和当前回归选择。
helper 不安装依赖，也不接受用户 Registry 路径。将 `TMPDIR` 与 pytest `--basetemp` 指向可写测试目录；
需要避免源码树缓存时关闭 pytest 缓存。若本地 PATH 依赖任务内安装，应保留该客户端。

这里只是有限 WSL2 离线接受。没有调用真实 provider/model 或外部工具效果。
原生 picker/effort 矩阵、强制 SSE 重连、长历史分页、活跃 worker 恢复、非 WSL Linux、Windows、macOS、
mini mode、上游插件/工具和 dev 2.0.32 未验证。不宣称操作系统网络隔离或新版 wheel 验证。
终端检查为有界文本观察，不是截图；usage/cost/context 仍不可用。
