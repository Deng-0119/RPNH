---
name: rpnh-petrinet-viewer
description: "通过只读 PetriNet 看板查看当前和历史 Registry run。"
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: viewer.md
  revision: "2026-10-04.5"
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
`Origin` 精确指向该监听器的请求。连接相互独立，每个已接受 socket 都具有有限 I/O
超时，因此一个局部 loopback client 不会无限阻塞其它 Viewer 请求。该超时不是总请求
deadline，也不限制连接数或线程数。这一边界不会给 Registry 执行或 provider 调用增加
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

选择 Overview Agent 会显示其声明 `source_ids` 中的每个真实 transition、各成员已披露的
firing 行、前后 Agent 和 **Open full Petri net**。显示名称相同不会合并 Agent。
画布卡片汇总全部成员的记录，包括 rework 和并发实例；代表成员已结算，不会遮住其它成员的
未结算或未知状态。

计数只针对所选帧内这些成员的已披露范围。缺少 runtime 或披露不完整时，总数保持未知，
不会当作零。明确提供的空列表会显示为空；只有本帧范围披露完整时才显示零总数。
同一成员中完整 firing 引用及已披露事实均相同的行会去重；同一引用出现不同事实、成员
关联冲突，或同一引用出现在多个成员时（包括同帧其它 Agent 卡片上的成员），会在导航前
明确标为不一致并禁用精确导航。每张卡仍只显示、计数自己声明的成员。

选择成员可打开它原本 Petri transition 的 **Executions** 页签，不预选 firing；选择具体
firing 会打开该页签，并准确展开完整引用对应的记录。**Executions** 表格使用相同导航。
简短版本标签仅供展示，每一行可查看完整引用。legacy 行若缺少完整引用或足够可比较的
task/run/net 身份，仍显示已披露内容，但禁用精确导航控件。

在 Overview → PetriNet → Overview 间切换会保留原始成员和所选 firing。已接受的同网刷新
即使观察 head 前进，只要仍披露同一成员的相同完整目标，就保留选择。目标不再披露、
出现不一致或来源范围改变时，会明确提示并清除选择；不会替换成同名或最新实例。
这个展示选择不授予执行权限或跨来源权威。

其它图视图的详情栏提供 **About**、**Executions**、**Evidence**。Evidence 显示已披露
身份、binding 和 checkpoint；标准 viewer 不公开 prompt、配置正文或资源正文。

库所的 **Executions** 页签为所选 checkpoint 中的每个 token 保留独立卡片。每张卡以完整
纯文本 JSON 显示该 token 记录的精确 `resource_ref`，保留 `resource_id` 与
`resource_version_id`，不合并 token 或资源版本。显式 `resource_ref: null` 表示该
token 记录没有资源引用；旧响应缺少字段时明确标为**未提供**，不按 null 解释。这一披露
适用于同一 Registry 的 live 和所选历史 checkpoint；不读取资源名称或正文，不用
`work_resource_ref` 替代，也不推断 grant 或 delivery。

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

## 单帧观察模型（开发边界）

`dashboard-model.mjs` 提供两个纯只读 helper：`observationContext(frame)` 与
`observationCoverage(frame)`。两者只接受 `normalizeFrame` 的输出，包括旧
`rpnh/net_view/v1` 的 fallback；不修改输入、net node 或 edge。只读的**本帧观察上下文**
卡片在 Detailed flow、PetriNet 与 Executions 中消费该模型；Overview 与其它技术信息
一样隐藏该卡片。没有新增 HTTP 响应 schema、Registry 记录或 reader 服务。
当顶层 source 与 `net.source` 都提供 mode、捕获 writer epoch 或 task ID 时，helper
会拒绝对应字段冲突；缺失的可选提示继续兼容，不从另一层补齐。既有 normalizer 与 frame
接纳保持不变。上下文生成失败时，仅卡片清除旧内容并显示**上下文不可用**；主画面、时间轴
与错误栏沿用既有行为，不伪造 `read_failed` coverage 声明。普通请求失败时保留最后完整
frame 及其卡片。backend 无须在 `net.source` 再重复顶层 task ID。

上下文把 live 映射为 `current`，history 映射为 `canonical-as-of`。唯一的
`source_cuts` 项原样保留 exact `net_ref` 与 `checkpoint_ref`，不压平身份字段。
`cut.head_ordinal` 是所选观察边界；`checkpoint_selector` 保留既有 `position.cursor`
提示，旧 fallback 中该提示等于 head，并不证明存在已保存 checkpoint。
`observed_capture.latest_head_ordinal` 单独保存帧报告的最新 head。source-cut 的
`cursor` 专指分页；当前帧没有历史分页，所以它保持 null，不能与 checkpoint 选择互换。

卡片分别显示这三个位置，最新 head 只取当前帧的报告值，不取历史轮询积累的值。缺失值显示
**未知**，已证实的零仍显示 `0`。原生**精确引用（完整 JSON）**折叠区以纯文本保留完整
net/checkpoint 引用，不缩短身份、不翻译引用值、不生成链接；展开状态跨刷新与语言切换保留。
这一首片不显示 epoch，不新增请求或运行态写入。

历史模式中 `cut.writer_fencing_epoch` 为 null：既有 source epoch 属于当前捕获，
只保留在 `observed_capture`，不证明历史 writer epoch，也不是 checkpoint 的 marking
epoch。`initial_configured` fallback 没有 Registry cut、selector 或捕获 head，
其 firing coverage 为 `unsupported`。未提供的 Registry 位置保持 null。

`local_source` 中的 mode、task ID、run 目录仅为本地展示提示，不建立跨源身份或权限。
SourcePointer 字段、`source_set`、`manifest_version`、访问路径、披露证据、查询范围与
分页 cursor 均保持 null。这些 helper 不能证明跨源完整，也不能把独立读取拼成一致观察。

Coverage 只涵盖当前帧明确列出的 transition ID 上已披露的 firing 行，并保留原 firing
披露声明。只有声明与模式匹配（`current_observations` 或 `canonical_only`）、提供观察
head 和 net 引用、且每个已列 transition 都有 firing 数组时才为 `complete`；它仅说明
这一帧的已披露范围完整，不表示所有真实执行或完整 lifecycle 证据可用。只有这种已知完整
范围才允许报零。部分范围可报告已加载的正数；未证实的空计数与未知总数保持 null。显式
缺证或不支持的披露也保持 null 计数，不从 head 距离或摘要提示估算总数，不补造运行
manifest、readiness、adoption 或执行证据。

## 显式选择跨网版本的已保存检查点

默认 v1 dashboard/history API 及 current-net 时间轴保持不变。时间轴报告网版本边界后，
Viewer 用当前帧的 exact 已保存 selector 探测独立能力；用户点击**前一网段**后才请求
返回的精确前驱。该页显示一个已保存 checkpoint，使用该网持久化 compiler declaration、
boundaries、exact bindings 与 marking，不复用当前图，也不把旧段混入 v1 时间轴。

只读端点为 `GET /api/v2/checkpoint-view`。三个参数均必填且仅能出现一次：`net_ref`
与 `checkpoint_ref` 是 URL 编码 JSON，只含 `entity_type`、`logical_id`、`version_id`，
类型分别必须为 `net_instance/v1` 与 `marking_checkpoint/v1`；`cut` 是该 checkpoint
完整发布事务的正整数十进制 ordinal。未知、重复、缺失或不匹配参数均失败，没有隐式
latest 或 v1 fallback。`rpnh/checkpoint_view/v1` 封套包含规范 selector、
`rpnh/dashboard/v1` 历史 frame、独立捕获 head/writer epoch、采用证据及前一网段导航
coverage。先校验新封套，再把其 frame 交给既有 normalizer。

选择范围限于当前捕获 canonical checkpoint 可达的 predecessor 链；
`--max-checkpoints` 限制遍历，达到上限明确报告 partial，不全库补搜不可达目标。
所选 frame 只使用 exact cut 的 canonical 对象／事件、完整事务／publication 证据、
一致的 descriptor 字节及 exact resource provenance。持久 compiler wire 走固定
离线 loader，不加载 HOST 代码，不调用新的编译 callback，不暴露任意资源正文。
有限材料 inventory 同时核验 owner/principal 的 canonical exact refs、effective task
权限、plan/node 归属、node role 与 operation spec，以及输入资源和 schema refs 在
selected root 集合中的对应关系；registered port/place/cardinality/outcome 元组须与
持久 wire 一致。唯一新增内部正文读取限于该 wire 实际端口使用、属于 selected root
且同 cut canonical 的 schema resource；核验已登记 content type/字节数及完整 JSON
与 wire schema 的类型敏感一致性。业务 token/input resource 正文不读取、不返回。
捕获 head 或 writer epoch 变化时拒绝混合读取。

Checkpoint cut、结构网身份和捕获 H/E 是三个独立时钟。例如 checkpoint 在 75 完整
发布、该网 adoption 在 77 才提交，选择 75 显示的是 cut75 已保存的网和 marking，
不会跳到 77，也不会复制较晚捕获的采用状态。采用状态分别为此 cut 当前采用、此前
曾采用、查询完整但无证据，以及证据未知／不完整。该保存 selector 不支持额外选择
较晚 adoption/activity cut。

切段停止播放、延迟拖动与轮询，清除 node/firing 选择和 viewport 记忆，不播放跨网
变化动画。**返回保留视图 #H** 恢复之前的完整 frame；即使原 live frame 的捕获 H
晚于其 checkpoint，也不会请求 latest 冒充原位置。恢复后明确标为暂停保留捕获，
refresh、轮询和时间轴控件保持禁用；只有显式**返回实时**才重新读取当前态。
失败或过期响应保留最后完整画面；v2 404/501 不降级到 `/api/v1/net`。

本片未提供历史 firing/activity 行、provisional 回放、跨网 delta 或旧网段的 checkpoint
分页。Firing coverage 为 `not_provided`，不补空 runtime 数组，也不声称零执行。
验收使用真实保留的两网 fixture，经实际只读 reader／GET handler 进入 Node VM/DOM
stub 中的生产 app/panels。样本两网拓扑相同，declaration、binding 与 token occurrence
不同，并共享一个 resource pair；样本没有 firing。合成竞态／负例测试单独标注。
本次未验浏览器、ELK 布局／绘制、密集历史分页及真实移动 writer。

### 显式查看已记录 firing 活动

在真实 transition 的 **Executions / 执行**详情点击**查看已记录活动**。
这个独立只读观察仅列 `firing_admitted/v1`、`transition_firing_started/v1`、
`operation_execution_started/v1`。主图保留所选检查点 C；活动单独显示捕获的
证据 head H 与 writer epoch E。每行分别显示记录 ordinal 和完整事务提交位置。
PROVISIONAL 没有正式可见位置。已记录 operation start 仅说明已加载开始证据，
不能证明进程存活、完成、业务结果或结算。

**加载更多活动**固定完整 task/run/net/checkpoint/member 范围与 H/E；H/E
变化时保留旧观察并停止翻页。**显式刷新活动**完整验证新的第一页后原子替换旧
观察，失败保留旧 H/E；来源或披露资格改变则清除。关闭、切换选择、检查点导航、
返回实时会丢弃活动。语言切换保留按完整 firing 身份展开的卡且不请求新数据。
活动卡不会改变主图 runtime、Agent 计数、token、时间线或动画；Agent 成员导航
可自然进入相同真实 transition，但 Agent 概览不聚合这些活动。

可选 `GET /api/v2/firing-activity` 使用 `rpnh/firing_activity/v1`；HEAD
执行相同验证但不返回 body。必选参数是精确 `net_ref`、`checkpoint_ref`、完整
检查点提交正整数 `cut`、含 1–64 个不重复所选成员的 JSON `transition_ids`
数组。`limit` 为 1–100（默认 50），`cursor` 只能使用返回的不透明续页 token。
不接受任意过去 evidence head 或资源 body。查询/cursor 预算为 16 KiB/8 KiB。
每页在一个显式只读 SQLite snapshot 内验证，然后用新鲜 H/E 与来源绑定 guard
检查。每事务最多验证 2048 个 envelope、每 descriptor 256 KiB、每页验证材料
8 MiB，activity reader deadline 为两秒。超预算失败关闭，不改变业务合法性；
检查点闭包沿用既有读取边界。

错误为 `400 invalid_query`、`409 stale_observation`、`403 access_changed`、
`501 unsupported`、`503 read_failed`。只有从第一页到终页的连续合法链才说明
该范围三类事件完整，单独终页不证明此前已加载。已加载事件数和独立 firing 数
分开；lifecycle/全部活动总数始终未知。完成、结果、successor checkpoint、
结算与 delta 始终 `not_provided`。本功能只读一个绑定 Registry 的 metadata，
不提供跨来源权限或任意过去 provisional 状态重建。

显式加载活动会暂停播放和主图自动刷新，避免后续自动轮询丢弃所选观察。界面明确标注主图观察已保留。关闭活动后按原自动刷新设置恢复；返回实时会清除活动并沿用既有实时读取路径。

活动字节预算在同一 read snapshot 内，先用 `LENGTH(CAST(... AS BLOB))`
逐列检查实际需要读取的候选事件、完整事务 envelope、outbox JSON、descriptor
和身份/成员行，再物化正文。每页预算包括传输的 UTF-8/BLOB 值、每行固定32字节、
每列固定8字节、有界 H/E 控制材料，以及每次 descriptor 读取缓冲（登记size再加
1字节用于检测变化）。这是验证材料上限，不是精确 Python heap 上限。每次 SQL
物化都计费，不同查询再次读取同一事件也计入；事务和 exact descriptor 缓存命中
则既不重读，也不重复计费。descriptor 正文保持既有 exact locator/envelope
校验，先检查文件大小，最多读取登记size+1字节。每条事件包括缓存 firing 的后续
事件，仍必须与已验证 descriptor 的完整 firing 引用一致。

### exact token 资源登记卡

在库所的 token 列表中，“查看资源登记”只请求已显示 exact token 在所选保存检查点
引用的完整资源 pair。opt-in GET/HEAD `/api/v2/checkpoint-view` 新增可选 JSON
`token_resource` 参数，包含 `token_ref`、`resource_ref`、`expected_task_id` 和
`expected_capture`（head ordinal H、writer fencing epoch E）。原 net/checkpoint/cut C
参数仍必填。缺失、null 或不完整资源引用不能打开卡片；不使用 work ref、同名、路径或
latest resolver 补全。旧 v1 和不带 target 的 v2 响应不变；旧 provider 可不提供详情。

独立版本 `token_resource_metadata` 仅投影一个 exact token occurrence：task/run/net/
checkpoint refs、C 与 H/E、token/pair、place 与 active-in-checkpoint 标记，登记字节
大小、媒体类型、可空 schema 标识，以及登记 publication event、记录 ordinal、所属
完整 transaction commit ordinal。schema 标识仅作普通文本，null 表示未提供。同资源
在其他检查点仍是不同 occurrence。登记位置不代表首次正式可见、读取、消费、权限、
交付或发布当时的 writer epoch。

本卡在既有 checkpoint reader 之外增加零正文读取。既有 reader 仍可能为所选编译
wire 和 port-schema 读取正文；若目标是这些资源的别名，不能宣称整请求不读正文。
本卡未进行内容完整性验证：登记大小不是实测大小，`actual_verified_byte_size` 为 null，
不构造登记或内容 hash。不披露 summary、descriptors、provenance、producer dossier、
locator 或 payload。范围/capture 不匹配时拒绝；披露资格改变会清除旧详情。过期响应只可
保留同范围已有详情并明确警告。关闭或切换 token/库所/检查点会作废迟到响应，不改变主图
或 firing activity。

客户端从第一份合法登记卡响应取得 exact task/run 身份，并为同一完整请求范围保留锚；
披露资格改变清除详情后仍保留该锚。关闭或切换范围才清除身份锚。所有标识字段必须为
字符串，拒绝数组等可强制转换的值。opt-in HTTP 响应边界在披露前交叉核验 frame
两层 source、所选 net/checkpoint/C、capture H/E 与 token 的库所位置。
