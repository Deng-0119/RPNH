---
name: rpnh-frontends
description: "Choose, configure and switch RPNH frontends with session-specific resume."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: frontends.md
  revision: "2026-10-26.1"
  status: source-reviewed
  basis: "Frontend selection, setup, Basic terminal and same-session handoff; validation recorded separately"
---

# 选择、配置与切换前端

[English](frontends.md) | [中文](frontends_ZH.md)

在终端输入 `rpnh`，新会话会显示选择菜单，可选 Codex、OpenCode、官方 DSH Web 或
内置 Basic 终端。菜单显示哪些界面已就绪；选择尚未准备好的前端后，RPNH 会先连接或
安装受支持的版本，再打开会话。Basic 无需单独安装。

初次使用可执行 `rpnh init`，依次配置模型并选择前端。也可以先独立配置界面：

```bash
rpnh frontend
rpnh frontend list
rpnh frontend setup codex
rpnh frontend setup opencode
rpnh frontend setup dsh
rpnh frontend setup basic
```

`rpnh frontend` 和 `setup` 只配置选择，不打开对话、不调用模型。兼容的已安装客户端
会直接复用；缺失的客户端安装到 RPNH 偏好文件旁的 `frontends/`，不替换全局客户端。
Codex／OpenCode 安装需要 Node 和 npm；DSH 需要 Git、Node `^22.19.0 || >=24.0.0`，
setup 会准备固定源码、pnpm 依赖和官方 Web 资源。缺少前置工具时会给出安装指引；
配置失败不会保存为可用选择，菜单允许改选其他界面。

OpenCode 配置与启动精确要求 **1.19.4**；管理安装使用官方 npm registry 的
`opencode-ai@1.19.4`。已保存的 1.19.3 客户端须通过 setup 更新或重新连接；启动器在
取得 Registry owner 前拒绝其他版本。有限离线检查与历史验证边界见
[OpenCode 指南](opencode_ZH.md)。

也可以明确连接已有安装：

```bash
rpnh frontend setup codex --codex-bin /absolute/path/to/stock/codex
rpnh frontend setup opencode --opencode-bin /absolute/path/to/opencode
rpnh frontend setup dsh --dsh-source /absolute/path/to/deepseek-harness
```

前端配置与模型、凭据配置独立。把 Codex 界面换成 DSH 界面时，仍使用该会话原来的
RPNH provider、精确模型、历史和已注册任务状态。

## Basic 终端

执行 `rpnh --frontend basic` 即可启动，无需添加 UI 依赖或单独安装客户端。交互终端的
欢迎面板显示已配置模型、当前工作目录与会话；输入分隔线和状态使下一次输入与之前的
输出清晰分开。

Python `readline` 可用时，支持当前行编辑、Up/Down 浏览本进程输入历史，以及 Tab
补全基本 slash 命令。此输入历史与已登记的对话历史独立，不跨进程保存。行末加
反斜杠（`\`）可将同一条消息继续输入到下一行；最后一行不加反斜杠即可提交。

`/help` 只显示以下基本命令：

| 命令 | 作用 |
|---|---|
| `/help` | 显示基本帮助 |
| `/clear` | 清理显示，不删除对话历史或重置会话 |
| `/status` | 查看主会话身份、模型、焦点与回合状态；选中子任务时保留原有任务状态行为 |
| `/history` | 显示最近至多 10 条已登记的对话消息 |
| `/frontend [NAME]` | 选择或指定前端，继续使用同一会话 |
| `/resume` | 显式恢复暂停的主回合或结算其终态结果；选中子任务时使用其既有恢复控制 |
| `/rollback` | 将暂停的主回合退回上一个已完成回合 |
| `/quit`（别名 `/exit`） | 退出 Basic |

主会话焦点下的 `/status` 只读；`/clear` 不删除 Registry 记录，`/history` 不重放消息、
不调用模型。`/help tasks` 列出现有高级控制，包括 `/agent`、`/workflow`、`/tasks`、
`/switch` 和 `/task ID ...`；它们仍可使用，但不出现在默认帮助或基本命令补全中。
任务示例见[使用指南](usage_ZH.md)。

在空输入时按 `Ctrl+D` 发出 EOF 并退出。在提示符按 `Ctrl+C` 会取消当前输入，
显示使用 `/quit` 退出的提示，然后在同一会话中继续接受输入。
`/quit`（或 `/exit`）正常退出，状态码为 0。
Agent 执行期间仍沿用既有 checkpoint 暂停行为和显式恢复／回退控制。切换前端不改变
会话的模型或恢复语义。

## 恢复与切换

```bash
rpnh --session-dir ./my-session
# 退出界面后，沿用上次的前端恢复：
rpnh --resume ./my-session
# 或对同一会话明确换一个界面：
rpnh frontend switch opencode --resume ./my-session
```

resume 使用该会话上次成功接管时的前端，不受新会话首选项改变影响；显式
`--frontend NAME` 可以覆盖。没有前端记录的旧会话，在交互终端显示选择菜单。
打开历史不会自动续跑已暂停的执行。

在 Basic 内输入 `/frontend` 可选择其他界面，也可直接输入 `/frontend codex`、
`/frontend opencode` 或 `/frontend dsh`。Basic 会先释放所有权，再由新前端打开
同一会话。在其他界面中，先退出当前界面，再执行
`rpnh frontend switch NAME --resume PATH`。同一会话仍只允许一个可写界面。

不带 `--resume` 的 `rpnh frontend switch codex` 用于设置下次新会话菜单的首选项，
不会接管正在打开的界面。

## 脚本与明确选择

`rpnh --frontend NAME` 直接指定界面，不显示新会话菜单；交互终端中缺少该客户端时
进入配置。脚本应先执行 `rpnh frontend setup NAME`，非交互启动不会自行下载或提问。

`--prompt` 仍为 Basic 的一次性执行，不显示前端菜单。一次性执行、输入／输出重定向和
`TERM=dumb` 终端使用纯文本输出，不添加 ANSI 提示符效果、欢迎面板或交互输入装饰。
脚本未明确选择前端且没有已配置首选项时，会给出配置指引。显式 `--frontend auto` 保留原来的兼容 Codex／Basic 自动
选择；普通交互 `rpnh` 使用新菜单。


参见 [Basic 与 Viewer 离线帮助](frontend-help_ZH.md)，了解情境排查及本机监听上限。
