# 两位用户共同构建 Agent 任务网

[English](README.md) · [示例目录](../../README_ZH.md) · [协作指南](../../../docs/guides/user-collaboration_ZH.md)

两个受控原 Registry principal 向同一网络贡献有用的任务合同，沿原受保护 Branch 与 owner adoption 接纳精确的连续修订。采用后的目标网运行两个真实 stock `AgentLoop` 角色：curator → auditor。

模型驱动是**动态确定性本地程序**。它读取本次真实工具返回并计算产物，不是 live LLM、旧回放或预填答案。此例验证原生 agent 结构、工具协议、合同共建与产物来源，不证明自主分析、认知质量或独立真人协作。

[![实际官方 Viewer 主任务 PN：五节点六弧、两次已结算 firing。](figures/petri-panorama.png)](figures/petri-panorama.png)

[实际官方 Agent Overview](figures/overview-panorama.png) · [同次运行捕获来源](figures/provenance.json)

概览的两个身份均来自原 `registered_agent_binding`。说明文字解释职责，不提供身份权威。箭头保留原结构交接路径，不冒充发生顺序记录。[完整主任务 PN](figures/petri-panorama.png)有五节点六弧，包含本次**未运行**的框架中断返回路径；未声明返工分支。原登记的文件物化和工作区收尾子网承接 agent 文件 I/O，与这张主任务 PN 分开记录。

## 每个参与者改变什么

| 身份或角色 | 实际职责 | 证据边界 |
| --- | --- | --- |
| curator 贡献者 | 确定性 source 程序加强 curator 合同，要求交接保留每条原输入 ID 与序号 | 独立原 principal、session、source Registry 和一次成功 TEXT firing；不是作者 agent |
| auditor 贡献者 | 第二个确定性程序从已接受 curator 修订出发，要求逐来源对账和显式数量检查 | 不同原 principal／session／source Registry；不是同 base 自动合并 |
| HOST owner | 审阅精确提案，授权受保护 Branch advance，在 target 执行前采用组合修订 | 原网外控制回执；不是 target PN firing |
| Curator 任务 agent | `read_file` 读取输入，保留来源并计算规范交接，以 `write_file` 登记产物 | 原 agent／执行绑定、loop、两 turn、三 action |
| Auditor 任务 agent | 读取真实登记交接，检查数量、总计和来源，登记最终报告 | 另一原 agent 绑定与 loop；使用相同的明确 scripted 模型配置 |

声明链为[基底](declarations/base.json) → [已接受 curator 合同](declarations/accepted-curator.json) → [共同采用合同](declarations/adopted.json)。两项定义变化仅为相应 `operations[0].config.node_synopsis`。工具始终是已授权的 `read_file`、`write_file`、`complete_interaction`，提案不授予新权限。

Curator 把 `retain_source_lineage=false` 改为 `true`；auditor 把 `reconcile_sources=false` 改为 `true`。两份完整指令都进入采用后的执行声明及各角色两次真实 request recipe。最终报告保留两项效果：四个来源 ID／序号以及四个相等的对账数量。这是可观察的协议采用，不推断模型内在语义理解。

## 本次结果与追踪

2026-10-12 UTC 的 `fresh03` 完成两次主任务 firing、两个逻辑 AgentLoop、四次本地 scripted turn、六个已结算 action。两个独立 source 各完成一次提案 firing，模型调用均为零。目标 `actual_model_call_counts=[4,0]` 表示四次已结算脚本请求、零超限调用；**外部 provider 调用为零**。基底登记的 agent 保留为历史，实际执行只使用采用版本的两个绑定。

[原始合成输入](fixtures/input.json)有四条记录。[解码报告](results/report.json)总计 24 分钟：planning 17、review 7。[verify.py](verify.py)从原输入独立重算，逐条核对来源 ID／序号／数值，并拒绝数字与布尔值类型替换。[原 terminal TEXT 产物](results/terminal-product.json)与[带精确引用的验收](results/validation.json)保留采用修订和最终资源之间的关系。

官方 Viewer 的实际证据：

- [Curator 执行](figures/curator.run-execution.png)、[精确绑定](figures/curator.run-binding.png)。
- [Auditor 执行](figures/auditor.run-execution.png)、[精确绑定](figures/auditor.run-binding.png)。
- [第一位登记提案者](figures/contributor-1.png)、[第二位登记提案者](figures/contributor-2.png)、[单独 owner 采用](figures/owner-adoption.png)、[完整采用回执](figures/owner-adoption-ref.png)。
- [来源／运行及只读收据](figures/provenance.json)标明每张图的捕获窗口。贡献与采用视图来自原 HOST，角色与执行视图也通过只读重开同一 target 核对。三个捕获窗口前后 head／事件数均为 1099，writer epoch 均为 2，未重跑任务。

标准 Viewer 披露精确引用和绑定，不披露提示词／资源正文。原 Registry 数据库及私有身份／session 映射留在本地。两个 source Registry 与 target 分别保留自己的身份和证据边界。

## 使用已有兼容安装运行

在产品仓库中选择不存在的短目录，以满足原生 AF_UNIX socket 路径要求：

```sh
python -B -m examples.user_collaboration.agent_cobuild.run --work-dir ./cobuild-run --serve --port 8775
python -B -m examples.user_collaboration.agent_cobuild.verify --input examples/user_collaboration/agent_cobuild/fixtures/input.json --result ./cobuild-run/export/result.json
```

使用已有 `local_process` adapter，不需要凭据或外部服务。每次建立新的受控本地身份和私有运行证据；不要复用旧运行目录，也不要未经披露审查发布原始 export。来源作者与目标模型选择明确固定为本例允许的脚本协议任务。

只读重开已有目标，**不执行任务、不取得 writer 权威、不生成新 grant**：

```sh
python -B -m examples.user_collaboration.agent_cobuild.view --run-dir ./cobuild-run/target --port 8775
```

重开入口展示原目标身份、执行与资源引用。贡献面板依赖原 `run --serve` HOST 及一小时只读会话；重开不重建该会话，过期后沿保留的贡献回执和图像核对。

## 实现与验证范围

示例使用 `AgentStage`／default `AgentLoop` 构建两份 stock 单 agent 声明，在普通 Module 中显式连接端口。合同修订沿用既有 plain-proposal 与受保护 Branch 门。脚本 auditor 通过 typed semantic-input 元数据选择交接输入。

已记录的针对性检查通过：31 项 driver 检查、9 项独立结果检查器检查。它们覆盖本例的结构／协议合同与结果校验，不认证 live 模型推理能力或产品全部集成。

[来源收据](figures/provenance.json)区分实际执行源码窗口与后续只读展示／重开变更。当前源码入口为 [contracts.py](contracts.py)、[run.py](run.py)、[scripted_model.py](scripted_model.py)、[view.py](view.py) 和 [verify.py](verify.py)。原[路线研究](../README_ZH.md#历史路线规划机制证据)与[电费示例](../native_billing/README_ZH.md)分别保留自己的历史业务与贡献证据。
