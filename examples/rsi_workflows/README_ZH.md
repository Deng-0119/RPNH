[English](README.md) | [中文](README_ZH.md)

# 单 Registry 合成迭代（R2）

这个有限示例把纯 proposer/evaluator/selector HOST 接到原 iteration-profile compiler。
不新增 runner、scheduler、provider、预算计数器、Registry 类型或外部 winner 状态。
依赖 R1 后继修订的显式 terminal_outcomes 字段；原冻结 R1 未生成可用的 native
terminal binding，本示例不在运行前偷偷补 config。

## 条件与精确谱系

候选是 incumbent + 1 的整数，evaluation 对同一 registered request 中的整数 target
计算候选与 incumbent 的绝对误差；越低越好，平分或退化 retain。domain-level unknown
输出空分数，只保留有效 incumbent，不能晋升。这是独立合成条件，不改变 RRSI v0.6 的
scorer、限制、heldout 或研究协议，也不证明模型改善。

- proposer 只接收本次 claimed state，不接收 request 正文。
- candidate 绑定父 state 的 exact resource ref。
- evaluation 绑定本轮 candidate、request（包含合成 dataset）、父 state 和 evaluator
  operation binding 的 exact refs；已注册 HOST identity 固定本例 scoring implementation。
- selector 通过原 PN consume 同轮 incumbent/candidate/evaluation；输出 state 保留三者
  exact refs，以及 request/scorer binding。admission 后的业务一致性检查拒绝错误轮次、
  候选或父 state 配对。
- 后轮 proposer 只消费前轮 selector 的 registered next。轮数和 readiness 均来自 R1
  有限 Module，没有 Python 外部 controller。测试 step 仅提出一个明确 firing，准入由
  原 RunOwner.admit 决定。

这些是可信纯 HOST，不是针对恶意 Python scorer 的沙箱。Registry origin/claim 谱系与
业务内容配对各司其职。测试只证明本例精确数据流，不等于通用训练/test 无泄漏隔离、
恶意 HOST 防护或任意 scoring 正确性。

## 终态语义必须明确

本例 profile 把 stop、final_select、final_retain 显式映射为 native complete：stop
表示本轮正常评估后提前结束协议，不是用户中断；最后轮 select/retain 表示有限过程结束，
不保证候选改善。另有回归测试显式把 final_retain 设为 failed，核原 native reader 原样
保留该分类。profile 编译之后不再修改 terminal config。

domain unknown 与物理执行结果未知/未settled不同。缺 evaluation、仅发布未settle、
错误配对或角色异常不能产生正常 selection。provider submission uncertainty、
owner-stop/resume、transport loss 仍是独立 native gate，不把它们映射为成功。

native model-call cap 固定为1，与 rounds 独立。纯HOST运行12个 operation，原
returned-model-call 计数仍为0。保持原 actual_returned_calls 语义，不能把它叫作总
dispatch或所有physical attempt的上限；没有解析任何 model binding。

## 验证与执行边界

在仓库根目录使用已经准备好的 Python 测试环境：

```bash
python -m pytest -q examples/rsi_workflows/tests/test_runtime.py -k 'not original_orchestrator'
python -m pytest -q examples/rsi_workflows/tests/test_runtime.py -k original_orchestrator
```

第一组是无socket D0：直接调用原 start_run → RunOwner.admit/start/products/succeed
→ terminal，测试封锁socket/subprocess。通过原 read_run_execution/read_run_terminal_bytes
以只读Core重建结果，输出后重验cut。伪造/删除report不改变准入或Registry状态。

第二组默认使用真正 OwnerEventLoop AF_UNIX（D1）与既有 Orchestrator.run →
Harness.exact_execute，HOST用立即完成的Future。环境拒绝AF_UNIX时保留原失败，不改
权限、不冒称native通过。可明确运行测试专用D0 transport double：

```bash
python -m pytest -q examples/rsi_workflows/tests/test_runtime.py --rsi-transport=pipe
```

这里只复用原样 examples/tool_pipeline/tests/pipe_transport.py，替换wake transport，
不是自动production fallback，也不是socket、多进程、外部provider、registered-model
port或R3验收。

不新增CLI、author-revision到run的捷径、自动branch晋升、child编排、retry、训练或发布。
纯Registry测试不表示author/CAS或完整R2/R3路线图门槛已经完成。
