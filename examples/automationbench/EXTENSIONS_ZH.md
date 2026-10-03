# 当前 AutomationBench 扩展适配

[English](EXTENSIONS.md) | [历史实验](EXPERIMENT_ZH.md) | [运行手册](docs/RUNBOOK_ZH.md)

本页说明 2026-10-02 已保留 pilot 之后新增的可复用代码。它不描述当时实验的执行方式，也不会
改写 `results/` 中的任何内容。

## 支持边界

扩展包含两条相互独立的轴：

| 维度 | 支持项 | 含义 |
|---|---|---|
| 执行宿主 | RPNH native；固定版本的 DSH integration | 负责 actor loop、managed-tool 调用、生命周期和 Registry 证据 |
| 控制界面 | shell、Basic、Codex 或 OpenCode terminal | 调用并观察同一套 `rpnh-ab` CLI 与同一批文件，不是另一名 benchmark actor |

CLI 是唯一 benchmark 权威。Basic、Codex 与 OpenCode 不接收另一份题目 prompt，也不改变评分；
用户可以从这些 terminal 运行完全相同的命令。DSH 不同：`--host dsh` 会改变实际执行宿主，
因此会进入冻结 condition 和 acceptance identity。

以上扩展都不是历史 pilot 的证据。历史运行使用的是
[EXPERIMENT_ZH.md](EXPERIMENT_ZH.md) 所述 native host。

## 新增能力

- `--cohort` 按有序 task ID 将仓库内 18 题计划解析到固定上游，并冻结当前 task contract；它不会
  自动扩展为其余 582 道 public 题。
- `accept-host` 使用确定性合成任务通过已安装的 native 或 DSH host。七项 manifest 覆盖真实
  managed-tool 路径、超过旧 DSH 64 KiB 边界的结果、超过 48 次分派的无累计上限序列、静止停止，
  以及匹配的上游 world/rubric 行为；不调用 provider 或生产业务 API。launch 门禁会重新解析所指向
  的 lifecycle、tool event、score 与 host/Registry 证据；只有标签和文件 hash 不能通过。
- `status` 与 `stop` 只操作调用者提供的 work 目录。停止请求会保留，不会触发自动重跑。
- 会修改状态的离线维护命令（`score`、`summarize`、`reproject`、`export`）共用非阻塞 batch-owner
  锁；live owner 正在发布证据时会拒绝执行。`status` 仍只读，`stop` 在运行期间仍可使用。
- 评分拒绝缺失 lifecycle 证据或 task-contract、final-world、scoring-input hash 已过期的记录；
  score revision 必须匹配 attempt 创建时冻结的输入及上一版 score，`summarize` 不把不合格 score
  纳入成绩。
- `export` 包含 normalization event，以及 return ZIP 中实际脱敏后字节的 hash/size 清单；私有
  profile、原始 Registry 数据库和 provider transcript 仍只留在本地。

`acceptance` 继续作为小型 bridge smoke 的兼容别名；新文档使用 `bridge-smoke`，避免与七项
installed-host 门禁混淆。

## 使用已保留 cohort 准备新的 native 条件

使用独立安装的固定 AutomationBench checkout、已授权 profile、新的短 work 路径和绝对 cohort
路径：

```bash
AB_COHORT="$PWD/examples/automationbench/results/stratified-pilot-plan-20261002.json"

rpnh-ab doctor --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --cohort "$AB_COHORT"
rpnh-ab bridge-smoke --upstream "$AB_UPSTREAM" --work "$AB_WORK"
rpnh-ab prepare --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --cohort "$AB_COHORT" --host native
rpnh-ab accept-host --upstream "$AB_UPSTREAM" --work "$AB_WORK" --host native
rpnh-ab prepare --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --cohort "$AB_COHORT" --host native \
  --acceptance "$AB_WORK/host-acceptance/acceptance.json" \
  --launch-output "$AB_WORK/launch.json"
```

以上步骤均为确定性验收或准备，不会产生真实 provider 调用。明确授权 live command 前，先检查
生成的 plan、condition、doctor 记录和 acceptance manifest：

```bash
rpnh-ab run --upstream "$AB_UPSTREAM" --profile "$PROFILE" \
  --work "$AB_WORK" --config "$AB_WORK/launch.json"
```

最后一条命令可能产生付费模型调用。它建立新 condition，不会复现、修复或覆盖已保留 pilot。

可从另一个 terminal 或任一支持的展示界面执行：

```bash
rpnh-ab status --work "$AB_WORK"
rpnh-ab stop --work "$AB_WORK"
```

## DSH 宿主变体

使用 revision 与 `integrations/dsh/UPSTREAM.json` 一致的专用 checkout。在两次 `prepare` 和
`accept-host` 中都加入 `--host dsh --dsh-checkout "$DSH_CHECKOUT"`。适配器使用受支持的 DSH
console/task/history 链、同一个已登记 provider selection，以及同三项 AutomationBench managed
operation。每次 DSH run 都会生成并传递一份完整 managed binding：其中保留固定上游提供的真实
description、input schema，以及包含 `external_write` 的显式 effect 准入；不会退化为只有 selector
的通用 `object` 工具。AutomationBench 会显式请求 DSH 无累计 attempt 上限；DSH 的普通默认值仍为
48，普通 selector-only managed tool 仍只准入 `pure`。

冻结 DSH identity 时，固定 revision 的干净 factory 文件与经 RPNH 精确、幂等 seam 准备后的文件
被规范化为同一个 post-prepare condition；其他任何 tracked change 都会被拒绝。`accept-host` 在执行
前后各检查一次，因此首次 prepare 不会静默改变冻结条件。

生成 launch request 前，必须对完全相同的代码、profile 与 checkout 条件运行 DSH host
acceptance。native acceptance 不能转用于 DSH condition；任何新宿主结果也不会合并进 `results/`。
