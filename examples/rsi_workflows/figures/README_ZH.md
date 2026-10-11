# rsi_workflows：PetriNet 声明图册

[English](README.md) | 中文

以下是[公开源码 `34d42d17`](https://github.com/Deng-0119/RPNH/tree/34d42d171d7e9a7f803b71467063b55786c2cea8)中声明的初始 PetriNet 结构，由官方 RPNH viewer 展示。圆形表示 place，矩形表示 transition，弧表示输入、输出或读取关系。完整图片用于整体概览；四轮声明另提供下方本地交互 Viewer 入口。

[返回案例说明](../README_ZH.md)。

## 单轮合成迭代

声明源码：[examples/rsi_workflows/deterministic.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/rsi_workflows/deterministic.py), [examples/rsi_workflows/tests/test_runtime.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/rsi_workflows/tests/test_runtime.py), [cpn/rpnh/iteration_profile.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/cpn/rpnh/iteration_profile.py).

![单轮合成迭代：初始 PetriNet 结构](../assets/petrinet-bc51f0c2910010fa.png)


## 两轮合成迭代

声明源码：[examples/rsi_workflows/deterministic.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/rsi_workflows/deterministic.py), [examples/rsi_workflows/tests/test_runtime.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/rsi_workflows/tests/test_runtime.py), [cpn/rpnh/iteration_profile.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/cpn/rpnh/iteration_profile.py).

![两轮合成迭代：初始 PetriNet 结构](../assets/petrinet-e900afe5cddd927f.png)


## 三轮合成迭代

声明源码：[examples/rsi_workflows/deterministic.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/rsi_workflows/deterministic.py), [examples/rsi_workflows/tests/test_runtime.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/rsi_workflows/tests/test_runtime.py), [cpn/rpnh/iteration_profile.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/cpn/rpnh/iteration_profile.py).

![三轮合成迭代：初始 PetriNet 结构](../assets/petrinet-cfe4f81163c978a8.png)


## 四轮合成迭代

声明源码：[examples/rsi_workflows/deterministic.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/rsi_workflows/deterministic.py), [examples/rsi_workflows/tests/test_runtime.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/rsi_workflows/tests/test_runtime.py), [cpn/rpnh/iteration_profile.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/cpn/rpnh/iteration_profile.py).

![四轮合成迭代：初始 PetriNet 结构](../assets/petrinet-270f21f530edf7f3.png)

[四轮 Petri / Flow 全景与本地 Viewer](four-round/README_ZH.md)。


## 单轮 failed-retain 终止绑定

声明源码：[examples/rsi_workflows/deterministic.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/rsi_workflows/deterministic.py), [examples/rsi_workflows/tests/test_runtime.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/rsi_workflows/tests/test_runtime.py), [cpn/rpnh/iteration_profile.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/cpn/rpnh/iteration_profile.py).

![单轮 failed-retain 终止绑定：初始 PetriNet 结构](../assets/petrinet-6d4ef3a0349e89c3.png)

[图片身份记录](../results/figure-provenance.json)。
