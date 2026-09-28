---
name: rpnh-petrinet-viewer
description: "通过只读 PetriNet 看板查看当前和历史 Registry run。"
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: viewer.md
  revision: "2026-09-26.3"
  status: source-reviewed-pre-release
---

[English](viewer.md) | [中文](viewer_ZH.md)

# 只读 PetriNet 看板

看板只投影一个已有 Registry run，不获取 writer 权限，不执行 transition、不恢复任务、不创建
结果，也不修改 PetriNet。终端文本与浏览器读取同一份持久化 run 证据。

## 五分钟串行与并行教程

先在源码仓库创建两个离线 run。它们使用确定性 local-process 替身，不调用 provider：

```bash
DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rpnh-viewer-tour.XXXXXX")"
python -m examples.workflow_patterns.run \
  --scenario serial --run-dir "$DEMO_ROOT/serial"
python -m examples.workflow_patterns.run \
  --scenario parallel --run-dir "$DEMO_ROOT/parallel"
rpnh net --run "$DEMO_ROOT/serial" --view --no-open
```

打开命令输出的 loopback 地址。查看串行 run 后，只停止自己启动的 viewer，再打开并行 run：

```bash
rpnh net --run "$DEMO_ROOT/parallel" --view --no-open
```

预期 Agent 层结构为：

```text
串行： intake -> work -> deliver

并行： prepare -> facts --\
                -> risks ---+-> join
```

实际并行 Overview 只保留 Agent 后继关系：

![实际并行 Agent 概览](../../examples/workflow_patterns/assets/parallel-overview.png)

把同一个已完成 run 切换到 PetriNet 后，可以看到实现 fan-out 和全输入 join 的 place 与弧：

![实际并行 PetriNet 视图](../../examples/workflow_patterns/assets/parallel-petrinet.png)

解释精确 place 和 arc 前先切换到 PetriNet。ASCII 只描述 Agent 职责关系，Petri 投影才是
权威。[工作流模式源码说明](../../examples/workflow_patterns/README_ZH.md)还提供文档任务和
六阶段时间轴案例。

## 在终端查看 run

```bash
rpnh net --run /absolute/path/to/run
rpnh net --run /absolute/path/to/run --format json
rpnh net --run /absolute/path/to/run --show-resources
rpnh net --run /absolute/path/to/run --resources-only
rpnh net --run /absolute/path/to/run --node NODE_ID
rpnh net --run /absolute/path/to/run --output projection.json
```

默认投影隐藏 resource place；`--show-resources` 只增加真实 net 声明的 resource node；
`--resources-only` 只保留这些资源及相关连接，空结果表示该 net 没有声明资源。
`--node` 选择精确节点，`--output` 写出所选终端投影。

## 打开看板

```bash
rpnh net --run /absolute/path/to/run --view
rpnh net --run /absolute/path/to/run --view --no-open
rpnh net --run /absolute/path/to/run --view --no-open \
  --max-checkpoints 4096 --max-firings 5000
```

`--no-open` 只输出本地地址，不自动打开浏览器。两个正数上限默认分别为 2048 个 canonical
checkpoint 和 2000 条 firing。查看已知大型 run 时提高它们，不需要改源码。`--host` 只接受
显式 loopback IP，`--port` 范围为 0–65535。view 模式有意拒绝 `--resources-only`、
`--node`、`--output` 和非文本 `--format`；这些筛选使用终端投影。

Viewer 是独立的本地只读展示服务。它接受 IPv4／IPv6 loopback 字面地址
（`127.0.0.1` 与 `::1`），为 IPv6 输出带方括号的 URL，并且只服务 `Host` 与可选
`Origin` 精确指向该监听器的请求。这一边界不会给 Registry 执行或 provider 调用增加
HTTP transport；Codex、OpenCode 与 DSH 继续使用各自的适配传输。

## 认识页面区域

| 区域 | 含义 |
|---|---|
| 顶栏 | 所选 run 来源、界面语言、只读提示、阅读指南和全屏控制。 |
| 运行摘要 | workflow 标题，以及 Overview 之外的步骤、未结算、已结算、有效 token 计数。破折号表示未提供，不是零。 |
| 视图工具栏 | 信息层级、搜索、资源、活动定位、刷新和实时轮询。 |
| 画布 | 当前图投影。选择节点或弧会打开右侧详情。 |
| 详情栏 | 所选对象的说明、已披露执行记录和精确技术证据。 |
| 时间轴 | 当前网版本的 canonical checkpoint；只改变观察位置。 |
| 页脚 | viewer 健康状态、所选观察时间和可用的 checkpoint 引用。 |

## 四种信息层级

| 视图 | 显示内容 | 有意隐藏的内容 |
|---|---|---|
| Overview | 明确分类的 Agent 与结构性 next-Agent 关系。 | 工具、条件、place 和资源。箭头不证明实际 firing 顺序。 |
| Detailed flow | 步骤卡片、join、条件和资源；只折叠普通一对一交接 place。 | 不静默折叠多对一 join、资源或条件。 |
| PetriNet | 当前 adopted net 投影出的每个 place、transition 和 arc。 | resource node 在启用 Resources 前保持隐藏。 |
| Executions | 每个已披露 firing 一行，含 attempt、记录状态、业务 outcome 和准入位置。 | 缺少执行披露不解释为执行次数为零。 |

选择 Overview Agent 会显示前后 Agent 和 **Open full Petri net**。其它图视图的详情栏提供
**About**、**Executions**、**Evidence**。Evidence 显示已披露身份、binding 和 checkpoint；
标准 viewer 不公开 prompt、配置正文或资源正文。

## 控件与键盘操作

| 控件 | 作用 |
|---|---|
| Language | 在本地切换中英文标签。 |
| Guide | 打开页面内简要阅读指南。 |
| Full screen | 进入或退出浏览器全屏。 |
| Overview / Detailed flow / PetriNet / Executions | 只切换读侧投影。 |
| Search + Locate | 淡化不匹配内容并居中下一个匹配节点；在 Search 中按 Enter 等同于 Locate。 |
| Resources | 显示／隐藏已声明资源依赖；Overview 是 Agent-only，因此不显示该按钮。 |
| Locate activity | 优先居中存在未结算记录的节点；若没有，Overview 选择首个 Agent，其它图视图在可用时定位有 token 的 place。 |
| Refresh | 重新读取当前 live 或 historical 观察位置。 |
| Live | 启用时每 2.5 秒轮询 live 位置；不会启动任务。 |
| Line bridges | 在线路相交但不连接处绘制跳线；只有节点端点才表示连接。 |
| `−` / `＋` | 以画布中心缩小／放大；滚轮以指针位置缩放。 |
| Fit to view | 将完整当前投影适配到画布。 |
| Reset focus | 清除搜索和选择、关闭详情并适配全图。 |
| Minimap | 显示／隐藏缩略图；点击缩略图可重新居中。 |
| 详情栏 `×` | 清除当前选择。 |
| `● Live position` | 离开历史位置，回到最新观察。 |
| `◀` / `▶` | 选择已加载的前一个／后一个 canonical checkpoint。 |
| Play / Pause、速度、滑块 | 以 0.5×、1× 或 2× 回放已加载 checkpoint，不执行任务。 |
| Earlier records | 在存在更早分页时继续加载一页。 |
| Animate changes | 示意相邻 canonical settlement 变化；它不是执行事件。 |

用主指针拖动画布空白处可平移。聚焦节点或弧后，可按 **Enter** 或 **Space** 选择。
当前没有其它全局 viewer 快捷键。

## 图形图例

| 符号或样式 | 含义 |
|---|---|
| 蓝色矩形卡片 | transition 或 Agent 步骤。 |
| 灰蓝色圆形 | 普通数据或同步 place。中心数字是有效 token 数；`?` 表示不可用。 |
| 绿色边框 | 声明的任务入口／边界 place；浅绿色填充表示存在有效 token。 |
| 紫色边框 | 声明的输出 place；它是可能出口，不是完成证据。 |
| 琥珀色虚线圆形 | 声明的 resource place；仅在显示资源时出现。 |
| 淡黄色 transition 填充 | 至少一条已披露 firing 尚未结算；不能证明进程仍存活。 |
| 灰蓝色实线箭头 | 普通 consume 或 produce arc。 |
| 紫色虚线空心箭头 | read arc：观察 token 而不消费。 |
| 红色短虚线双箭头 | reset arc。 |
| 琥珀色长虚线箭头 | resource 或 variable-resource 依赖。 |
| 加粗轮廓 | 当前选择；淡化节点／弧位于搜索或选择焦点之外。 |
| 线路跳线 | 两条线交叉但不连接。 |
| 移动绿点 | 相邻已保存 checkpoint 间的示意，不是新增 token 事实。 |

颜色和形状只辅助阅读；精确 ID、mode、outcome 和引用应在详情栏或终端 JSON 中核对。

## 状态与完成边界

`settled` 是 Registry 记录状态，不自动代表业务答案成功；`started` 不证明进程仍存活；
`outcome_unknown` 与缺失数据不能解释为零。执行详情可显示 admitted、
dispatch-authorized、started、returned/failed-unsettled、outcome-unknown、settled 和
invalidated。

浏览器会标注声明的输出 place，但不会虚构 terminal-success 徽标。任务是否完成必须通过
对应任务／会话的 status、result 命令核对 Registry terminal evidence 与最终登记结果。
仅凭进程退出不足以判定完成。

## 时间轴与历史 workflow

时间轴回放当前 adopted net 版本的 canonical checkpoint，不重建 provisional／unsettled
中间态。选择历史位置后，即使任务继续运行，轮询也不会强制跳回 live。可用案例库的
`long_process` 查看更密集的完成历史。

查看旧 workflow 时，把它的精确 run 目录传给 `--run`。主会话 Registry 可以链接子
workflow Registry，但每个子 Registry 保持独立；查看或回退主会话不会删除子 Registry。

Codex selector 使用 `--root`、`--thread-id` 和 `--turn` 或 `--task-id`；DSH selector
使用 `--root`、`--session-id` 和 `--turn` 或 `--request-id`。它们的 `--view` 也支持
`--presentation`、`--show-resources`、`--no-open`、`--port`、`--max-checkpoints` 与
`--max-firings`；`--describe` 和 `--json` 不启动 HTTP。presentation metadata 只改变标签，
不修改执行身份或 prompt。

捆绑 JavaScript 库由 lockfile 固定的本地包构建。看板不加载 CDN 脚本，也不调用 provider。
