# HarnessAudit Office：基于 RPNH 的应用示例

[English](README.md) | 中文

**公开结果对照：** [已发布成绩与本例解读](COMPARISON_ZH.md) · [实现差异补充](IMPLEMENTATION_COMPARISON_ZH.md)。直接引用论文／官网结果，不要求在本机实现或运行原始 harness。

本示例展示 **正式 RPNH＋按 benchmark 配置的 agent 团队＋原始 Office 工具**。
它整理自已经完成的 Office 实验接入代码，并采用最终的观测导出修正；不另建一套 harness，
不覆盖 `cpn/`，也不把评测标准答案写入 agent 指令。

## 不需要 API Key 的入口

在 RPNH 源码根目录执行：

```bash
python examples/harnessaudit_office/example.py results
```

只用 Python 标准库读取已归档结果，不安装依赖、不联系模型、不执行任务，也不冒充 Registry 回放。
已有研究保留 **20 次执行尝试、15 个已评分源槽位**，覆盖 **5 道 Office 任务、2 种执行条件**。
原先被配额截断的尝试仍然保留。先阅读[结果与边界](RESULTS_ZH.md)，再查看
[逐槽位成绩](results/source_slot_scores.csv)和[全部尝试](results/attempts.csv)。

## 这个示例展示什么

公共任务视图提供目标、角色说明和公开工具。应用图包含规划 hub、原任务声明的 specialist 和最终 hub。
RPNH 原生路径负责操作准入、调用身份、Registry 记录和结算；上层适配器运行原始有状态 OfficeBank，
将执行事实投影给原始 HarnessAudit 评分器。

```text
public task -> hub_plan -> specialist_1 --+
                       -> specialist_2 --+-> hub_finalize -> final output
                       -> specialist_3 --+   （仅 off-t6 有第三个 specialist）
```

这是**结构声明示意**，不是已运行 PetriNet 的截图。图中没有声明“政策报告使能后才允许写操作”，
因此不能宣称已验证这种前提。每个业务角色仍可见全部 15 个公开 Office 工具；不使用隐藏的
useful/forbidden 标签制作知道答案的工具白名单。角色指令与实际强制权限并不是同一个事实。

[DESIGN_ZH.md](DESIGN_ZH.md)将这一流程对应到实际函数和产物。
使用示例不需要先进行优化或新增实验。

## 安装可选示例

使用 Linux/WSL2、Python 3.11+。正式 RPNH checkout 应包含
`3492ba2fb50e40173afebae627138afea0bc2f24` 的无累计调用配额功能。外部 benchmark 放在 RPNH 仓库外：

```bash
python -m pip install -e .
python -m pip install -e './examples/harnessaudit_office[test]'

export AUDIT_ROOT="$HOME/rpnh-example-deps/HarnessAudit"
git clone https://github.com/UCSB-AI/HarnessAudit.git "$AUDIT_ROOT"
git -C "$AUDIT_ROOT" checkout --detach 6317162590aeeb1c8dde32b880ac199933343e4a
python -m pip install -e "$AUDIT_ROOT[oai]"

python examples/harnessaudit_office/example.py --help
```

上游 `[oai]` extra 提供评分辅助模块导入的 SDK；本例执行任务仍使用 RPNH driver，不使用上游的
OpenAI agent loop。本例为可选源码示例，不能假定此前发布的 wheel 已包含它。
不复制上游任务和 fixture，来源见[第三方说明](THIRD_PARTY_NOTICES.md)。

## 使用自己的模型配置

保留的适配器当前支持 **RPNH `local_process` 执行 profile 及兼容的本地进程 judge adapter**，
不宣称覆盖 RPNH 的全部 provider 路由。按[模型配置指南](../../docs/guides/models_ZH.md)
复用自己已授权的配置；不要复制作者的账户配置，也不要把历史模型标签理解为现在一定可用的模型。

执行 profile 使用 `llm_execution_selection/v1`，引用自己的本地 adapter。
现有 readiness 要求 adapter argv 明示 `--model`、`--reasoning-effort`；judge 还需明示
`--model-max-output-tokens 512`。具体约束见[CONFIGURATION_ZH.md](CONFIGURATION_ZH.md)。

设置本地变量后执行，所有路径保持在 Git 外：

```bash
: "${RPNH_EXECUTOR_PROFILE:?设置执行 profile 路径}"
: "${EXECUTOR_MODEL:?设置实际模型标签}"
: "${EXECUTOR_EFFORT:?设置配置的 effort}"
: "${RPNH_JUDGE_ADAPTER:?设置 judge adapter 路径}"
: "${JUDGE_MODEL:?设置 judge 模型标签}"
: "${JUDGE_EFFORT:?设置配置的 effort}"
export WORK="$(mktemp -d)"
python examples/harnessaudit_office/example.py configure \
  --task-id off-t1 \
  --executor-profile "$RPNH_EXECUTOR_PROFILE" \
  --executor-model "$EXECUTOR_MODEL" --executor-effort "$EXECUTOR_EFFORT" \
  --judge-adapter "$RPNH_JUDGE_ADAPTER" \
  --judge-model "$JUDGE_MODEL" --judge-effort "$JUDGE_EFFORT" \
  --authorize --output "$WORK/off-t1.json"
python examples/harnessaudit_office/example.py check-config --config "$WORK/off-t1.json"
```

`configure` 只生成配置，`--authorize` 记录用户对所选路由的授权，不会触发付费调用。
执行与评分就绪状态分别报告。新运行默认把模型累计调用、工具累计调用和整任务时限设为 `null`，
**没有必填金额上限**。单次请求超时、启动及故障处理、原生约束和人工停止仍保留；不表示无限硬件、
无限上下文或无限容错。

## 执行一道题，再给已有证据评分

以下两条命令可能调用所选模型。业务写入作用于隔离的 benchmark OfficeBank，不涉及真实员工或外部账户。

```bash
python examples/harnessaudit_office/example.py run \
  --config "$WORK/off-t1.json" --audit-root "$AUDIT_ROOT" \
  --output "$WORK/off-t1-run"

python examples/harnessaudit_office/example.py score \
  --config "$WORK/off-t1.json" --audit-root "$AUDIT_ROOT" \
  --run-root "$WORK/off-t1-run/adapter-run" --output "$WORK/off-t1-score"
```

`run` 不会自动调用 judge；`score` 不重跑 executor。输出目录必须是新目录。
低分或业务未完成也是结果，不应自动删除或重新尝试。其它任务为 `off-t3`、`off-t4`、`off-t5`、`off-t6`，
每题创建独立配置与输出目录。重复次数可由使用者选择，安装或导入不会默认发起 15 次实验。

## 检查运行

```bash
rpnh net --run "$WORK/off-t1-run/adapter-run/rpnh-run" --view --no-open
```

使用既有只读 viewer，不绘制虚构的执行结果。运行目录保留 `protocol.json`、`public_input.json`、
`actions.normalized.json`、`crosswalk.json`、`capture_diagnostics.json`、原生 Registry 与本地 Bank 快照；
`full_score_report.json` 记录评分。工具已返回，不等于后续模型已消费返回值；角色通信需要独立交付证据。
已有证据可以用 `example.py reproject --run-root ... --output NEW_DIRECTORY` 离线重投影，不调用 executor
或 judge；是否补评分另行显式选择。

## 结果与发布边界

结果包含 5 道题、不同资源及源码条件、逐条保留的投影版本；不是排行榜提交、完整 benchmark、
单一条件成功概率或内核安全认证。保留原有角色指令封装，包括其中的效率导向措辞；本轮整理没有开展
提示或工作流优化，也不把旧成绩说成新封装重新运行所得。

不入库完整 Registry、原始模型请求响应、账户 profile、个人绝对路径、完整 Bank 快照和历史交接 ZIP。
公开成绩 CSV 是历史报告数据，单凭它不能重新给旧运行打分；新运行会为使用者产生本地证据。

安装 test extra 后可执行 `python -m pytest examples/harnessaudit_office/tests -q`。
这些是离线示例／函数测试，不是新增模型实验。交付包另有实际执行与尚待本地确认的测试范围记录。
