---
name: rpnh-product-validation-20261009
description: "当前产品组合的有限本地验证与明确未跑范围。"
metadata:
  document-kind: results
  language: zh-CN
  counterpart: README.md
  revision: "2026-10-09.1"
  status: finite-local-validation
---

[English](README.md) | [中文](README_ZH.md)

# 产品组合有限验证（2026-10-09）

15 个原始候选组合在独立完整 5,843 文件身份下验证。762 个不同原用例通过分窗补齐；763 个选择用例中的 TypeScript/Python parity 按 owner 要求暂不执行。不是一次全绿运行，原失败仍保留。完整条件、各次结果和未跑项见 [机器记录](results.json)。

RSI 示例正式接入 ExecutionServices 和原 Orchestrator 后，select/retain/stop 及 10 轮四项通过，实际 operation 数为 6/6/3/30，登记模型计数为 0/0；Owner IPC 是 D0 内存替身。独立 legacy factory 两项通过。三个 DSH 用例使用原本地 AF_UNIX owner 与注入的合成 port，不能作为 stock 客户端或真实 provider 成绩。

独立 M1/M2 955 项通过。R5 覆盖 1,154 个不同用例，为 1,024+130 两窗；旧全量窗口仍 FAILED（1,135 次通过调用，19 项原未跑），不是一次全绿。独立 Codex 实物全树 5,733 文件、composed122/default8 通过，stock 未跑、完整 native fixture 绑定受阻。terminal R3 的 133 项 D0 与 10 项有限 native-after 通过，不能转移为完整 H7 原生能力。

文档构建、wheel/sdist 重建、安装 832 成员读回、16 个受控数据命令和五个示例导出通过。源码树外安装复用已装依赖，无下载。没有重跑历史业务实验，没有改写 ERP 或 SCB 分数。完整 H7 原生链、product-origin 与 Codex 0.162 仍是后续工作。
