# R1 terminal 契约修订说明

## 缺陷与原生接缝

旧便利层为所有 terminal binding 输出空 config。`cpn/rpnh/registry/module_terminal.py::_terminal_material_for_binding` 读取 `terminal.config.run_outcome`；值不为 `complete` 或 `failed` 就返回 None。这是实际 native publication 前置，不是 compiler 对图形/typed products 的校验能推导出来的。HOST 若声明严格 terminal config，旧候选也会在编译时拒绝。

现有接口的准确名称是 `TerminalBinding` 和 `register_module_terminal`（通过 `RunOwner.terminal()`），本次不新建名为 ModuleTerminalDeclaration 的类型或另一套 terminal 发布机制。

## 最小修正

1. 新必填 `terminal_outcomes`：stop / final_select / final_retain 各明确为 complete|failed。
2. stop 映射用于任意轮 domain stop，含末轮 stop；另外两项只用于最后一个声明轮次的 select/retain。中间轮次 select/retain 仍产出 next，由既有 PN 消费，不提前终态。
3. 原样生成 `TerminalBinding.config={"run_outcome": value}`，由原 Registration config schema 和原 compiler 校验。
4. terminal tool 必须承认原 native config；executor config 仍是 closed empty object。测试给 terminal 独立 required-run_outcome schema，拒绝仍只允许空 config 的 HOST。
5. 同步封闭 schema、唯一示例、双语 README 和同一测试文件，保持六文件交付。未修改 core runtime、Registry、compiler、scorer、legacy RRSI 或预算。

不会自动把 domain stop、保留 incumbent、轮次耗尽或 UNKNOWN 当成功。示例显式指定三项 complete，含义只是该示例正常完成协议，不证明候选更好。通用调用者可为任一分支指定 failed。如果同一个 stop 需要多种原生结果，本模板不足以表达，不能用私有状态改变终态含义。

领域 UNKNOWN 评估与物理执行未完成分开：可信 domain 可按明确策略保留有效 incumbent；物理 provisional/unsettled、owner interruption 不能靠 complete 配置变成业务结果。真正物理 `outcome_unknown` 持久记录和恢复流程本包未测，不以 payload 字符串充当其证据。

## 确定性证据

终态 targeted 集 `terminal-first.xml` 共16项：7项 schema/compiler/HOST契约检查，9项真实临时SQLite Registry。

真实 Registry 的9项是：

- stop→complete、stop→failed
- final_select→complete、final_select→failed
- final_retain→complete、final_retain→failed
- 历史空 config Module 完成所有操作后仍 running，无 terminal evidence
- selector admitted、started、products已发布但未 succeed settlement 时均无 terminal
- owner_stop保持 stopped_by_owner，无 domain terminal evidence

正例调用原 `start_run`、`RunOwner.admit/start/products/succeed/terminal`，然后由独立 read-only Core 经原 `read_run_execution/read_run_terminal_bytes` 回读 exact terminal，验证唯一 evidence、幂等发布和 read cut 不变。业务 executor/tool 均为 fail-on-call 哨兵；socket/socketpair/subprocess 被测试 guard 拒绝。没有 Orchestrator、OwnerEventLoop 或额外 scheduler。这是真实 Registry contract integration，不是完整 native transport / campaign 验收。

历史控制组的 Module 内容由 untouched frozen R1 API 实际派生并保存为 `historical-r1-module.json`；测试重新构造相同空config材料，并断言 canonical SHA-256 为 `f5c3bac53c4b18ee7c42b7d8fdd946275775f89d5a31c51d9b58718cd9a2e66b`。旧 profile 被新 schema 拒绝的事实单独测试，不声称旧 profile 被新 loader成功读取。

最终完整测试结果以 `test-results.json` 与 `final-tests.xml` 为准；targeted重跑、patch重放、独审都分开记，不累计为更多unique案例。

## 基线与边界

本修订在独立目录，使用已核 `main@715468dab0b1bea07d7e94a7aa0606eaf194365c` 的相关 product源码。它相对原 ec9077e只改两处本包materialization内的cpn文件，两者均已Git blob核验。原冻结包仍保留ec9077e/code674来源记录；revision没有混入未合H1/H2代码。

v2完整补丁和旧R1→v2增量见 REPLACEMENT_NOTICE_ZH.md，两条路径二选一。新增schema/template仍是未发布candidate v1，缺mapping的旧profile明确不兼容。只替换交付材料，不覆盖用户已注册的历史实验身份。

## 后续依赖

R2现在直接消费修正后的compiler产物，不在示例补terminal config。它仍负责其generic pure HOST、exact domain输入配对、UNKNOWN策略等独立验证。完整运行transport、owner中断竞态/恢复、provider accounting、author publication/CAS、parent-child、训练和真实模型都不由本次terminal step验证宣布通过。旧冻结rrsi_v06的方法与分数不变。

### 封包时远端又前进（不更改实测身份）

最后只读核验观察到 main=`d92ff3704b6002bf5ecbccb3e6a3d1489809a805`，其父为715468d，内容是H2a TaskControl/native-reader收敛。产品改动含 `cpn/rpnh/registry/run_authority.py`、`cpn/rpnh/task_control.py`、架构说明及新增reader测试；不与本包六个新增路径冲突。两种R1补丁和独审源码未变，也未混入新H2代码。

本包150项的准确实测基础仍为715468d，不冒称d92ff37运行回归已通过。因为新head改动原终态reader文件，最终合入前应在实际新head上重跑同组本地gate；root/R2可单独接续此项。`final-head-drift.json`保留只读差异，不将新head误写成已测试基线。
