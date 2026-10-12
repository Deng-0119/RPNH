---
name: rpnh-native-billing-collaboration
description: "历史十操作电费工具 PN：受控贡献、原采用机制与按日期保留的执行证据。"
metadata:
  document-kind: example
  audience: trusted-host-developer
  language: zh-CN
  counterpart: README.md
  revision: "2026-10-12.4"
  status: historical-validated-local-native-and-viewer
  basis: "public synthetic tool pipeline; original Registry and HOST authority"
---

# 历史原生电费贡献机制

[English](README.md) | 中文

当前 agent 合同协作入口为 [curator → auditor 共建](../agent_cobuild/README_ZH.md)。本页保留历史十操作电费工具 PN 及其原代码、版本、图像、结果和失败。两份确定性 source 程序修改业务校验上限；这些作者和电费操作均不是任务 agent，本记录不验证当前 agent 示例。

在原电费案例中，用量贡献者提交第一项修改，电价贡献者继承已接受版本继续修改，owner 审阅并采用最终网。原生运行和官方 Viewer 已于 2026-10-12 核验，业务结果为 **2.000 kWh／1.70 CNY**。

![实际完成的原生电费 PetriNet：全部 28 节点、29 弧。](figures/panorama.png)

可打开原图查看全分辨率。以下均为同一次已完成运行的未修改截图：

- 用量：[贡献节点](figures/contributor-2.png)、[proposal 引用](figures/contributor-2-refs.png)、[base revision](figures/contributor-2-base_revision_ref.png)、[candidate revision](figures/contributor-2-candidate_revision_ref.png)
- 电价：[贡献节点](figures/contributor-1.png)、[proposal 引用](figures/contributor-1-refs.png)、[base revision](figures/contributor-1-base_revision_ref.png)、[candidate revision](figures/contributor-1-candidate_revision_ref.png)
- owner：[单独采用行为](figures/owner-adoption.png)

[图像来源与精确哈希](figures/provenance.json)标识该结果及其源码窗口。

## 业务任务与两项贡献

示例复用公开的[十操作电费流水线](../../tool_pipeline/README_ZH.md)：读取用量和电价、分别校验、分别归一化、连接区间、计算费用、用整数算法独立校验，最后发布报告。原公开合成 fixture 与预期结果保持不变。

每位贡献者通过 `branch_module(..., ExtractPlan(), output_mode="definition_only")` 复制自己的精确已接受 base：

1. 用量贡献者从初始 base 提出**每段用量最多 100,000 Wh**。owner 审阅并在受保护的共享 Branch 上接受该版本。
2. 电价贡献者从已接受的用量版本提出**每段电价最多 1,000 分／kWh**，同时保留用量上限。owner 审阅并接受这个后继。
3. owner 显式采用同时包含两项贡献的 revision，随后由原 `OwnerEventLoop` 和 `Orchestrator` 执行十操作业务 PN。

原校验器使用 1,000,000,000 的整数上限。示例中的说明性上限收紧业务输入校验，并非安全权限、账号限制或计费算法变更。薄适配层调用原校验器，保留原 complete/rejected 产品类型。纯边界测试表明：base 接受 100,001 Wh，用量贡献拒绝该值；有效 fixture 通过最终两项上限并得到原报告。

这是**已接受版本的单 parent 连续祖先链**。它不演示同 base 自动合并：原保守 resolver 将连接的电费修改归为一个 `coupled_contract`，其 choices 整组选择一侧。主案例沿已接受 parent 继承保留双方贡献，纯回归覆盖 resolver 的既有行为。

## 原权威与提议者记录

owner 和两位贡献者拥有三个不同的原 task Registry、principal 记录及 HOST session，由一个可信本地进程控制。它们是模拟本地主体，不是三位真人、外部认证账号或独立 OS 安全域。

每份确定性贡献者程序执行一次原生 source 操作，从精确已接受 base 字节生成完整 revision，经原 gateway 发布 stock TEXT。`ingest_completed_text` 从原记录重新闭合成功 source firing、invocation、产品与 principal。每条 proposal 有自己的 delivery witness；其中 `synthetic_fixture=false` 表示采用原始执行来源通路，参与者和业务输入仍是合成的。

目标发布 principal 不替代记录中的提议者。owner 分别批准两条精确提案，完成两次受保护 Branch advance，再通过 `owner.control.edits.submit` 单独采用最终版本。两个 advance receipt 和一个 adoption receipt 均由原产品提交边界生成。运行沿用既有调度与权限检查，不调用模型、provider、钱包或外部业务服务。

## 运行与查看

使用已有且兼容的 RPNH 环境及本源码 checkout。示例不会安装软件或选择模型／provider。选择一个尚不存在、较短且可写的工作路径：

```sh
python -m examples.user_collaboration.native_billing.run --work-dir /path/to/short/fresh-work --serve
```

每次调用都会创建新的 canonical 身份并记录一次新运行；不要重用先前工作目录。runner 校验 canonical 结果，并在 `export` 子目录写出派生的 `evidence.json`、`dashboard.json`、`contributions.json` 和 `report.json`。这些只是本地观察结果，权威仍是原 Registry。runtime 导出与 session 材料应留在公开仓库之外。

`--serve` 保持原 HOST/session 绑定存活，并打印实际回环 Viewer URL。默认端口 8774，可用 `--port` 修改。owner 授予一小时只读范围，示例 read session 使用相同生命周期，并始终受原 grant 到期时间约束。官方 `UserContributionView` 每次请求都会复核读取权限。

要重开**同一次结果**，保留该 `--serve` 进程，在授权到期前重新访问它打印的 URL。此操作读取已完成 runtime，不重新执行业务。该查看入口依赖存活的可信 HOST；进程退出或 grant 到期后，持久 Registry 路径或导出 JSON 本身不能恢复贡献 session。

在 Viewer 中：

1. 打开 **PetriNet**，查看全部 28 节点和 29 弧。纯工具网的 agent Overview 可以为空；**Detailed flow** 展示业务工具结构。
2. 打开 **用户贡献**，选择任一记录中的提议者，聚焦 `check_usage_input.run` 或 `check_tariff_input.run`。
3. 检查 `value/config/max_integer` 及其精确 proposal/base/candidate 引用。
4. 打开**未映射至节点的独立行为证据**，查看 owner adoption。

## 历史结果与源码身份

记录的原生运行包含两次完成的 source TEXT firing、两条已批准提案、两次受保护 Branch advance、一次 owner adoption、十次业务结算、十二项业务资源及一个 complete 业务终态。三个模型计数均为 `[0, 0]`。完整报告逐字段匹配原 fixture：**2.000 kWh／1.70 CNY**。

实际浏览器截图已与原 Registry 导出逐项核对，包括两位提议者的字段、精确引用及单独 adoption。查看前后 head **1318**、event count 和 writer epoch 均不变。PNG 保留原不透明 actor/session/checkpoint 引用与观察时间；这些是技术标签，并非个人身份映射或凭据。图片像素未修改。

[SOURCES.json](SOURCES.json)锁定八个复用的公开电费源码与合成 fixture 文件；[provenance](figures/provenance.json)另外记录实际执行的产品／示例源码树指纹。fixture pin 不标识执行产品树，按日期记录的结果也不认证后续组合源码。最后一次本地验收通过七项纯检查；它们禁止创建 Registry、socket bind/connect/listen 及 subprocess 执行：

```sh
python -m unittest discover -s examples/user_collaboration/native_billing/tests -v
```

本例演示同一共享 PN 的贡献记录与原生执行，不据此推断协作质量、远程身份认证、生产计费可用性或进程崩溃自动恢复。

参阅[原生协作指南](../../../docs/guides/user-collaboration_ZH.md)、[Viewer 贡献指南](../../../docs/guides/viewer-user-contributions_ZH.md)、[保留的路线规划研究](../README_ZH.md)及 [MIT 许可证](../../../LICENSE)。
