---
name: rpnh-dsh-unified-validation
description: "官方 DSH Web、统一前端选择与共享 MainSession 的离线验证。"
metadata:
  document-kind: validation
  audience: operator-and-developer
  language: zh-CN
  counterpart: dsh-unified-20261019.md
  revision: "2026-10-19.2"
  status: offline-validated
---

[English](dsh-unified-20261019.md) | [中文](dsh-unified-20261019_ZH.md)

# 官方 DSH Web 与统一前端验证 — 2026-10-19

`rpnh --frontend dsh` 使用官方发布的 Web index、原生客户端插件、Web/Connection/
Typert 传输与模块加载器。绑定的 RPNH Remote BFF 将这些界面协议转为 Python
MainSession 操作，与 Basic、Codex、OpenCode 共用直接主会话。官方客户端资源未重写；
RPNH 的小型客户端扩展只负责打开 launcher 绑定的会话。

固定目标为 DSH 源码 `687f282e8625c6b8d11ff58211a90524be2e4317`、npm 资源
`0.2.1-rc.2`、Linux/WSL2、Node `22.22.1`。该版本没有随包提供终端 TUI。
[此前前端验证](frontends-20261019_ZH.md)保留 Codex、OpenCode 与旧 headless 宿主的范围。

## 行为与执行权威

- `rpnh config frontend dsh --dsh-source CHECKOUT` 保存独立于 provider/model 配置的
  偏好。显式 `--frontend` 优先；`auto` 保留 Codex/Basic 选择。`DSH_SOURCE` 优先于保存路径。
- launcher 在取得 MainSession owner lease 前检查固定源码、Node 与依赖，准备 31 个官方
  固定版本包、生成 Loader 配置并检查入口。首次准备下载缺失包；完整缓存可离线复用。
  资源检查会发现官方启动 JS/CSS 或客户端入口缺失，下载失败不覆盖已保留的旧包。
- 原生 Web 使用 loopback 监听和官方连接认证，原生宿主通过继承的私有 pipe 连接 Python。
  Registry 写入、provider、工具、停止及恢复都归 RPNH；没有加载 stock AgentLoop、LLM、
  tools 或 session persistence，也没有第二套持久化会话。临时界面事件只服务展示。
- 历史保留 Registry 的原始时间。未知历史模型明确标为未知，不能归到当前模型。模型选择
  与 reasoning effort 来自 RPNH 可用配置；切换到不支持 effort 的模型后不复用旧 effort。
- 支持绑定会话的文本输入、原生模型选择及 RPNH slash 菜单。单会话范围明确拒绝新建
  原生会话；文件上传、任意 workspace 编辑和原生工具执行不在此适配范围。
- 冷打开不会提交 prompt 或自动恢复暂停 turn。Stop 请求真实 checkpoint stop；
  `/rpnh-resume` 与 `/rpnh-rollback` 经原生命令菜单调用共享控制。关闭宿主只停止当前
  前端启动或显式恢复的主任务，等待实际 checkpoint，不接管外部 running/terminal 任务。
- 原有 `rpnh-dsh` headless 入口及其独立会话格式、恢复契约继续保留。

## 本次官方界面验证

| 窗口 | 结果与边界 |
|---|---|
| Python 前端偏好、CLI、launcher、官方资源 | 54 项通过，3.80 秒；含缺失入口修复、版本缓存复用、下载失败保留与继承 pipe/owner 生命周期。 |
| 官方 Web 与实际 Chromium | 15 项通过，9.96 秒，零跳过；原生历史、新输入、停止、冷重开、resume 命令、模型 A/high 切 B/no-effort、Host/Origin、私有 FD 与 EOF。此窗口 Python RPC 是脚本替身。 |
| 生产 launcher／真实 Registry／官方浏览器 | 五阶段通过：新回合结算、冷历史与启动停止、冷 checkpoint 显式恢复结算、退出暂停、冷打开保持暂停。累计本地 fixture 调用 `0→1→1→2→2→2`，五次显式 SIGINT 返回 130；核对同一 logical turn/attempt 目录和 Registry 输入/答案。停止覆盖 provider 提交前的启动窗。 |
| wheel 与文档 | 构建与独立 target 安装、官方模块/JSON 资源、已安装 help 与前端偏好保存通过；16 份文档、624 个本地目标、46 个 Bash 代码块通过。不是完整安装版测试集。 |

额外真实回退验证通过：原生 `/rpnh-rollback` 保留相同 logical turn 与 attempt 目录，
将 Registry 回合登记为 `interrupted`，没有新增 provider 调用；回退后下一输入也完成准入、
checkpoint stop 与回退。界面输入保持可用，两个已提交回答保留。原五阶段结束时第三回合
确实暂停；补充控制之后，现场为两个 committed、两个 interrupted、无 active turn，原
child Registry/checkpoint 均保留。官方阶段全部尝试共有三次本地 fixture 提交：通过运行
两次，第二次选择器失败之前的新回合一次；远程 provider/model 调用为零。

原型首次仅加载模块与 renderer，不计作界面通过；后续才实际渲染官方侧栏、聊天和输入框。
正式浏览器暴露了增量回答缺少 step 事件的问题：静态历史能显示，新的已完成回答却没有正文。
现由展示投影补全原生 step/start 与 step/end。原生命令 lookup 的 provider 注册也已按官方
接口修正；均保留首失败。新建会话假成功与会话公开错误丢失两项已加入回归：绑定界面明确
拒绝新建，首次打开和错误变化使用原生错误通道显示，也保留 checkpoint 提示。

实际回退还暴露了展示语义错误：DSH 原生 `interrupted` 专指意外退出遗留回合，而 RPNH
同名状态来自 owner 回退。现仅将展示映射为 `aborted/user`，保留 Registry 原状态，避免
误报 DSH 崩溃；暂停仍不关闭。修复前失败、原浏览器错误文案和修复后回归保留。测试替身
原先错误删除回合，已改成保留 interrupted lineage；未为这个替身错误增加 rebaseline。

官方资源与历史投影独审发现的两项问题已关闭：缺失启动 JS 时不再错误复用；未知模型
历史不再归到当前 profile。相关修复前失败与修复后检查均保留。正式实现九文件类型窗口自有诊断为零，上游 109 条诊断保留；
随后只增加回退回归与展示映射。完整上游 TypeScript 图不算通过。这里没有真实远程 provider/model 调用，也不认证全部 DSH 能力、
live provider、全部平台或未来 revision。

## 已发布组件阶段与继承修复

此前已发布的组件阶段使用 DSH 聊天/输入/主题组件与 AgentLoop/Session，尚未加载完整
官方 Web shell。该阶段的五阶段真实 Registry 验证和最终 11 项原生测试是历史证据，
不能替代本页的官方界面端到端窗口。Python 159 项、启动停止/launcher 27 项与最终 ownership
相关 10 项均保留各自范围，窗口重叠，不相加。

继承并保留的实际修复包括：只有 `RESUME_STARTED` 才登记当前前端的 ownership；连续状态
读取时仍按单调时钟截止时间执行 owner 结算；启动期间 run identity 尚未提交时，只向当前
前端登记的唯一存活主任务 handle 请求停止并等待真实 checkpoint；正常退出后的 BrokenPipe
进入 transport 错误处理。没有放宽 Registry authority、伪造 completion 或使用进程退出代替
暂停证据。最初本地 fixture 的非规范 JSON 被协议正确拒绝，只修正替身序列化。

原官方端到端前两次失败属于测试选择器：空会话与已有会话 placeholder 不同，以及侧栏、
面包屑、消息正文出现相同 prompt。两次均保留原记录，只修正本地 driver；未计为完整通过。

设置与操作见 [DSH 指南](../guides/dsh_ZH.md)。
