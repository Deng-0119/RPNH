# 四轮合成迭代

[English](README.md) | 中文

[返回案例说明](../../README_ZH.md)。

这些图片展示 `initial_configured` 初始声明；不包含执行状态、token marking 或成功结果。全图用于定位，编号局部图用于阅读节点、弧和标签。

声明源码：[deterministic.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/rsi_workflows/deterministic.py), [test_runtime.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/rsi_workflows/tests/test_runtime.py), [iteration_profile.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/cpn/rpnh/iteration_profile.py)。使用[官方 Viewer `34d42d17`](https://github.com/Deng-0119/RPNH/tree/34d42d171d7e9a7f803b71467063b55786c2cea8) 渲染。

每轮 proposer → evaluator → selector。第 1–3 轮的 next 送入后轮；每轮 stop 与第 4 轮 selected_state 都是声明的输出。虚线空心箭头表示只读 candidate。Flow 折叠各轮 incumbent 和 evaluation place，同时保留 request、candidate、next 与终止分支。

[Petri](#petri) · [Flow](#flow)

## Petri

<img src="../../assets/rsi_rounds_4/petri-full.png" width="730" alt="Four-round synthetic iteration — complete Petri declaration" />

[打开定位图原图](../../assets/rsi_rounds_4/petri-full.png).

按轮次跳转：[初始输入](#p01) · [第 1 轮](#p02) · [第 2 轮](#p08) · [第 3 轮](#p15) · [第 4 轮](#p21) · [输出](#p26)

沿编号从左向右阅读重叠局部图，边缘截断的连线在相邻图继续；节点 ID 按从上到下、再从左到右列出，补充 Viewer 的通用或省略标签。

P27/P28 是第 1/3 轮的补充分支图，分别与 P06–P07 / P19–P20 对照。桥状交叉不表示节点连接。

### P01

**初始输入。** 初始 state → [第 1 轮 proposer](#p02)；四个 request 分别直达对应 evaluator：[1](#p04), [2](#p10), [3](#p17), [4](#p23)。

完整节点（从上到下，再从左到右）：`round_4_evaluator.request`, `round_3_evaluator.request`, `round_2_evaluator.request`, `round_1_proposer.state`, `round_1_evaluator.request`。

<img src="../../assets/rsi_rounds_4/P01.png" width="730" alt="四轮合成迭代 — petri P01 — 初始输入" />

### P02

**第 1 轮 proposer。** [state](#p01) → proposer → [candidate](#p03) 与 [incumbent](#p03)。

完整节点（从上到下，再从左到右）：`round_1_proposer.run`。

<img src="../../assets/rsi_rounds_4/P02.png" width="730" alt="四轮合成迭代 — petri P02 — 第 1 轮 proposer" />

### P03

**第 1 轮 candidate。** [proposer](#p02) → candidate；[evaluator](#p04) 只读 candidate，[selector](#p06) 消费 candidate。下方 incumbent 也通向 [selector](#p06)。

完整节点（从上到下，再从左到右）：`round_1_evaluator.candidate`, `round_1_proposer.incumbent`。

<img src="../../assets/rsi_rounds_4/P03.png" width="730" alt="四轮合成迭代 — petri P03 — 第 1 轮 candidate" />

### P04

**第 1 轮 evaluator。** [request](#p01) + [candidate](#p03) → evaluator → [evaluation](#p05)；虚线空心箭头表示读取 candidate，不消费它。

完整节点（从上到下，再从左到右）：`round_1_evaluator.run`。

<img src="../../assets/rsi_rounds_4/P04.png" width="730" alt="四轮合成迭代 — petri P04 — 第 1 轮 evaluator" />

### P05

**第 1 轮 evaluation。** [evaluator](#p04) → evaluation → [selector](#p06)。

完整节点（从上到下，再从左到右）：`round_1_evaluator.evaluation`。

<img src="../../assets/rsi_rounds_4/P05.png" width="730" alt="四轮合成迭代 — petri P05 — 第 1 轮 evaluation" />

### P06

**第 1 轮 selector。** [candidate](#p03) + [incumbent](#p03) + [evaluation](#p05) → selector → [next](#p07) (retain/select) / [stop](#p26)；分支转折见 [P27](#p27)。

完整节点（从上到下，再从左到右）：`round_1_selector.run`。

<img src="../../assets/rsi_rounds_4/P06.png" width="730" alt="四轮合成迭代 — petri P06 — 第 1 轮 selector" />

### P07

**第 1 轮 next。** [selector](#p06) → next → [第 2 轮 proposer](#p08)。

完整节点（从上到下，再从左到右）：`round_1_selector.next`。

<img src="../../assets/rsi_rounds_4/P07.png" width="730" alt="四轮合成迭代 — petri P07 — 第 1 轮 next" />

### P08

**第 2 轮 proposer。** [第 1 轮 next](#p07) → proposer → [candidate](#p09) 与 [incumbent](#p10)。

完整节点（从上到下，再从左到右）：`round_2_proposer.run`。

<img src="../../assets/rsi_rounds_4/P08.png" width="730" alt="四轮合成迭代 — petri P08 — 第 2 轮 proposer" />

### P09

**第 2 轮 candidate。** [proposer](#p08) → candidate；[evaluator](#p10) 只读 candidate，[selector](#p12) 消费 candidate。 incumbent 的完整节点见 [P10](#p10)。

完整节点（从上到下，再从左到右）：`round_2_evaluator.candidate`。

<img src="../../assets/rsi_rounds_4/P09.png" width="730" alt="四轮合成迭代 — petri P09 — 第 2 轮 candidate" />

### P10

**第 2 轮 evaluator 与 incumbent。** [request](#p01) + [candidate](#p09) → evaluator → [evaluation](#p11)；虚线空心箭头表示读取 candidate，不消费它。 incumbent 也通向 [selector](#p12)。

完整节点（从上到下，再从左到右）：`round_2_evaluator.run`, `round_2_proposer.incumbent`。

<img src="../../assets/rsi_rounds_4/P10.png" width="730" alt="四轮合成迭代 — petri P10 — 第 2 轮 evaluator 与 incumbent" />

### P11

**第 2 轮 evaluation。** [evaluator](#p10) → evaluation → [selector](#p12)。

完整节点（从上到下，再从左到右）：`round_2_evaluator.evaluation`。

<img src="../../assets/rsi_rounds_4/P11.png" width="730" alt="四轮合成迭代 — petri P11 — 第 2 轮 evaluation" />

### P12

**第 2 轮 selector。** [candidate](#p09) + [incumbent](#p10) + [evaluation](#p11) → selector → [next](#p13) (retain/select) / [stop](#p26)。

完整节点（从上到下，再从左到右）：`round_2_selector.run`。

<img src="../../assets/rsi_rounds_4/P12.png" width="730" alt="四轮合成迭代 — petri P12 — 第 2 轮 selector" />

### P13

**第 2 轮 next。** [selector](#p12) → next → [第 3 轮 proposer](#p15)。

完整节点（从上到下，再从左到右）：`round_2_selector.next`。

<img src="../../assets/rsi_rounds_4/P13.png" width="730" alt="四轮合成迭代 — petri P13 — 第 2 轮 next" />

### P14

**第 2 → 3 轮的连线。** [第 2 轮 next](#p13) → [第 3 轮 proposer](#p15)。这里只展示跨图连线和局部节点；长 request/stop 连线仍通向各自 evaluator/输出。

<img src="../../assets/rsi_rounds_4/P14.png" width="730" alt="四轮合成迭代 — petri P14 — 第 2 → 3 轮的连线" />

### P15

**第 3 轮 proposer。** [第 2 轮 next](#p13) → proposer → [candidate](#p16) 与 [incumbent](#p16)。

完整节点（从上到下，再从左到右）：`round_3_proposer.run`。

<img src="../../assets/rsi_rounds_4/P15.png" width="730" alt="四轮合成迭代 — petri P15 — 第 3 轮 proposer" />

### P16

**第 3 轮 candidate。** [proposer](#p15) → candidate；[evaluator](#p17) 只读 candidate，[selector](#p19) 消费 candidate。下方 incumbent 也通向 [selector](#p19)。

完整节点（从上到下，再从左到右）：`round_3_evaluator.candidate`, `round_3_proposer.incumbent`。

<img src="../../assets/rsi_rounds_4/P16.png" width="730" alt="四轮合成迭代 — petri P16 — 第 3 轮 candidate" />

### P17

**第 3 轮 evaluator。** [request](#p01) + [candidate](#p16) → evaluator → [evaluation](#p18)；虚线空心箭头表示读取 candidate，不消费它。

完整节点（从上到下，再从左到右）：`round_3_evaluator.run`。

<img src="../../assets/rsi_rounds_4/P17.png" width="730" alt="四轮合成迭代 — petri P17 — 第 3 轮 evaluator" />

### P18

**第 3 轮 evaluation。** [evaluator](#p17) → evaluation → [selector](#p19)。

完整节点（从上到下，再从左到右）：`round_3_evaluator.evaluation`。

<img src="../../assets/rsi_rounds_4/P18.png" width="730" alt="四轮合成迭代 — petri P18 — 第 3 轮 evaluation" />

### P19

**第 3 轮 selector。** [candidate](#p16) + [incumbent](#p16) + [evaluation](#p18) → selector → [next](#p20) (retain/select) / [stop](#p26)；分支转折见 [P28](#p28)。

完整节点（从上到下，再从左到右）：`round_3_selector.run`。

<img src="../../assets/rsi_rounds_4/P19.png" width="730" alt="四轮合成迭代 — petri P19 — 第 3 轮 selector" />

### P20

**第 3 轮 next。** [selector](#p19) → next → [第 4 轮 proposer](#p21)。

完整节点（从上到下，再从左到右）：`round_3_selector.next`。

<img src="../../assets/rsi_rounds_4/P20.png" width="730" alt="四轮合成迭代 — petri P20 — 第 3 轮 next" />

### P21

**第 4 轮 proposer。** [第 3 轮 next](#p20) → proposer → [candidate](#p22) 与 [incumbent](#p23)。

完整节点（从上到下，再从左到右）：`round_4_proposer.run`。

<img src="../../assets/rsi_rounds_4/P21.png" width="730" alt="四轮合成迭代 — petri P21 — 第 4 轮 proposer" />

### P22

**第 4 轮 candidate。** [proposer](#p21) → candidate；[evaluator](#p23) 只读 candidate，[selector](#p25) 消费 candidate。 incumbent 的完整节点见 [P23](#p23)。

完整节点（从上到下，再从左到右）：`round_4_evaluator.candidate`。

<img src="../../assets/rsi_rounds_4/P22.png" width="730" alt="四轮合成迭代 — petri P22 — 第 4 轮 candidate" />

### P23

**第 4 轮 evaluator 与 incumbent。** [request](#p01) + [candidate](#p22) → evaluator → [evaluation](#p24)；虚线空心箭头表示读取 candidate，不消费它。 incumbent 也通向 [selector](#p25)。

完整节点（从上到下，再从左到右）：`round_4_evaluator.run`, `round_4_proposer.incumbent`。

<img src="../../assets/rsi_rounds_4/P23.png" width="730" alt="四轮合成迭代 — petri P23 — 第 4 轮 evaluator 与 incumbent" />

### P24

**第 4 轮 evaluation。** [evaluator](#p23) → evaluation → [selector](#p25)。

完整节点（从上到下，再从左到右）：`round_4_evaluator.evaluation`。

<img src="../../assets/rsi_rounds_4/P24.png" width="730" alt="四轮合成迭代 — petri P24 — 第 4 轮 evaluation" />

### P25

**第 4 轮 selector。** [candidate](#p22) + [incumbent](#p23) + [evaluation](#p24) → selector → [next](#p26) (retain/select) / [stop](#p26)。

完整节点（从上到下，再从左到右）：`round_4_selector.run`。

<img src="../../assets/rsi_rounds_4/P25.png" width="730" alt="四轮合成迭代 — petri P25 — 第 4 轮 selector" />

### P26

**声明的终止输出。** 第 4 轮 selector 的 retain/select → selected_state；各轮 stop → 对应的 stop 输出。来源 selector：[1](#p06), [2](#p12), [3](#p19), [4](#p25)。

完整节点（从上到下，再从左到右）：`round_4_selector.next`, `round_4_selector.stop`, `round_3_selector.stop`, `round_2_selector.stop`, `round_1_selector.stop`。

<img src="../../assets/rsi_rounds_4/P26.png" width="730" alt="四轮合成迭代 — petri P26 — 声明的终止输出" />

### P27

**第 1 轮 retain/select 分支。** [selector](#p06) → [next](#p07) 的两条不同分支；端点节点在相邻图中完整展示。stop 连线通向 [输出](#p26)。

<img src="../../assets/rsi_rounds_4/P27.png" width="730" alt="四轮合成迭代 — petri P27 — 第 1 轮 retain/select 分支" />

### P28

**第 3 轮 retain/select 分支。** [selector](#p19) → [next](#p20) 的两条不同分支；端点节点在相邻图中完整展示。stop 连线通向 [输出](#p26)。

<img src="../../assets/rsi_rounds_4/P28.png" width="730" alt="四轮合成迭代 — petri P28 — 第 3 轮 retain/select 分支" />

## Flow

<img src="../../assets/rsi_rounds_4/flow-full.png" width="730" alt="Four-round synthetic iteration — complete Flow declaration" />

[打开定位图原图](../../assets/rsi_rounds_4/flow-full.png).

按轮次跳转：[初始输入](#f01) · [第 1 轮](#f02) · [第 2 轮](#f07) · [第 3 轮](#f12) · [第 4 轮](#f17) · [输出](#f21)

沿编号从左向右阅读重叠局部图，边缘截断的连线在相邻图继续；节点 ID 按从上到下、再从左到右列出，补充 Viewer 的通用或省略标签。

### F01

**初始输入。** 初始 state → [第 1 轮 proposer](#f02)；四个 request 分别直达对应 evaluator：[1](#f04), [2](#f09), [3](#f14), [4](#f19)。

完整节点（从上到下，再从左到右）：`round_4_evaluator.request`, `round_3_evaluator.request`, `round_2_evaluator.request`, `round_1_evaluator.request`, `round_1_proposer.state`。

<img src="../../assets/rsi_rounds_4/F01.png" width="730" alt="四轮合成迭代 — flow F01 — 初始输入" />

### F02

**第 1 轮 proposer。** [state](#f01) → proposer → [candidate](#f03)；incumbent 通路折叠为到 [selector](#f05) 的直接连线。

完整节点（从上到下，再从左到右）：`round_1_proposer.run`。

<img src="../../assets/rsi_rounds_4/F02.png" width="730" alt="四轮合成迭代 — flow F02 — 第 1 轮 proposer" />

### F03

**第 1 轮 candidate。** [proposer](#f02) → candidate；[evaluator](#f04) 只读 candidate，[selector](#f05) 消费 candidate。

完整节点（从上到下，再从左到右）：`round_1_evaluator.candidate`。

<img src="../../assets/rsi_rounds_4/F03.png" width="730" alt="四轮合成迭代 — flow F03 — 第 1 轮 candidate" />

### F04

**第 1 轮 evaluator。** [request](#f01) + [candidate](#f03) → evaluator → [selector](#f05)；虚线空心箭头表示读取 candidate，不消费它。 evaluation place 在此折叠。

完整节点（从上到下，再从左到右）：`round_1_evaluator.run`。

<img src="../../assets/rsi_rounds_4/F04.png" width="730" alt="四轮合成迭代 — flow F04 — 第 1 轮 evaluator" />

### F05

**第 1 轮 selector。** [candidate](#f03) + [proposer](#f02) + [evaluator](#f04) → selector → [next](#f06) (retain/select) / [stop](#f21)。

完整节点（从上到下，再从左到右）：`round_1_selector.run`。

<img src="../../assets/rsi_rounds_4/F05.png" width="730" alt="四轮合成迭代 — flow F05 — 第 1 轮 selector" />

### F06

**第 1 轮 next。** [selector](#f05) → next → [第 2 轮 proposer](#f07)。

完整节点（从上到下，再从左到右）：`round_1_selector.next`。

<img src="../../assets/rsi_rounds_4/F06.png" width="730" alt="四轮合成迭代 — flow F06 — 第 1 轮 next" />

### F07

**第 2 轮 proposer。** [第 1 轮 next](#f06) → proposer → [candidate](#f08)；incumbent 通路折叠为到 [selector](#f10) 的直接连线。

完整节点（从上到下，再从左到右）：`round_2_proposer.run`。

<img src="../../assets/rsi_rounds_4/F07.png" width="730" alt="四轮合成迭代 — flow F07 — 第 2 轮 proposer" />

### F08

**第 2 轮 candidate。** [proposer](#f07) → candidate；[evaluator](#f09) 只读 candidate，[selector](#f10) 消费 candidate。

完整节点（从上到下，再从左到右）：`round_2_evaluator.candidate`。

<img src="../../assets/rsi_rounds_4/F08.png" width="730" alt="四轮合成迭代 — flow F08 — 第 2 轮 candidate" />

### F09

**第 2 轮 evaluator。** [request](#f01) + [candidate](#f08) → evaluator → [selector](#f10)；虚线空心箭头表示读取 candidate，不消费它。 evaluation place 在此折叠。

完整节点（从上到下，再从左到右）：`round_2_evaluator.run`。

<img src="../../assets/rsi_rounds_4/F09.png" width="730" alt="四轮合成迭代 — flow F09 — 第 2 轮 evaluator" />

### F10

**第 2 轮 selector。** [candidate](#f08) + [proposer](#f07) + [evaluator](#f09) → selector → [next](#f11) (retain/select) / [stop](#f21)。

完整节点（从上到下，再从左到右）：`round_2_selector.run`。

<img src="../../assets/rsi_rounds_4/F10.png" width="730" alt="四轮合成迭代 — flow F10 — 第 2 轮 selector" />

### F11

**第 2 轮 next。** [selector](#f10) → next → [第 3 轮 proposer](#f12)。

完整节点（从上到下，再从左到右）：`round_2_selector.next`。

<img src="../../assets/rsi_rounds_4/F11.png" width="730" alt="四轮合成迭代 — flow F11 — 第 2 轮 next" />

### F12

**第 3 轮 proposer。** [第 2 轮 next](#f11) → proposer → [candidate](#f13)；incumbent 通路折叠为到 [selector](#f15) 的直接连线。

完整节点（从上到下，再从左到右）：`round_3_proposer.run`。

<img src="../../assets/rsi_rounds_4/F12.png" width="730" alt="四轮合成迭代 — flow F12 — 第 3 轮 proposer" />

### F13

**第 3 轮 candidate。** [proposer](#f12) → candidate；[evaluator](#f14) 只读 candidate，[selector](#f15) 消费 candidate。

完整节点（从上到下，再从左到右）：`round_3_evaluator.candidate`。

<img src="../../assets/rsi_rounds_4/F13.png" width="730" alt="四轮合成迭代 — flow F13 — 第 3 轮 candidate" />

### F14

**第 3 轮 evaluator。** [request](#f01) + [candidate](#f13) → evaluator → [selector](#f15)；虚线空心箭头表示读取 candidate，不消费它。 evaluation place 在此折叠。

完整节点（从上到下，再从左到右）：`round_3_evaluator.run`。

<img src="../../assets/rsi_rounds_4/F14.png" width="730" alt="四轮合成迭代 — flow F14 — 第 3 轮 evaluator" />

### F15

**第 3 轮 selector。** [candidate](#f13) + [proposer](#f12) + [evaluator](#f14) → selector → [next](#f16) (retain/select) / [stop](#f21)。

完整节点（从上到下，再从左到右）：`round_3_selector.run`。

<img src="../../assets/rsi_rounds_4/F15.png" width="730" alt="四轮合成迭代 — flow F15 — 第 3 轮 selector" />

### F16

**第 3 轮 next。** [selector](#f15) → next → [第 4 轮 proposer](#f17)。

完整节点（从上到下，再从左到右）：`round_3_selector.next`。

<img src="../../assets/rsi_rounds_4/F16.png" width="730" alt="四轮合成迭代 — flow F16 — 第 3 轮 next" />

### F17

**第 4 轮 proposer。** [第 3 轮 next](#f16) → proposer → [candidate](#f18)；incumbent 通路折叠为到 [selector](#f20) 的直接连线。

完整节点（从上到下，再从左到右）：`round_4_proposer.run`。

<img src="../../assets/rsi_rounds_4/F17.png" width="730" alt="四轮合成迭代 — flow F17 — 第 4 轮 proposer" />

### F18

**第 4 轮 candidate。** [proposer](#f17) → candidate；[evaluator](#f19) 只读 candidate，[selector](#f20) 消费 candidate。

完整节点（从上到下，再从左到右）：`round_4_evaluator.candidate`。

<img src="../../assets/rsi_rounds_4/F18.png" width="730" alt="四轮合成迭代 — flow F18 — 第 4 轮 candidate" />

### F19

**第 4 轮 evaluator。** [request](#f01) + [candidate](#f18) → evaluator → [selector](#f20)；虚线空心箭头表示读取 candidate，不消费它。 evaluation place 在此折叠。

完整节点（从上到下，再从左到右）：`round_4_evaluator.run`。

<img src="../../assets/rsi_rounds_4/F19.png" width="730" alt="四轮合成迭代 — flow F19 — 第 4 轮 evaluator" />

### F20

**第 4 轮 selector。** [candidate](#f18) + [proposer](#f17) + [evaluator](#f19) → selector → [next](#f21) (retain/select) / [stop](#f21)。

完整节点（从上到下，再从左到右）：`round_4_selector.run`。

<img src="../../assets/rsi_rounds_4/F20.png" width="730" alt="四轮合成迭代 — flow F20 — 第 4 轮 selector" />

### F21

**声明的终止输出。** 第 4 轮 selector 的 retain/select → selected_state；各轮 stop → 对应的 stop 输出。来源 selector：[1](#f05), [2](#f10), [3](#f15), [4](#f20)。

完整节点（从上到下，再从左到右）：`round_4_selector.next`, `round_4_selector.stop`, `round_3_selector.stop`, `round_2_selector.stop`, `round_1_selector.stop`。

<img src="../../assets/rsi_rounds_4/F21.png" width="730" alt="四轮合成迭代 — flow F21 — 声明的终止输出" />

