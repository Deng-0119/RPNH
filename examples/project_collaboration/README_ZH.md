# 复制、共创并复用同一 Agent 项目

[English](README.md) · [操作指南](../../docs/guides/project-entry_ZH.md)

从完整项目开始，由两个受控贡献者修改，解决真实冲突并形成双 parent 修订 **M**，在业务执行前采用结果。随后下载精确 M，在独立接收方导入并执行同一原任务。包包含定义、输入 fixture、环境要求、原任务材料、验收器、来源和许可证；裸 Module 不能提供这个完整起点。

业务 PN 的两个 stock `rpnh/default-agent/v1` agent 是 **implementer** 和 **reviewer**：前者用原 workspace 工具编写、执行数据合并代码，后者检查实现、完整 instruction 和原断言。领域算法由 agent 编写。下级执行 PN 在同一 Registry 内归属于精确业务 invocation/firing，处理文件物化、workspace finalization 等 harness 机制；其 token、firing 和 checkpoint 与业务 marking 分离，AgentLoop 日志本身不是执行 PN。

在已记录旅程中，两端独立项目都由 stock AgentLoop agent 完成原任务；仅字面量适配输出路径后，各通过三个原断言。原验收器可见，未运行上游容器／test.sh。17 张原始 Viewer 图呈现两端完成态、业务执行前的协作历史与两层 PN。

## 实际 Viewer：项目与两层 PN

2026-10-13 原生检查窗口的实际官方 Viewer 图。 [捕获来源](figures/provenance.json)记录原图字节与观察。

打开图片原尺寸查看较小引用；同名 JSON 保留精确观察。

![source 完成后的 agent 简要视图：implementer → reviewer，两个 agent、一次交接。](figures/source-overview.png)

source 完成后的 agent 简要视图：implementer → reviewer，两个 agent、一次交接。 source-05 · cut 3474 · selector 3401 · [精确引用、面板文字与位置](figures/source-overview.json)。

## 材料与任务

原任务为 Terminal-Bench 2 [multi-source-data-merger](https://github.com/harbor-framework/terminal-bench-2/tree/2fd12b88aafdd04a52c298e3940bcb189f9766d6/multi-source-data-merger)，固定提交 `2fd12b88aafdd04a52c298e3940bcb189f9766d6`。[SOURCES.json](materials/SOURCES.json) 记录十份原文件及字节来源。[原 instruction](materials/upstream/multi-source-data-merger/instruction.md)、[验收器](materials/upstream/multi-source-data-merger/tests/test_outputs.py)、[test.sh](materials/upstream/multi-source-data-merger/tests/test.sh) 和完整 [Apache-2.0 许可证](materials/upstream/LICENSE) 均保留；RPNH 衍生代码使用 [LICENSE-RPNH](LICENSE-RPNH)。

Agent 必须读取 JSON、CSV、Parquet，统一同义字段，按 user ID 合并，并依 A > B > C 优先级生成 `merged_users.parquet` 和 `conflicts.json`；后者列出冲突值和最终选择。实现代码和 reviewer 报告也须交付。数据来源优先级是任务规则，不能代替协作冲突解决。

[scenario/generated](scenario/generated/) 提供完整 seed、精确 lock、初始 TEXT fixture 及 A/B/A-resolved 提案定义。原二进制 Parquet 保留在 materials，包内使用明确可逆的 base64 封装。不提供上游 solution 或预制 merger。

## 环境与源码绑定

包 **0.1.1** 使用 Python >=3.11,<3.14、pandas **3.0.3**、pyarrow **24.0.0** 和 `rpnh-harness>=0.1.0rc2`。使用绑定权威产品 checkout 的任务内干净环境。本轮 WSL 验证环境使用 Python 3.13.12 和上述当前数据依赖。现有库满足所选包时继续使用，不因历史 pin 盲目降级已有环境。

冻结上游 Dockerfile 仍声明 Python 3.11/pandas 2.2.3/pyarrow 17.0.0；上游 `test.sh` 另声明 Python 3.13/pandas 2.3.3/pyarrow 22.0.0 及自己的测试依赖。两份原文件及原导入包均保留。0.1.1 是明确披露的兼容变体，不是严格上游容器复现。当前依赖已通过原输入读取和 Parquet/CSV 往返检查；业务正确性仍须独立验产物。

harness 最低版本或 metadata 通过不能证明“RPNH 最新”。运行前记录解释器、实际 `cpn` 导入位置、权威 checkout、精确测试源码/修订及本地改动，并单独核对远端 `origin/main`；源码来源与包版本 metadata 分开记录。driver 在运行前分别检查每个项目自身包的环境 metadata；原启动仍须建立获准 profile 和 live owner dispatcher，内核隔离仍须由原 runtime 实验验证。

## 运行完整流程

在权威 checkout 先查看入口：

```bash
python examples/project_collaboration/scenario/operate_project.py --help
```

使用已准备的任务内环境解释器。以下变量是操作者本地选择：`PROJECT_RUN_ROOT` 为可写 workspace 内全新、较短的目录，`APPROVED_PROFILE` 为既有获准 profile。不要公开私有 selector 或凭据内容。在已有本任务模型/预算明确授权后运行：

```bash
python examples/project_collaboration/scenario/operate_project.py \
  --root "$PROJECT_RUN_ROOT" \
  --package examples/project_collaboration/scenario/generated/seed-project.zip \
  --definitions examples/project_collaboration/scenario/generated \
  --existing-profile "$APPROVED_PROFILE" \
  --conflict-choice local \
  --authorized-model-run
```

授权标志只表达调用意图，不创建凭据、安装许可或模型预算。沿用既有获准凭据/账号，无需新建账号。[操作指南](../../docs/guides/project-entry_ZH.md) 说明普通 WSL 入口、只在进程内转发凭据变量名及恢复边界。全新流程不是已有运行的恢复命令。

1. **导入并建立权限。** 完整 seed 导入新 Registry 身份，创建自己的受保护 Branch，显式授予 scoped read 和邀请。受支持 stop/resume 保留原 actor/session，不复活已过期或已撤销的 reader。
2. **验证两方贡献。** 两个受控本地主体提交人工定义的 Module，经过真实确定性 validator operation → stock TEXT → delivery/proposal，保留输入、invocation、firing 和结果引用。受控贡献者不等于独立真人认证或作者模型。
3. **解决原冲突。** A 加强 implementer/reviewer 指令；B 修改同一 implementer atom 并将连通交接容量设为 1。首次 analyzer 暴露 divergent-atom/coupled-contract 冲突。A-resolved 显式吸收 B 容量，引用 B 精确 proposal/revision 和 A 的 `previous_proposal`；在 A-resolved/B 上重新分析，为所有冲突提交一致选择，形成真正双 parent M，并核验两方内容均保留。确定性 validator 校验定义，不编写业务答案。
4. **分开接纳与采用。** 受保护 Branch 接纳不改变 runtime，前后比对 net/checkpoint/marking。采用依据实际 runtime marking 和显式 token→place 映射，只以原 adoption receipt 为成功依据。
5. **业务前恢复内容。** 采用 M → 用 base 内容创建、审核、接纳并采用新后继 → 用 M 内容创建、审核、接纳并采用新后继。三次采用全部发生在业务 firing 前，历史完整保留；证明内容恢复后可执行，不证明终态 token 复活或外部效果回滚。
6. **下载精确 M 并独立接收。** 导出原双 parent M，不以之后单 parent 后继代替。receiver 从自己的下载包取得 fixture/environment/license 闭包，建立新 revision/element 身份及自身 owner 授权，不复制源 session、grant 或 Registry 数据库。
7. **两端执行并检查。** source 在最终已采用后继上运行，receiver 在独立导入的 M 上运行，均由两个 stock agent 完成原任务。收集已结算产物、实现、reviewer 报告、invocation/firing/执行证据、模型 attempt 及全部失败；原生 terminal 不等于评分通过。

## 产物、评分和 Viewer

driver 将原请求和 checkpoint 写入 `requests/`、`JOURNEY_RESULT.json`。成功收集后，`artifacts/source/` 与 `artifacts/receiver/` 分别包含已结算 workspace 文件和报告。`ORIGINAL_ASSERTIONS_PATH_ADAPTED.json` 记录可信适配器结果：先核冻结 grader 原字节，仅替换四处输出路径字符串，全部三个原测试函数保持断言。它须与真正执行原布局 `test.sh` 的成绩分开报告；若执行后者，还须记录依赖、CTRF/reward。grader 已可见，不称盲评；完整 instruction 审阅独立于三项测试成绩。

成功运行后，driver 请求实际官方 Viewer URL，保留 HOST 供检查。查看 source/receiver 的 agent 简要视图、完整 PN、真实贡献/冲突、M 的 parents、内容恢复/采用引用、资源以及所测 Viewer 提供的下级执行网。每张截图须绑定实际 revision/net/checkpoint 和 invocation/firing 引用。读取过期或撤权后须重新显式选择 scoped grant，打开 Viewer 不会授予权限。 历史 M/base 视图还需对应保留 runtime net 的精确 compiler-wire body；driver 会在每次成功采用后记录读取 plan，并在最终 runtime grant 显式合并同项目 scopes，详见操作指南。

**2026-10-13 原生检查窗口：** 原生人工输入验证、真实双 parent M、业务前三次采用、精确原 M 下载、身份不重叠的独立 receiver 导入已通过。首次登记凭据失败保留为 `NOT_SUBMITTED`，provider HTTP 请求为 0。原 checkpoint 恢复保留同三 Registry 和原失败/计数历史，创建新的 execution generation。真实无模型恢复定向回归一次通过（27.70s），此前两次 fixture 失败完整保留。

两端原生任务均已完成，独立完整 instruction 审阅**接受两端**。以下结果对应原 source/receiver 执行，不是替换 run command 的新运行。

| 已核验结果 | Source | Receiver |
| --- | --- | --- |
| 登记请求／模型响应 | 23／22 | 20／20 |
| 业务 firing | 3：原中断 firing 1 个、完成职责 2 个 | 完成职责 2 个 |
| 精确 scoped 产物导出 | 18 文件／47,815 字节 | 20 文件／52,089 字节 |
| 路径适配原断言 | 3/3 PASS | 3/3 PASS |
| 原 `test.sh` | NOT_RUN | NOT_RUN |
| 各自导出 workspace 内的 agent 实现 | `workspace/merger.py` | `solution/merger.py` |

共 **43 次登记请求，未超过授权上限 48**，响应共 42 次。source 保留原一次 `NOT_SUBMITTED` 凭据失败，该次 provider HTTP 请求为 0。上述是登记／响应计数，不推定尚未报告的 provider retry attempt 分母。恢复仅在持久化 checkpoint reentry 授权之后复用精确旧 command ID 和 launch receipt；变化的是 execution generation，不是 run command。轮询只观察原执行，原 HOST 持续运行。

两端原断言保持全部检查，仅披露四处 `/app` 输出路径字面量替换；grader 为 **VISIBLE**，不称盲评官方容器成绩。独立语义审阅核对三个原输入字节／摘要，确认两份实现均通用且没有硬编码答案。两端全部四行／20 单元格正确，恰有用户 101 的三项字段冲突（`name`、`email`、`created_date`）。两份 Parquet 字节一致，冲突 JSON 唯一区别是 source 多一个末尾换行。可选 status 的 `active`／`true` 归一化仍是已披露的指令歧义，不判缺陷。receiver 的 `check_out` 复算与原交付字节一致，不是修补原产物。

source 和 receiver 实现摘要不同。receiver 从**业务执行前**下载的精确原 M 独立导入后自行编写实现，包未复制 source 答案。两端实际 Registry 业务 PN 均为 **5 节点／6 弧、两个 agent 职责**。source 有三个 AgentLoop 实例（含原失败），receiver 有两个；loop 实例计数与下级执行网证据分开解释。

两端实际官方 Viewer 捕获窗口均完成，**报告缺项为 0**：source 捕获 101 张 PNG，receiver 捕获 28 张。两端简要视图均为两个 agent 节点／一条边，完整业务 PN 均为五节点／六弧且无裁切；sidebar 布局修正后真实 child 执行网图也完整适配。receiver execution API 返回 HTTP 200，关联真实文件物化／workspace-finalization 的 `map_ready`／sealed 证据。

source 通过原生 **Previous Net Segment** 连续两次导航，先查看已采用的 base 内容恢复，再查看原双 parent M 及其首次采用。M 两个 parents 和全部八个提供的历史引用均实际可见，返回原 completed 视图也已核验。在 owner grant 显式选择两个精确旧 compiler-wire body 后，检查的五个 source 历史／贡献 API 请求均返回 HTTP 200；原 Core／读取门不变。

本有界流程在 2026-10-13 原生检查窗口完成原生执行、原路径适配评分、完整 instruction 审阅和两端实际 Viewer 检查。首次浏览器尝试在 HTTP 前发生的 socket 路径过长失败保留。真实图及其精确观察引用见下方图像来源；模型登记总计仍为 43。

## 源端完成态与执行网

![source 完整业务 PN：五节点／六弧，原中断 firing 记录仍保留。](figures/source-petri.png)

source 完整业务 PN：五节点／六弧，原中断 firing 记录仍保留。 source-05 · cut 3474 · selector 3401 · [精确引用、面板文字与位置](figures/source-petri.json)。

![真实关联的文件物化执行 PN，map_ready 且 sealed；精确业务 parent 与 child 引用见同名证据。](figures/source-execution-materialization.png)

真实关联的文件物化执行 PN，map_ready 且 sealed；精确业务 parent 与 child 引用见同名证据。 source-05 · cut 3474 · selector 3401 · [精确引用、面板文字与位置](figures/source-execution-materialization.json)。

![真实关联的 workspace-finalization 执行 PN，有独立 checkpoint/token 及 map_ready／sealed 证据。](figures/source-execution-finalization.png)

真实关联的 workspace-finalization 执行 PN，有独立 checkpoint/token 及 map_ready／sealed 证据。 source-05 · cut 3474 · selector 3401 · [精确引用、面板文字与位置](figures/source-execution-finalization.json)。

![完成态输出 token 的资源登记：精确引用及登记元数据，不是 payload 正文展示。](figures/source-result-resource.png)

完成态输出 token 的资源登记：精确引用及登记元数据，不是 payload 正文展示。 source-05 · cut 3474 · selector 3401 · [精确引用、面板文字与位置](figures/source-result-resource.json)。


## 贡献、精确 M 与内容恢复

![User contributions 中按记录的提议者聚焦；展示受控主体，不证明真人作者身份。](figures/source-contributor-focus.png)

User contributions 中按记录的提议者聚焦；展示受控主体，不证明真人作者身份。 source-05 · cut 3474 · selector 3401 · [精确引用、面板文字与位置](figures/source-contributor-focus.json)。

![原 M 在历史 cut 574 的贡献观察，发生在业务执行前，不是 completed 状态。](figures/source-M-contributions.png)

原 M 在历史 cut 574 的贡献观察，发生在业务执行前，不是 completed 状态。 source-05 · cut 574 · selector 574 · [精确引用、面板文字与位置](figures/source-M-contributions.json)。

![保留的历史面板中实际可见的原双 parent M 精确引用。](figures/source-M-reference.png)

保留的历史面板中实际可见的原双 parent M 精确引用。 source-05 · cut 709 · selector 709 · [精确引用、面板文字与位置](figures/source-M-reference.json)。

![记录的元素变更证据中第一个精确 M parent 引用。](figures/source-M-parent-1.png)

记录的元素变更证据中第一个精确 M parent 引用。 source-05 · cut 574 · selector 574 · [精确引用、面板文字与位置](figures/source-M-parent-1.json)。

![记录的元素变更证据中第二个精确 M parent 引用。](figures/source-M-parent-2.png)

记录的元素变更证据中第二个精确 M parent 引用。 source-05 · cut 574 · selector 574 · [精确引用、面板文字与位置](figures/source-M-parent-2.json)。

![历史 selected-observation JSON 中实际可见的首次 M 采用回执精确版本。](figures/source-M-adoption.png)

历史 selected-observation JSON 中实际可见的首次 M 采用回执精确版本。 source-05 · cut 574 · selector 574 · [精确引用、面板文字与位置](figures/source-M-adoption.json)。

![业务执行前，恢复 base 内容的新后继对应的精确采用回执。](figures/source-base-adoption.png)

业务执行前，恢复 base 内容的新后继对应的精确采用回执。 source-05 · cut 709 · selector 709 · [精确引用、面板文字与位置](figures/source-base-adoption.json)。

![恢复 M 内容的新后继对应的最终精确采用回执。](figures/source-continuation-adoption.png)

恢复 M 内容的新后继对应的最终精确采用回执。 source-05 · cut 3474 · selector 3401 · [精确引用、面板文字与位置](figures/source-continuation-adoption.json)。


## 独立接收方

![独立 receiver 完成态简要视图：两个 agent／一条边，使用新本地身份。](figures/receiver-overview.png)

独立 receiver 完成态简要视图：两个 agent／一条边，使用新本地身份。 receiver-04 · cut 2441 · selector 2386 · [精确引用、面板文字与位置](figures/receiver-overview.json)。

![独立 receiver 完整业务 PN：五节点／六弧，两个职责均完成。](figures/receiver-petri.png)

独立 receiver 完整业务 PN：五节点／六弧，两个职责均完成。 receiver-04 · cut 2441 · selector 2386 · [精确引用、面板文字与位置](figures/receiver-petri.json)。

![receiver 真实文件物化执行 PN，绑定自身业务 firing。](figures/receiver-execution-materialization.png)

receiver 真实文件物化执行 PN，绑定自身业务 firing。 receiver-04 · cut 2441 · selector 2386 · [精确引用、面板文字与位置](figures/receiver-execution-materialization.json)。

![receiver 真实 workspace-finalization 执行 PN，具有独立执行 checkpoint 与证据。](figures/receiver-execution-finalization.png)

receiver 真实 workspace-finalization 执行 PN，具有独立执行 checkpoint 与证据。 receiver-04 · cut 2441 · selector 2386 · [精确引用、面板文字与位置](figures/receiver-execution-finalization.json)。

## 捕获来源与观察位置

2026-10-13 原生检查窗口的实际官方 Viewer 图。[图像来源与逐图记录](figures/provenance.json)包含精确源码来源、PNG 摘要、revision/net/checkpoint 及观察位置。17 张 PNG 共 4,908,456 字节，原图未变换；同名 JSON 仅省去 localhost URL 和本地 run_dir，保留原 panel text、refs 与 position。source 捕获为 H3474/E8，receiver-04 捕获为 H2441/E4；之后 owner 读取授权行政续签推进 receiver 到 H2444，不能据此声称不同窗口 head 相等。历史 cut、checkpoint selector hint 和捕获 head 分别解释。
