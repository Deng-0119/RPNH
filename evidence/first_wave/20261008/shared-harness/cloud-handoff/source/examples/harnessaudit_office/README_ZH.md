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

## 终态导出的身份契约

`export_registry(..., terminal_evidence_ref=...)` 保留现有参数名，但非空值仅表示调用方期待的
精确身份。终态状态、证据和结果字节由现有 Registry 当前 execution authority 在同一个冻结的
canonical 截面选定。未提供期待 ref 时仍会导出当前终态；非终态返回 `terminal_evidence_ref: null`，
不产生最终用户通信。向非终态传入非空期待 ref，或传入错误、不存在、跨 run、旧 generation 的 ref，
均会被拒绝。

历史终态行可以与重新开启的 generation 共存，但不能关闭当前开放的 generation，也不能替代
当前 generation 选定的证据。仅在 `include_provisional` 启用时读取活跃 firing 的 provisional 事实；
终态 authority 和结果始终必须是 canonical。缺少当前 authority、精确身份或原子闭包不一致、读取中
Registry head 或 writer epoch 改变时均保守拒绝，并保持调用方 observations 不变。
导出不会创建 writer、resume 运行、调用 provider 或修补证据。

返回的终态 ref 与最终通信中的原始证据来自同一条核验后的 Registry 链。
`capture_diagnostics.terminal_identity` 给出当前 authority ref、状态、generation、终态与结果 ref、
原生 `run_outcome`、head ordinal 和 writer epoch（`rpnh-ha/current-terminal-identity/v1`）。
本次 projection revision 为 `execution-return-registry-reader-v3`，离线重投影 provenance
使用同一 exporter revision。
`stop_reason` 仍是调用方诊断，不代表原生终态或业务成功。现有 capture/评分语义和公开成绩不变。
离线重投影按同一契约核对保存的 driver ref，不能把过期侧文件身份悄悄拼入报告。

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

## 可选的新配置条件

[公开检索/工作流 v1](CONFIGURATION_COMPARISON_ZH.md) 必须显式启用，当前只有合成离线验证。上文 baseline 流程仍是默认，已发表成绩不改。

终态身份、canonical 闭包、不可变描述符字节和资源直接 provenance 由共享的 `registry.run_authority.read_run_execution` 读取接口校验。导出器只格式化其 typed 结果，并在输出 observations 前重检同一 read cut，不维护第二套终态匹配链。
