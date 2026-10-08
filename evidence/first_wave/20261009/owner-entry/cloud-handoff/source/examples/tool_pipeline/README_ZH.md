[English](README.md) | [中文](README_ZH.md)

# 用 typed Petri net 执行原子工具流程

这是完全离线的合成电费例子：1,500 Wh 按 80 fen/kWh、500 Wh 按
100 fen/kWh 计费，结果为 **2.000 kWh，1.20 + 0.50 = 1.70 CNY**。
不连接电力公司、不缴费、不调用模型。

重点是执行边界：**十个独立 operation 由现有 Harness 和同一 Registry
分别准入与结算**，每个 operation 只调用一个精确注册的 HOST 工具。
输入检查、归一化、AND-join、计算、独立验证、最终发布都是真实 transition。
没有另一个 Python 函数负责调度这些业务步骤。

启动器复用 AgentTask 已有的 `Orchestrator` 入口，由它调用同一个 Harness。
事件循环和 worker pool 仍由调用方创建、持有并关闭；该入口不会创建第二个
Registry、调度状态或信号处理器。Provider 装配、工具 ABI 与业务 PN 不变。
本例保留 `make_harness(...)`，供逐步观察的旧测试兼容使用，返回同一入口内部
的 Harness；正常执行使用 `make_orchestrator(...).run()`。

## 从仓库根目录运行

使用仓库正常的 Python 3.11+ 开发环境，安装核心与测试依赖。本例是源码目录中的
可信 HOST 示例，不新增或替代已安装的 `rpnh` 会话入口。

```bash
python -m examples.tool_pipeline.run \
  --run-dir /tmp/rpnh-tool-pipeline-run \
  --output-dir /tmp/rpnh-tool-pipeline-export
```

两个目录都必须不存在。默认输入为 [usage.json](fixtures/usage.json) 和
[tariff.json](fixtures/tariff.json)；可指定 `--usage FILE`、`--tariff FILE`、
`--max-in-flight 1|2`。输入数值必须是有上界的非负整数**字符串**，不经 binary
float。UTC 时间区间必须唯一、无重叠，两份资料的 ID 与起止时间必须精确匹配。

只有 Registry terminal evidence 的 `run_outcome` 为 `complete` 时才显示 PASS。
无效业务输入产生明确的 rejection 资源，不产生成功 terminal；已准入的兄弟分支
结束后，以 `quiescent_marking` 和退出码 1 结束。两路同时无效会保留两个 rejection，
不会制造互相竞争的失败 terminal。未预期的 worker 异常保留未决 firing，不能变成
验证成功，也不会自动重放。

输出均为可丢弃、可重建的衍生文件：

- `report.json`：最终精确资源，包含 source、candidate、validation 的版本引用
- `evidence.json`：已采用声明、terminal、全部中间资源与 provenance、firing 及
  按 Registry 顺序排列的事件
- `adopted-pn.json`、`projection.json`：实际编译声明和只读 Registry 网投影

不依赖原进程及其 Python 对象，可重新导出：

```bash
python -m examples.tool_pipeline.run --readback \
  --run-dir /tmp/rpnh-tool-pipeline-run \
  --output-dir /tmp/rpnh-tool-pipeline-readback
rpnh net --run /tmp/rpnh-tool-pipeline-run --view
```

已采用的声明嵌入本例应用 schema；本例 readback 显式提供对应 schema catalog。
运行目录及导出文件请放在仓库外，不要提交 Registry 数据库。

## 显式声明

[module.json](module.json) 是固定 `ModuleDeclaration`，经现有 `operation`
组件 lower 为十个 `PNFragment` transition、18 个 fusion 后的 place。
声明既可以由人编写，也可以由 agent 生成；执行语义与作者来源无关，本例离线
验收使用零模型调用。

两路均为 read → check → normalize，随后共同进入 join → compute → validate
→ publish。read 另有一个显式输出，向 validator 交付原始资料快照。

validator 的两份源快照是 read 各自的**第二个 product**，具有不同的 Registry
资源引用与 consuming token。单一 consume 输出不会被悄悄广播。
`join_intervals` 必须取得两个 typed 输入；只有一支归一化完成时不能启动。
`validate_report` 消费 candidate 及两份原始快照，`publish_report` 则必须消费
独立注册的 validated 资源，candidate 中的 JSON 标志不能将它启用。

每个输入都是精确 `ResourceVersionRef`；每个输出复用现有 `PetriOutputOrigin`、
output binding、schema authority、`derived_from`、output registration 和
普通 Success。业务 `parents` 字段在发布前与实际已交付输入核对；这些字段不能
授予读取权限。工具只收到已 claimed 的输入值/引用和冻结规则，不能凭同名或
“属于同一 Registry”的任意 ref 去读取其他资源。

## 真实工具 ABI、并发与信任边界

[host.py](host.py) 显式为每个 transition 注册一个工具与一个单步 executor，例如：

- 工具：`example/tool_pipeline/read_usage/v1`
- executor：`example/tool_pipeline/execute_read_usage/v1`
- 本例局部 ABI：`example/tool_pipeline/async_atomic_tool/v1`

已采用注册冻结实现 revision、源码/handler 哈希、输入输出 schema、单位与舍入规则。
JSON 不含 import path 或 callable。注册本身是可信 HOST 决策；源码哈希记录身份，
并不把不可信 Python 隔离成沙箱。

**现有** `invoke_registered_tool` 在 owner 线程核验已 Start 的 execution、
allowed tool key、已采用注册及精确 identity/contracts。若在这里执行阻塞式同步
工具，会串行阻塞 owner。因此本例明确注册 `async def` 原子工具：通过核验的调用
先返回 coroutine，单步 executor 严格检查类型，再在 Harness 已准入的 worker 上
await。每个 executor 只 await 一次、做一个工具操作，再正常发布资源。
不创建额外线程池、业务 ready queue、completed map、多步骤业务调度器或持久化
ledger；并发上限由现有 Harness worker 容量统一控制。

这是高级可信 HOST 的 example-local ABI，不是 core 新增通用 async API，也不是
managed-plugin 子进程回执或 managed-tool action 回执。证据由精确工具注册绑定与
实际 operation 的 admission/Start/input delivery/output/Success 构成。
测试包装器的内存观察只作辅助证明，不能成为准入 authority。

## 独立算术与反例

[tools.py](tools.py) 的计算使用精度 50 的 `Decimal`，**逐区间**按
`ROUND_HALF_UP` 保留两位 CNY，再累计各区间舍入后的金额。验证器使用原始整数
Wh 与 fen/kWh，独立计算 `rounded_fen = (Wh * fen_per_kWh + 500) // 1000`，
不调用 Decimal 计算 helper。它还核对源版本、原始 schema、区间覆盖、边界、全部
归一化数值、逐项金额及总和。

舍入 fixture 包含两个 5 Wh、费率 100 fen/kWh 的区间。每个半分钱分别舍入为
1 fen，总额 **0.02 CNY**；先求和后舍入则会错成 0.01。

```bash
python -m examples.tool_pipeline.run \
  --usage examples/tool_pipeline/fixtures/usage-rounding.json \
  --tariff examples/tool_pipeline/fixtures/tariff-rounding.json \
  --run-dir /tmp/rpnh-tool-rounding-run \
  --output-dir /tmp/rpnh-tool-rounding-export
```

## 定向测试与 transport 限制

```bash
python -m pytest -q examples/tool_pipeline/tests
```

默认测试路径使用真实 `OwnerEventLoop` AF_UNIX listener 与 socketpair。
原生 CLI **没有自动 fallback**；平台拒绝 AF_UNIX 时报告 BLOCKED/退出码 2，
不能把这一结果描述为原生运行通过。

在该类平台上，可显式选择仅供测试的 pipe transport：

```bash
python -m pytest -q examples/tool_pipeline/tests \
  --tool-pipeline-transport=pipe
```

[tests/pipe_transport.py](tests/pipe_transport.py) 只替换 socket 创建、wake FD
和清理；继承实际 owner 的 HOST submit、completion watching 和 dispatch。
Registry、Harness、工具身份核验、工具本体、schema、publication、Success 与
terminal 判定均未替换。这**没有**验证外部 socket 客户端、原生 IPC 或 plugin
子进程。需要在允许 AF_UNIX 的 Linux/WSL2 上运行默认测试和原生 CLI，才能补齐
这一独立 transport 验收。

并发测试用受控 Event，不凭耗时猜测：两个 read handler 必须在不同 worker 上
真正进入，且两个精确 Registry firing 都仍 active；tariff read 被阻塞时 usage
归一化必须已结算落库，join 仍未启用。其他测试覆盖容量、精确因果资源引用、缺输入
准入、错误工具身份、坏单位/时间/数值、双路拒绝、schema 合法的 `1.71` candidate
被拒、舍入、worker 异常未决、终态重建不重复 dispatch、只读重建。

## 边界

- 这是显式声明的 PN，**不是**任意 Python 自动转 PN。
- 没有自动 lower `run_tool_program`，也没有实现单一父工具内嵌 typed execution-net
  的数据流、结果及失败桥接。
- 只有本地 immutable-read/pure 计算；没有外部写、重试、支付、provider 调用、Docker
  或新增 CI trigger。外部副作用 `outcome_unknown` 不在本例范围。
- 导出只是观察，不是另一套 workflow authority。精确版本通过 Registry API 保持
  不可变；core 当前检查存储 payload 的存在与大小，不承诺抵御同长度磁盘篡改，存储
  目录须受信任。交付文件哈希只是交付校验，并非新增 Registry 完整性保证。
- 没有新增任意中断工具的恢复机制。未决 firing 需按现有 recovery/reconciliation
  规则处理；`completed` 字典或报告文件不能让恢复绕过 authority。
