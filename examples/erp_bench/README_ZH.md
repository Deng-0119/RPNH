# 通过 RPNH 运行 ERP-Bench

[English](README.md)

本示例将 RPNH 持有的模型执行与受管工具接入两个源码保持不变的 ERP-Bench
任务：`2000_easy_01_buy_only_baseline` 用于 smoke，
`2299_hard_repair_plan_hard` 用于修复展示。修复任务的故障在初始状态中已存在。
这两个案例不构成整体 benchmark 成绩，也不证明 RPNH 有实测优势。

RPNH 持有 actor、工具准入、TaskControl 生命周期与 Registry 事实；Harbor
持有官方 Docker 环境与原始 verifier。本示例不启动上游 Pi/Claude solver，
不重新生成任务，也不修改核心代码。

## 来源与安装

RPNH 固定基点为 `ae09445fe1d9b973502bc5d2c961976c1d2c0163`，仓库为
`https://github.com/Deng-0119/RPNH`。ERP-Bench 固定提交为
`ceba3880af555129b5278e056a0c20f2fb5a0ba9`，仓库为
`https://github.com/agentic-labs/erp-bench`。任务校验检查上游精确提交、origin、
任务跟踪源码及依赖摘要，并将原始 instruction 字节不变地传给 actor。

使用 Python 3.12 或更新版本，以及本示例独立的环境。从 RPNH checkout 根目录
同时安装本地核心与示例：

```sh
python3.12 -m venv .erp-venv
.erp-venv/bin/python -m pip install . './examples/erp_bench[test]'
.erp-venv/bin/rpnh-erp --help
```

示例依赖将 Harbor 固定为 `0.24.0`。此安装不会安装或配置 Docker、模型 provider。
真实试验还需要正常支持 AF_UNIX/进程的 Linux/WSL 运行时、Docker，以及环境适配器
检查的依赖。一次仅运行一个 ERP 世界。运行目录应为较短、私有的 Linux 原生路径；
环境、缓存和运行数据不得进入源码 overlay。不要连接生产 ERP 数据库。

上游容器请求 3 CPU、4096 MB 内存、2048 MB 存储、零 GPU；该存储请求不包括宿主机
镜像及构建缓存。保留官方时限：actor 3600 秒、verifier 300 秒、构建 600 秒。
必须记录实际镜像摘要及 solver/verifier 依赖版本：原始镜像标签可变，镜像与
verifier 指定的 `odoo-client-lib` 版本也不同，分别为 `2.0.0` 与尝试安装 `2.0.2`。

源码报告接受精确 RPNH 基点，或从该基点派生且 base→HEAD 全部差异均位于
`examples/erp_bench/` 的提交；拒绝无关核心、全局或其他示例的变更。
`base_commit` 保持固定，`tested_commit` 记录实际 HEAD，并另存工作文件摘要及脏状态
身份。导出 patch 仍以原始基点为目标。更广泛的集成需要明确更新契约，不能把更新的
main 静默当作原基点。

## 离线命令

以下路径是可移植占位路径，请替换为自己的 checkout 与文件。共享 validator 及
相邻 schema 由集成人提供，作为冻结只读输入；本包不另建或分发共享框架，固定核心
基点中也没有它们。调用时必须显式提供路径。

```sh
rpnh-erp inspect-task --upstream upstream/erp-bench \
  --task 2000_easy_01_buy_only_baseline
rpnh-erp check-plan --input observations/plan.json
rpnh-erp validate-report --manifest reports/condition/result-manifest.json \
  --shared-validator contracts/shared_contract/validate.py \
  --artifacts-root reports/condition --source-root source/RPNH
rpnh-erp export-source --source-root source/RPNH --output returns/erp-source
```

`inspect-task` 输出公开源码/任务身份，不输出本地 checkout 路径。`check-plan`
调用 `planning.validate_plan`，只检查已提供的观察及分配算术，不查询当前 Odoo 状态，
也不提供官方分数。输入对象包含 `orders`、`routes`、`allocations`、`minimum_margin`；
精确 schema 为 `src/rpnh_erp_bench/planning.py` 中的 `PLAN_INPUT_SCHEMA`。
引用必须对应已提供的观察。计划不可行时返回违规项及退出码 1。

`validate-report` 使用指定契约核验 manifest 和所引用的公开字节；可选
`--source-root` 还会验证精确当前源码身份。校验通过不证明实际执行、影响安全或业务成功。
`export-source` 将当前完整自有文件身份作为正向白名单，在新目录写入源码 overlay、
原基点 patch、变更文件摘要及 checksums，不导出运行目录或私有状态。集成前应审阅
导出物并执行 `git apply --check`。

## 已授权试验

先取得覆盖所选任务、现有 provider/精确模型与官方时间窗口的明确用户授权。
凭据存在或历史执行不足以授权新调用。保留现有 RPNH execution-selection 文件的私有性，
不得将密钥、profile 或 endpoint 值复制进示例。CLI 不替换模型，也不提供任意模型、
费用或调用次数预算参数。

```sh
rpnh-erp run --upstream upstream/erp-bench \
  --task 2000_easy_01_buy_only_baseline \
  --execution-selection private/execution-selection.json \
  --run-root private/erp-runs/smoke-01 --condition-id smoke-01 \
  --source-root source/RPNH \
  --shared-validator contracts/shared_contract/validate.py \
  --authorize-existing-model
```

`--run-root` 是本次单个条件的新目录，不是共享父目录，且必须尚不存在；公开制品位于
其 `public/` 子目录。授权标志确认已有用户授权，本身不产生授权；该标志为必填项。
另行获得授权后，展示任务
可改为 `2299_hard_repair_plan_hard`，并使用新条件 ID 与全新世界。修订条件时保留首轮
记录，传入 `--previous-record reports/previous-condition/result-manifest.json`。
不得覆盖失败、复用已改变的数据库，或向同一 solver 回灌原始 grader 反馈后仍称其为
未改变的原始运行。

运行时修复须显式选择。`--firewall-package packages/firewall.deb` 提供任务内软件包
路径，可重复该参数以提供多个包；`--build-compatibility` 明确请求 driver 所声明的
构建兼容修复。不提供时，CLI 分别传递空包路径元组与 `False`，不会静默修复失败构建。
例如，上游浮动 uv 镜像可能遇到 PEP 668 安装拒绝。应保留该次原始失败，然后使用
新条件 ID 与 `--previous-record` 进入明确修复的运行时条件。上游 checkout 保持不变，
记录实际修复及依赖身份。这些参数不安装宿主机全局依赖，也不改变模型、官方时限、
任务 instruction 或 scorer。

使用 Codex 订阅配置时，还须通过 `--codex-binary OFFICIAL_CODEX` 指定现有官方程序，
绕开全局包装器。私有端点关闭原生网页搜索与自动项目文档，保留 RPNH 已有工具禁用
参数，模型、服务、请求预算与凭据引用不变，并明确记录此运行时适配。缺少该端点的
Codex 试跑会在世界和模型启动前被拒绝。模型选择及 adapter 配置在构建前保存私有
快照。适配运行保留原始 grader 数值，使用 `grader_compatibility` 声明，不声称是
未改变条件的原始 benchmark 成绩。

CLI 通过 `asyncio.run(run_trial(...))` 将参数交给 driver，由 driver 持有环境准备、
执行、清理及报告生成。CLI 标准输出为 driver 的安全 JSON 结果。命令错误返回 1
并输出安全错误类型，参数错误返回 2，中断返回 130。`run` 正常退出 0 仅表示返回了
结果，实际结论须读取阶段状态和原始评分报告。

## 动作边界与生命周期

受管 `erp_python` 工具在任务世界内通过原始 `odoo-client-lib` 接口执行 Python。
它是以**整个脚本为粒度**的 `external_write` 操作：一次准入与回执可以包含多个
Odoo 读写，不代表逐事务的 RPNH 准入、回滚或 ERP 恰好一次语义。响应丢失可能意味着
已提交影响未知，应按已知对象身份读回，不可直接重放写入。`validate_plan` 是独立的
`pure` 算术工具。

solver 以非 root 身份在 network-none 策略下运行，同时保留任务内 Odoo loopback
访问。这是明确的 `adapted_network_none_nonroot_script` 条件：原始任务 manifest 的
`allow_internet=true` 不变，但实际访问范围缩小。应声明该适配，不得将结果称为
未改动原条件的 benchmark 成绩。实际隔离探针通过后才可启动。开发者 checkout、setup
数据、隐藏测试、参考解、Docker socket 和私有宿主文件都不是 solver 输入。

生命周期为：全新 seed/就绪 → RPNH owner 执行 → 关闭 bridge 准入 → 停止并确认
owner/脚本 writer 静默 → 冻结终态证据 → 原始 verifier → 拆除该次自有世界。
官方 actor 时限到达时使用受支持的 stop；不能证明静默则阻断冻结及评分。verifier
材料只能在 solver 写入停止后引入，此后不得恢复 solver。冻结身份绑定私有数据库/
文件系统快照、辅助 repair 记录、sentinel 时间戳和时区；仅重建数据库不能保留该身份。

## 报告与结论范围

每个条件都有 `rpnh/example-evidence/v1` 的 `result-manifest.json`，分别记录五阶段：
**offline、mock、native、provider、evaluation**。阶段保留 `passed`、`failed`、
`blocked`、`not_run`、`unknown` 状态，以及命令证据和原因。缺失调用计数保持 `null`，
不能改成零；真实 provider 与 fake provider 分开计数。native 声明需要实际 owner/
Registry 投影，不得编造 task 或对象 ID。fixture 不是实际模型运行。

原始 verifier 输出 `reward.txt`、`reward.json`、`rule_results.tsv`、`optimality.json`
和 `spend.json`。解析器保留指标名、earned/total 分母、NA 状态及 0–100 reward，
原始 `passed` 阈值为 99。评分执行完成与业务验收不同；verifier 退出 0 或 native
终态均不能单独证明 ERP 成功。实际输出的零是测得的失败分数；未运行/受阻评分没有分数。
流程、lineage、复用等补充观察与原始 reward 分开。业务计划改变不证明流程定义修订、
计算复用或 RPNH 实测优势。

原始 provider/工具文本、profile、Registry 数据库、ERP 快照及 verifier 原始字节均
留在私有区域。公开记录仅包含经过审阅的白名单投影与精确摘要。评分净化投影明确声明
字节已改变，并关联保留的原始摘要，不能标注为原始 raw 内容。公开路径相对、受限并
经过 hash 核验。源码导出不包含生成的 run、凭据、缓存、环境或共享/核心变更。

离线检查可将 `ERP_SHARED_VALIDATOR` 指向冻结 validator，并可选地将
`ERP_TEST_UPSTREAM` 指向精确只读上游 checkout，然后执行：

```sh
python -B -m pytest -p no:cacheprovider examples/erp_bench/tests/test_cli.py
```

CLI 测试使用合成 driver 调用与离线算术，不准备 Docker、不调用模型，也不证明
native/provider/业务验收。MIT/CC0 来源及运行时依赖的独立许可证说明见
`THIRD_PARTY_NOTICES.md` 与 `licenses/`。
