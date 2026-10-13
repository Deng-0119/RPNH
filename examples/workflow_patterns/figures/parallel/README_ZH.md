# 并行分叉与汇合

[English](README.md) | 中文

[返回案例说明](../../README_ZH.md)。

这些图片展示 `initial_configured` 初始声明；不包含执行状态、token marking 或成功结果。全景展示完整结构；细节可在下方本地 Viewer 中查看。

声明源码：[parallel.json](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/workflow_patterns/scenarios/parallel.json), [run.py](https://github.com/Deng-0119/RPNH/blob/34d42d171d7e9a7f803b71467063b55786c2cea8/examples/workflow_patterns/run.py)。使用[官方 Viewer `34d42d17`](https://github.com/Deng-0119/RPNH/tree/34d42d171d7e9a7f803b71467063b55786c2cea8) 渲染。

prepare 分叉到 facts 与 risks，再由 join 汇合。Overview 仅展示四个 Agent 及四条关系；Petri 保留数据/控制 place、权重和 interrupted 返回分支。

[Overview](#overview) · [Petri](#petri) · [本地 Viewer](#local-viewer)

## Overview

[pattern_parallel — complete Overview declaration](../../assets/pattern_parallel/overview-full.png)

[打开全景原图](../../assets/pattern_parallel/overview-full.png)。

## Petri

<img src="../../assets/pattern_parallel/petri-full.png" width="730" alt="pattern_parallel — complete Petri declaration" />

[打开全景原图](../../assets/pattern_parallel/petri-full.png)。

<a id="local-viewer"></a>

## 在本地 Viewer 查看声明

在仓库根目录，使用已安装 RPNH 的 Python 环境运行：

```bash
python examples/view_declaration.py parallel --no-open
```

打开终端打印的 `http://127.0.0.1:端口/` 地址；端口由系统分配，不是固定端口。保持该终端运行，查看结束后按 Ctrl+C 关闭 Viewer。

1. 切换 **Overview** 或 **PetriNet**。
2. 用滚轮或 `−` / `＋` 缩放，拖动画布空白处平移；**Fit to view** 返回全景。
3. 点击节点或弧打开详情；也可用 **Search + Locate** 定位节点。

该命令只读取随仓库提供、与全景图对应的 [静态声明帧](declaration-frame.json)，复用官方 Viewer。它不创建 Registry、不执行 transition、不调用模型；这里不提供执行状态、token marking 或历史回放。

若要查看自己已经存在的运行，把其准确的 Registry run 目录设为 `RUN_DIR`，再执行 `rpnh net --run "$RUN_DIR" --view --no-open`。该运行的状态与上面的静态声明不同。更多控件见[Viewer 指南](../../../../docs/guides/viewer_ZH.md)。
