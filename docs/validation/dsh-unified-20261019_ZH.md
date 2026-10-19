---
name: rpnh-dsh-unified-validation
description: "DSH 统一前端选择、共享 MainSession 与离线验证。"
metadata:
  document-kind: validation
  audience: operator-and-developer
  language: zh-CN
  counterpart: dsh-unified-20261019.md
  revision: "2026-10-19.1"
  status: offline-validated
---

[English](dsh-unified-20261019.md) | [中文](dsh-unified-20261019_ZH.md)

# DSH 统一前端验证 — 2026-10-19

`rpnh --frontend dsh` 选择使用 DSH 原生聊天、输入与主题组件的 Web 展示，
生命周期使用 DSH AgentLoop/Session 对象，与 Basic、Codex、OpenCode 共用同一个
直接 RPNH MainSession。它不是完整上游 Web shell；固定版本上游没有随包提供终端 TUI。

目标为 DSH 源码 `687f282e8625c6b8d11ff58211a90524be2e4317`、`0.2.1-rc.2`，
Linux/WSL2、Node `22.22.1`。[此前前端验证](frontends-20261019_ZH.md)保留旧 DSH
headless 宿主与其他客户端的验收范围；本记录新增统一展示入口。

## 使用行为

- `rpnh config frontend dsh --dsh-source CHECKOUT` 保存独立于 provider/model 配置的
  前端偏好。显式 `--frontend` 优先；`auto` 保持 Codex 或 Basic 的原行为。
  `DSH_SOURCE` 优先于保存的源码路径。
- 启动先检查固定源码与依赖、编译浏览器展示并检查资产，再取得 MainSession owner lease。
- 原生宿主通过两条继承的私有 pipe 连接 Python。provider、Registry 写入、工具与恢复归
  Python 管理，不挂载 DSH 原生 provider adapter、工具、filesystem plugin 或持久化会话存储。
- 历史投影来自所绑定 MainSession。重新打开不会提交 prompt 或自动恢复暂停回合。
  `/rpnh-resume`、`/rpnh-rollback` 使用共享应用控制。关闭宿主只 checkpoint-stop
  当前前端启动或显式恢复的主回合。
- 既有 `rpnh-dsh` registered headless 宿主继续可用，保留其原会话格式与恢复契约。

## 验证与实际修复

| 窗口 | 结果与范围 |
|---|---|
| Python 选择、CLI、launcher、私有 RPC、应用边界 | 159 项通过，82.53 秒；包含真实 Registry 历史、owner、重新打开检查及本地继承 pipe 子进程。 |
| 原生 DSH 与实际 Chromium | 最终 11 项通过，8.56 秒，含此前八项生命周期／HTTP／FD／输入／历史／停止／恢复与三项发布回归。此窗口 MainSession RPC 为脚本替身。 |
| 周期 owner 调度 | 修复前持续读取的一个回归失败；修复后三项 gateway 检查通过，包含 GET 观察行为及 owner 线程调度。 |
| 构建 wheel | 构建、独立 target 安装、前端模块资源、已安装 help 与保存前端配置通过；不是完整安装版测试集。 |
| 生产 launcher／真实 Registry／浏览器 | 五阶段通过：新回合回答、冷历史＋启动阶段停止、冷 checkpoint 显式恢复并结算、退出暂停、再次冷打开保持暂停。两次本地确定性 provider 调用；启动停止、退出与查看均无新增调用。五次显式终端 SIGINT 返回 130。 |
| 启动停止与关闭边界 | 27 项通过（22 项应用边界、5 项 launcher）；最后相关 10 项通过。窗口重叠，不相加。 |

最终审阅还复现了 Host／Origin 缺少校验，以及由带 reasoning effort 的模型切换到不支持该设置的模型后，旧 effort 被轮询恢复的问题。修复前三个回归失败；现先校验实际 loopback Host 再发放页面 token，并校验 API 携带的 Origin；模型与 effort 只在初次加载时同步，后续轮询保留用户选择。修复后十一项原生测试通过。

独立审阅发现冷打开后显式 resume 启动的主任务没有登记为前端管理对象，修复前两个回归失败。
现仅在 `RESUME_STARTED` 后登记 ownership，已有 running 或 terminal 任务不会被新接管。
应用边界回归通过。

生产浏览器还确认启动期间数据库已创建但 run identity 尚未提交的窗口。此时停止可使用当前前端登记的唯一存活主任务 handle，随后等待真实 checkpoint；没有放宽 Registry 验证，也不允许借此向未归属任务发信号。相关失败及诊断保留。关闭已退出 Node 的 pipe 时观察到的 BrokenPipeError 也进入已有 transport 错误处理。

生产浏览器验证还暴露了依赖空闲请求队列的 owner 调度：持续 DSH 状态读取可阻止终态证据
结算。现按单调时钟截止时间执行周期结算；读取方法仍为观察方法。

首失败均保留：Chromium socket 路径超过平台限制；输入组件缺少原生编辑行为注册；字体
加载需要正确 CSP；首次本地 provider fixture 输出非规范 JSON，被 adapter 正确拒绝。
只修正替身序列化，未改协议校验。随后真实 Registry 运行已产生终态证据，并暴露上述调度缺陷。

集成构建通过，所检查宿主文件无剩余类型诊断；完整 TypeScript 图仍在上游 vendor 源码出现
诊断，不计通过。没有真实远程 provider/model 调用。本记录不认证完整 stock Web 应用、
live provider、全部平台或后续上游 revision。

设置与操作见 [DSH 指南](../guides/dsh_ZH.md)。
