---
name: rpnh-frontends
description: "Choose, configure and switch RPNH frontends with session-specific resume."
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: frontends.md
  revision: "2026-10-19.4"
  status: source-reviewed
  basis: "Frontend selection, setup and same-session handoff; validation recorded separately"
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

也可以明确连接已有安装：

```bash
rpnh frontend setup codex --codex-bin /absolute/path/to/stock/codex
rpnh frontend setup opencode --opencode-bin /absolute/path/to/opencode
rpnh frontend setup dsh --dsh-source /absolute/path/to/deepseek-harness
```

前端配置与模型、凭据配置独立。把 Codex 界面换成 DSH 界面时，仍使用该会话原来的
RPNH provider、精确模型、历史和已注册任务状态。

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

`--prompt` 仍为 Basic 的一次性执行，不显示前端菜单。脚本未明确选择前端且没有已配置
首选项时，会给出配置指引。显式 `--frontend auto` 保留原来的兼容 Codex／Basic 自动
选择；普通交互 `rpnh` 使用新菜单。
