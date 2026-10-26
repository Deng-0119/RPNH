---
name: rpnh-frontend-help
description: "使用 Basic 终端及离线上下文帮助，不改变任务运行。"
metadata:
  document-kind: how-to
  audience: operator-and-developer
  language: zh-CN
  counterpart: frontend-help.md
  revision: "2026-10-25.1"
  status: source-candidate
---

# Basic 终端与本地帮助

[English](frontend-help.md) | [中文](frontend-help_ZH.md)

用 `rpnh --frontend basic` 启动 Basic。它保留普通终端滚动记录，并显示当前模型、会话与输入目标。本次保留已有 provider 配置、任务控制与 owner lease，保留当前会话前端选择及 `/frontend` 切换功能。

## 配置模型前也能使用帮助

```bash
rpnh help
rpnh help viewer
rpnh help recovery --language zh-CN
rpnh help provider --json
```

这些命令只读取安装包内的双语帮助目录，不发现 profile、不创建会话、不查询 Registry、不调用模型、不启动服务器。JSON 返回静态帮助，并非运行诊断。成功退出码为 0；帮助参数错误、主题未知或目录不可用时为 2。指定 `--json` 时错误也是 stdout 上的 JSON；文本错误写入 stderr。

主题包括 `basic`、`viewer`、`legend`、`live`、`history`、`comparison`、`connection`、`access`、`registry`、`recovery`、`provider`、`assets` 和 `layout`。每项说明含义、下一步检查、组件边界及人工维护的源码与文档路径。组件只说明应该检查哪里，不代表根因已经确认。

## Basic 操作

| 输入 | 作用 |
|---|---|
| `/help` | 简明 Basic 命令。 |
| `/help tasks` | 完整的原有任务控制命令。 |
| `/help viewer` | 本地 Viewer 帮助，也支持其它主题 ID。 |
| `/help viewer --language zh-CN` | 中文主题说明。 |
| `/status` | 选中 main 时查看会话身份、模型、焦点和轮次状态；选中子任务时保持原有子任务状态行为。 |
| `/history` | 无论当前选中哪个子任务，均查看最近 10 条**主会话**消息。 |
| `/clear` | 仅清除交互终端的显示，保留已登记历史。 |
| `/current`、`/switch ID`、`/switch main` | 原有焦点控制。 |
| `/resume`、`/rollback`、`/task ID ...` | 原有 owner 与 checkpoint 控制，详见 `/help tasks`。 |
| `/quit`、`/exit`、EOF | 离开 Basic，不停止子任务。 |
| `//tmp/file is relevant` | 发送原文 `/tmp/file is relevant`，只去掉第一层斜杠。 |

未知斜杠命令在本地拒绝，避免误发给模型。原文需要以 `/` 开头时使用 `//`。单次运行 `--prompt` 保留原有输出和原文消息行为。

在输入提示处，**Ctrl+C 取消当前输入而不退出会话**：显示退出提示，然后继续接受输入。任务执行期间沿用原有中断、checkpoint 行为；帮助不会触发恢复或重新解释恢复状态。

交互终端中，行末反斜杠表示同一消息续行。管道输入保持原有逐行协议，包括保留行末反斜杠。Python readline 可用时，Tab 补全 Basic 命令与帮助主题，Up/Down 回看本进程输入；退出时恢复此前的 readline 历史及补全器，不新建第二份历史文件。

颜色和清屏需要合适的终端能力；`NO_COLOR` 与 `TERM=dumb` 保留纯文本输出。显示模型、路径、历史及焦点时转义终端控制字符，普通多行消息仍可阅读。这是展示边界的改进，不代表全部历史 CLI 错误或模型输出路径都已完成过滤。

## Viewer 如何查看与呼叫帮助

点击顶栏“帮助与排查”或在非输入位置按 `?`。帮助可按主题搜索，随界面切换中英文；关闭后焦点回到原按钮。“解释当前视图”、选中对象与读取错误处的解决办法会打开对应解释。图形主模块加载失败时，独立帮助仍可使用；JavaScript 整体不可用时，可展开页面底部的原生“离线帮助”。

四个模式回答不同问题：简化概览看 agent 职责关系，详细流程看细分结构，Petri 网看精确 place/transition/token/arc，执行列表看已披露的实际 firing。结构图不证明所有路径已执行，未提供也不等于零。“比较与溯源”展开更多操作，当前来源和警告始终保留。大图缩得太小时，用名称或 ID 搜索并“定位”查看节点；布局失败后可切换执行列表，但缺失主程序资产时仍需离线帮助或终端入口。

自动刷新或回放在页面隐藏时暂停；手动关闭自动刷新也只影响观察。历史位置、比较、过滤和回放都不会执行、暂停或恢复真实任务。

## 本机端口什么时候关闭

```bash
rpnh net --run /path/to/existing-run --view --no-open --session-timeout 600
```

默认仅绑定 literal loopback，启动后 **3600 秒**关闭 listener；刷新不会续期。`--session-timeout 600` 改为十分钟，`--session-timeout 0` 显式选择持续监听。使用启动终端的 Ctrl+C 关闭自己启动的 viewer。关闭浏览器、切换标签或暂停刷新**不会关闭服务端端口**；到期后如需继续观察，从终端重新启动。不要通过扩大绑定或关闭 Host/Origin 校验来修复连接问题。

默认最多 16 个处理连接；过载返回 503。请求有 5 秒 socket/连接期限，浏览器读取期限为 15 秒；响应上限为 16 MiB。布局队列最多 4 项、每项最多 20,000 个节点与弧、执行期限 15 秒；超过预算会说明问题，不静默删图。限制保护传输与展示，不是可信 provider 的 CPU/内存隔离，也不能强制取消已经运行的 Python provider。当前服务不是公网认证网关。

## Viewer helper 与内部问题边界

Viewer 和 CLI 共用 `cpn/frontend/static/help-catalog.json`。Viewer helper 可解释控件，并区分传输、展示资产与布局、读取权限、Registry、恢复及 provider 问题。浏览器资产无法加载时仍可用 Basic 帮助。实际浏览器操作见 [Viewer 指南](viewer_ZH.md)，owner 与运行时边界见[故障处理指南](troubleshooting_ZH.md)。

helper 提供检查步骤，不自动修复。不能仅为恢复画面而清除 owner 锁、修改历史 Registry 身份、更换已保存的精确模型或重做效果未知的外部操作。敏感证据应私下保留，只分享审核过的最小诊断信息。连接或读取失败在检查对应边界前，根因仍未确认。

源码：`cpn/rpnh/basic_terminal.py`、`cpn/rpnh/help_catalog.py`、`cpn/rpnh_cli.py`、`cpn/frontend/static/help-catalog.json`。

当前项目 owner 的 `viewer-open` 在明确再次请求时重开已到期的缓存 listener；不会因浏览器刷新续期。已有 comparison-context POST 保留为有界只读选择请求，不是写入接口；其它请求体拒绝。
