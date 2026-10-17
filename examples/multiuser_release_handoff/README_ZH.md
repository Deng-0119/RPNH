# 多用户发布交接

[English](README.md) | 中文 · [示例目录](../README_ZH.md) · [运行说明](RUNNING_ZH.md)

四个受控参与者通过各自独立的智能体 Task、Registry 和私有业务 PN 协同完成 SQL 发布交接。原始 episode 在同一业务世界中从原生检查点恢复后完成：保留了修订说明 v2、原始 SQL、QA 检查表、一个支持事件和最终交接。最终只读审计 **98/98 必需检查通过**；全部 108 项中 106 PASS、2 项可选 UNKNOWN。

[![运维：实际完成的私有智能体 PN](figures/operations-petri.png)](figures/operations-petri.png)

[完整分辨率角色 PN](figures/operations-petri.png) · [智能体步骤详情](figures/operations-node.png) · [原生执行 PN](figures/operations-execution-finalization.png) · [五张 PN 与 Source 详情](figures/README_ZH.md)

## 智能体实际做了什么

- 运维创建规范发布说明，修订 SQL 语义与回滚指引，核对绑定到具体版本的确认。
- 开发读取说明，并在指定分支提交要求的 SQL。
- QA 与支持读取同一规范说明，原生读取 claim 实际重叠；竞争写入返回 BUSY，未产生写效果。两者释放初次 claim，重新读取 v2 后才确认。
- QA 提交检查表；支持保持原始事件字段，另附验证证据并创建事件；运维向开发、QA、支持发布最终交接包。

参与者是同一可信 HOST 下的本地受控 principal，不代表独立认证的外部真人。每个角色拥有独立 Task/Registry 和 AgentLoop 工作区。设计智能体生成了运维、QA、支持各两个智能体阶段及开发一个阶段，机械降层保留这些职责。共享 **Source 是独立的第五个 Registry，其 PN 控制资源，并不是第五个智能体**。本地 Git/Drive/事件适配器提供领域环境，领域流程没有取代智能体 PN。

## 完成、恢复与边界

初始窗口创建 v1 与 SQL，展示并发读取和写排斥，随后因排队消息的旧读取授权失效而停止。修复后在真正读取时为精确消息及收件者刷新授权。显式原生检查点重入将两个孤立 PROVISIONAL Source 根标记为被后继替代，没有伪造完成。Run01 发布 v2、完成 QA，随后支持在后端派发前遇到嵌套 JSON HOST 序列化故障。该效果经核对确实未发生，原 unknown 回执仍保留。

Run02 在原世界恢复兼容的永久切点；运维、支持在 generation 2 完成，QA 保留 generation 1，开发保留 generation 0。复用 v2、SQL、检查表，运维没有再次尝试 `update_note`。最终 Source 无活跃读取/触发、待处理请求/编辑，世界状态一致。最终交接发布后的收件者消费不作断言。

[整理后的检查与身份](result-summary.json) 保留分母，以及开发/QA 可选历史切点覆盖不足。这两者没有重开，缺少 `actual_restored` 帧符合实际。[业务记录](RESULTS_ZH.md) 说明精确产物和模型计数。图片保留原生 Viewer 原始字节，没有重画流程。

本结果完成的是适配后的发布文档与协调任务；SQL 执行、部署、实际回滚和下游消费验收仍未验证。原始评测器为 **NOT_RUN / NOT_COMPARABLE_ADAPTED**，不代表官方基准分数或全产品多用户验收。

## 使用示例

[运行与查看](RUNNING_ZH.md) 列出源码依赖、原始上游任务、无提供方预检及另行授权的新模型运行。此项是源码示例，不是具名 wheel 导出。新运行会创建新身份和世界，不会重现保留世界的检查点恢复；历史恢复以记录说明，不提供依赖私有状态的便携恢复命令。

[发布验证](release-validation.json)覆盖固定原任务下载、指南配置命令、13次调用的零provider原生预检、80项运行时／示例边界检查、三个新运行控制流测试及隔离安装。新运行收到非终态返回会停止等待者，只有原生Source与HOST状态均无残留才报告完成。本次发布复用历史episode，没有另开付费正式运行。
