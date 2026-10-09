# R2 单 Registry 确定性验证方案

固定输入为已核 `main@715468dab0b1bea07d7e94a7aa0606eaf194365c` 相关源文件，加冻结 R1 六文件 patch `cad3873f4225c326c1dc4ebff2a382c8de9b9ffb0e8d372fbb296f07200f9a45`。R1 原目录只读，不修改。独立目录不声称完整 clone；记录所有实际源文件和显式 overlay。

## 精确执行入口

1. 纯 D0：原 `compile_iteration_profile` 调用原 compiler，原 `start_run` 建一份 Registry；测试显式调用 `RunOwner.admit → start → products → succeed`。这些是现有通用步进接口，不需要 `OwnerEventLoop`、socket、第二 writer 或新 scheduler。测试只逐一提出指定 firing 作为探针，由原 admission 判断是否 enabled。
2. 补充端到端：原 `Orchestrator.run → Harness.exact_execute`；HOST 使用同步 Future，仅执行确定性纯函数并按原 `OperationDispatch/OperationProducts` 返回。若 native AF_UNIX 环境被拒绝，保留失败证据，不修改权限。只允许显式复用原 `examples/tool_pipeline/tests/pipe_transport.py` 作为 TEST ONLY wake transport，不能说成 native 验收。
3. 结果读取：原 `read_run_execution/read_run_terminal_bytes`，独立只读 Core 打开同一个 Registry，输出后检查 read cut 不变。不用 sink 或 report 作为完成证据。

## 有限新增范围

独立 `examples/rsi_workflows/` 放一个纯 HOST 三角色示例和英中文说明，外加测试。角色只消费 admitted inputs；返回普通 schema-valid product bytes，无 Core、文件、网络、模型、API、运行循环、预算计数器或进程外状态。候选为整数参数，评估为固定绝对误差纯函数；这是自有 synthetic 条件，不替换 RRSI 方法或声称改善真实模型。

candidate 绑定父 state exact resource；evaluation 绑定同轮 candidate、evaluation-request（包含合成 dataset）exact refs 和该 evaluator operation binding；selection state 绑定实际 candidate/evaluation/incumbent exact refs。selector 同时检查业务配对，并由 PN evaluation 输入控制能否 admission。UNKNOWN 评估只 retain，不能升候选。select/retain 走 next，stop 走终态 carrier；多轮完全来自 R1 有限展开 PN。

## 验收及边界

- 缺 evaluation、仅发布未 settle、上一轮事实及伪 report 均不能准入本轮 selector；拒绝不新增事件/推进 checkpoint。
- 同轮 exact 输入输出链、跨轮 next/state，retain、stop、多轮终态，原只读 terminal reader。
- rounds 与 native model-call cap 独立：多轮纯 operation 可完成，原 returned model-call 计数仍为零。禁止把 rounds/dispatch 当模型调用计数。
- 保留 frozen R1 字节与旧 RRSI 所有源文件；不新增 runner/CLI/child seam，不伪造 author→run API。author branch CAS 和跨 child orchestration 不属于本次有限 runtime 证明。
- native AF_UNIX、registered-model/scripted port、owner-stop/resume、进程及传输竞态留后续 native gate；未知权限错误不旁路。

若上述原 step 接口遇到真正 core 缺口，先报告最小缺口并停依赖实现，不另造 authority。

## 发现的 R1 修订依赖（2026-10-08）

运行核验发现 R1 生成所有 terminal.config={}，而原 native terminal reader 明确要求
run_outcome='complete' 或 'failed'。因此原样 R1 在PN完成后仍不能发布终态。
这不是 HOST 的已文档化义务；原包未列出补 terminal config 的 binding requirement。
最初尝试的 example bind_completion 派生 fixture 仅用于机制探查，已被否决为最终方案，
其初期代码和失败日志保留在独立 early-derived-fixture 中。R2 不以默认 HOST overlay 隐藏遗漏。

R1 作者正在制作独立后继修订：profile 必填 terminal_outcomes={stop,final_select,final_retain}，
每项显式选择 native complete|failed。R2 等待这份修订并直接消费原 compiler 的生成物。
本示例三项显式 complete 的理由是：stop=协议正常提前结束；select/retain=有界流程正常完成，
不保证候选改善。domain evaluation status=unknown 仅保留有效 incumbent，不产生晋升。
物理执行未知、未返回、未settled或异常没有正常selector输出，也不得由该映射制造成功。
