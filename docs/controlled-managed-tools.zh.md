---
name: rpnh-controlled-managed-tools
description: 显式启用 managed 结果回读、调度和隔离工具程序。
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: controlled-managed-tools.md
  revision: "2026-10-08.2"
  status: reference
---

[English](controlled-managed-tools.md) | [中文](controlled-managed-tools.zh.md)

# 受控 managed 工具

这些接口必须显式选择。普通工具集默认不加入 `read_managed_output`、
`run_tool_program` 或 `read_tool_program_output`。省略
`AgentTaskSpec.managed_tool_policy` 保持已有 managed 串行执行；省略
`tool_program_policy` 保持普通工具集。已有权限、效果准入、模型限额和停止条件
仍然有效。选择 reader 或调度策略不会扩大业务权限。

## 精确 managed 输出回读（P1）

需要让 managed 结构化结果持续可达时，在 workflow 节点声明的工具目录中显式暴露
`read_managed_output`。它读取同一 loop 中更早、已结算且 returned 的
`agent_action/v3`。已有 `read_action_output` 的 v2 stdout/stderr 接口保持独立。

从首次结果信封复制精确 `agent_action_ref` 和 `terminal_receipt_ref`，作为
`read_managed_output` 参数；可加 `offset_chars`（默认 0）和 `max_bytes`
（默认 10000，可见上限 10000）。action ref 包含 `entity_type`、`logical_id`、
`version_id`；receipt ref 包含 `resource_id`、`resource_version_id`。
不要替换成显示名称或 latest 版本。reader 核对同 loop 历史、状态及收据身份。

`managed_output_page/v1` 包含上述 refs、`reader`、`content`、`offset_chars`、
`next_offset_chars`、`total_chars`、`truncated`。正文是确定性 JSON 序列化片段；
逐页拼接后再解析完整输出。跟随 `next_offset_chars`，直到 null。offset 计字符，
`max_bytes` 计算完整 UTF-8 JSON 页，包括转义正文与 locator。最小信封与至少一个
进展字符放不下时明确失败。回读不重跑 handler、不改变业务状态。

暴露 reader 后，首次有界结果及压缩、替换历史保留可调用 locator。“已经返回”、
“包含于后续实际提交请求”、“可回读”是不同事实。没有可用 reader 时不宣传可调用
入口；超限结果明确失败，不静默丢失中间内容。failed/unknown managed action
不会改称 returned。

### 总披露预算与历史恢复

每个完整工具 turn 的模型可见上限为 40,000 字节，计入 assistant 调用信封、
参数、结果身份和 JSON 转义；单结果页面仍默认 10,000 字节。同批超限时，
确定性预览可降为显式 `tool_result_reference/v1` locator。最小合法 call/result
信封仍放不下时，准备请求明确失败；不会丢弃、合并或重跑已经执行的调用。
reader 不可用时，不能用假恢复入口隐藏超限正文。

压缩按 `retained_history_token_limit` 保留近期完整组，通常为 20,000 个近似
token。目录入口计入该预算。最新普通 turn 尚待通知的结果组，或小到放不下最小目录
入口的配置，可超出目标；目录与待通知组共同受 40,000 字节硬上限约束。
完整 fact capsule、摘要和 system prompt 另受既有上下文压力检查，不包含在近期
历史的 token 目标中。仅完成压缩不会解除该义务；需要后续普通响应已登记。
`not_submitted`、`submission_unknown` 不表示结果已交付或被语义使用。
通过该边界后，旧页面可退出近期历史。

暴露任一结果 reader 时，框架在 fact capsule 中确定性携带 `result_archive`
入口。调用其中指定的 reader，传入 `archive_loop_ref`、`before_turn_sequence`、
`offset` 和 `max_bytes`，参数中不包含 `reader` 字段。目录使用已登记的同 loop
固定历史切面，每页最多 16 条有界元数据；跟随 `next_offset`，直到 null。
再将选中条目的精确 action/receipt 或 output resource 交给普通正文 reader。
目录不返回结果正文、不执行 handler，reader 可用性来自当前真实目录。
它复用 Registry 不可变记录，可在同 loop 重建后恢复，不跨新 loop 授权。

## 只读证据及独立 read HOST（P2/P4）

```bash
rpnh net --run RUN_DIR --result-evidence
rpnh net --read-host-config READ_HOST_JSON --preflight
rpnh net --read-host-config READ_HOST_JSON --view --no-open
rpnh plugins --config PLUGIN_JSON inspect
```

`--result-evidence` 投影已登记 action/return 引用、后续请求材料与提交证据、
可观察后续 action refs 和已有停止事实。请求投影区分 `full`、`bounded`、
`reference`、`absent`、`unavailable`、`not_checked`；仅登记请求不证明提交。
无后续请求与历史材料不可用分别报告。`semantic_use` 和 `decision_influence`
保持 unknown，不推断业务 readback 或 benchmark 分数。runtime terminal 与业务
成功分开。停止投影保留 `llm_turn_cap` 与 `task_model_call_cap` 的区别、有效限额、
实际计数、已记录停止阶段和精确 loop/event refs。缺失事实保持 unavailable 或
not recorded；检查不会恢复、重试、改分，也不建立第二套计数器。

`--run` 与 `--read-host-config` 互斥。preflight/evidence 模式拒绝 Viewer 与资源
输出选项。`--preflight` 描述选定读会话，最终复核访问后关闭；不查询对象、不启动
监听器、不签发 grant、不核验执行准备状态。

owner 控制的 JSON 包含 `schema_version: rpnh/registry_read_host_config/v1`、
`purpose`、`sources`，可选 `limits`/`source_set`。每个 source 选择 `source_ref`、
`access_path`、绝对路径 `registry_root`、`binding_generation` 和已有
`observer_context`；其 task ref、purpose 必须匹配来源。这里选择已有权限，
不接受浏览器提交的 principal 或 grant。配置应为当前用户所有的普通文件，模式
0400 或 0600，使用可信且无符号链接的绝对路径。独立 read HOST 以只读方式打开
canonical sources，并复核访问；不可用来源遵循已有披露合同。参见
[Viewer](guides/viewer_ZH.md)。

`plugins inspect` 只读已安装元数据和显式选择，不加载 factory。已安装、已选择、
声明已加载、HOST 已绑定、可执行是不同状态。缺少 inert metadata 不授权执行
factory。`plugins list`/`check` 会加载可信已选 factory，范围不同。

## 可带离仓库的插件作者自检（P5）

用 `rpnh examples export --example native_plugin --output ABSENT_DIRECTORY`
导出原生案例。在目标 RPNH 环境明确构建并安装 wheel，再做声明自检。从导出根运行：

```bash
python -I examples/native_plugin/selfcheck_declaration.py --output declaration-a.json
python -I examples/native_plugin/selfcheck_declaration.py --output declaration-b.json --previous declaration-a.json
python -I examples/native_plugin/selfcheck_terminal.py --operation demo/add --run-dir ABSENT_RUN_DIR --result terminal.json
```

声明自检执行可信已安装 factory，检查 descriptor、schema、依赖、资源及实际导入
路径，不绑定 HOST。独立终态自检沿正常插件 runtime 执行一项明确授权的 pure
operation，检查真实输出、terminal 事实及零模型调用。IPC 拒绝是 blocked。
两个自检都不安装插件。制作 B 版本时一致更新 distribution、module、entry point、
plugin/version 和选择，检查 descriptor 变化并显式重新绑定。旧绑定不会自动成为 B。
参见[作者版本](../examples/native_plugin/AUTHOR_VERSIONS_ZH.md)。

## 独立保留 readback 条件

| 案例 | 显式新条件 | 保留条件 |
| --- | --- | --- |
| AutomationBench | `api-contract-visibility-readback-v2` | `baseline`、`api-contract-visibility-v1` |
| Office | `office-public-discovery-readback-v2` | 无 comparison 选择、`office-public-discovery-workflow-v1` |

通过各案例的 `--configuration-condition` 选择。新条件在实际节点目录暴露
`read_managed_output`，记录独立 condition/catalog/implementation 身份。Office
对支持的公开 campaign bundles 保留公共 discovery 和 policy-evidence 写入 gate；
reader 不放宽它。区分 registered return、可核对的后续实际提交请求包含和可观察
state/readback。actor 自述及 readback 都不证明语义使用。保持历史条件身份和结果；
这些选项不声称验收率或分数提升。参见
[AutomationBench](../examples/automationbench/README.md) 和
[Office](../examples/harnessaudit_office/README_ZH.md)。

## Stage 1/2：声明 managed 调度

HOST 提供显式 `managed_bindings`，把完整策略文档传给
`AgentTaskSpec.managed_tool_policy`：

```python
from cpn.plugins.managed_scheduler import ManagedSchedulerPolicy

managed_tool_policy = ManagedSchedulerPolicy(
    policy_id="managed_pure_parallel/v1", max_in_flight=2,
).identity()
```

Stage 1 仅准入 managed protocol-v2 pure operation。Stage 2 使用
`managed_conflict_domains/v1` 和 `conflict_domains`，由
`ManagedConflictDomain(registration_key, effect, reads=(), writes=(), unknown=False)`
构造。使用精确登记 key 及可信 HOST domain ID，不从模型提示或工具名猜测。
pure 声明没有外部 domain；已知 external_read 声明 reads 且无 writes；已知
external_write 声明 writes。冲突读写串行，不相关 domain 可重叠。缺失声明或
unknown domain 形成独占屏障。效果仍需原有准入。

`max_in_flight` 使用整个 run 共享的 `ManagedRunCapacity`，包括同时 firing。
owner 准入前先预留容量。prepare/finish 跨同一个 owner gateway；worker 和等待
在 owner 外。每项已启动调用保留真实 started/terminal 收据。原模型响应 ordinal
及 call 身份决定 action/result 消息顺序；完成先后不生成身份。所有逐项 outcome，
包括已知错误和未启动项，一次整 turn 结算并生成一个 loop 后继。已知单项失败
不会使整个 batch fail-fast。

已选择的策略也覆盖 builtin/managed 混合 turn 中的 managed 调用。它们与 builtin
保持原调用顺序逐项执行，并与其他 firing 共用准入；等待容量不会占住 owner
线程。未选择策略时保持原有串行行为。

stop/cancellation 阻止新启动并收拢已准入观察。unknown outcome 阻止该 operation
后续准入，保留真实收据并让已运行 sibling 结算。不得自动重试或重执行 unknown；
使用既有 reconciliation authority。观察已有 invocation 不创建新执行。

## Stage 3：声明的隔离程序 API

`cpn.plugins.controlled_script` 的实际 profile 为 `linux_isolated_python/v1`。
程序位于受限 Linux process/root，scratch 有界，无 HOST 文件、凭据、网络或
owner/Registry 对象。业务访问通过已登记 HOST broker。隔离不支持或 setup 失败
明确报告，不退回无约束执行。

HOST 选择共享 scheduler policy 及精确 managed 名称：

```python
import dataclasses
from cpn.plugins.controlled_script import IsolatedProgramBudget

# 提供真实节点绑定的 managed 名称；这不是默认工具列表。
def program_policy(selectedmanagednames):
    return {
        "profile_id": "linux_isolated_python/v1",
        "tools": sorted(set(selectedmanagednames)),
        "budget": dataclasses.asdict(IsolatedProgramBudget()),
    }
```

把文档传入 `AgentTaskSpec.tool_program_policy`。`tools` 必须非空、排序、唯一，
并在每个 program 节点绑定。节点显式暴露 `run_tool_program` 与
`read_tool_program_output`。spec 要求 `managed_tool_policy`；程序
`max_parallel` 不得超过共享容量。budget 是完整 dataclass 文档，不能只给部分字段。
字段为 `wall_seconds`、`cpu_seconds`、`memory_bytes`、`process_limit`、
`max_output_bytes`、`max_frame_bytes`、`max_calls`、`max_parallel`、
`max_source_bytes`、`scratch_bytes`。profile 只支持一个程序进程；资源限额不授权效果。

`run_tool_program` 恰好接受 `source`（非空 Python 文本）和 `arguments`
（JSON object）。SDK 注入 `arguments`、`tools`、`ToolCallError`、`result`。
使用显式稳定逻辑 key；child 身份由 program 身份和 key 派生，与完成顺序无关。
只允许选定 managed 名称，每个 child 单独验证 schema 和效果/conflict 准入。

source 隔离 SDK 是同步 API。runtime 执行 module source，不自动调用 `main`，
不 await coroutine、不驱动 event loop。`tools.call(key, name, args)` 返回 child
值或抛出带 `.code` 的 `ToolCallError`。`tools.parallel(calls)` 接受
`{"key": ..., "name": ..., "args": ...}` 列表，按输入顺序返回 reply；每项含
`key`、`ok`、`value` 或 `error_code`。它在准入容量内并发执行业务调用，不把 SDK
变成 async。`tools.read_result(locator, offset_chars=0, max_bytes=10000)` 同步返回
有界 child 页。`result(value)` 发布可 JSON 序列化的最终值。必须明确调用；只定义
函数不会执行任何业务调用。source 是一个程序，不是新 HOST runtime 或脚本自有队列。

下面假设显式选择了 pure `demo_add` binding，输入为 `{a, b}`。请用实际节点绑定
名称和 schema 替换示意值，以 `arguments={"a": 2, "b": 3}` 提交该 source：

```python
def main(arguments):
    replies = tools.parallel([
        {"key": "left", "name": "demo_add", "args": arguments},
        {"key": "right", "name": "demo_add", "args": arguments},
    ])
    return {"replies": replies}

result(main(arguments))
```

有顺序依赖时，可在 `main` 内用
`value = tools.call("first", "demo_add", arguments)`，检查返回形状后传给后续调用。
`result(tools.call("first", "demo_add", arguments))` 也是有效 module-level source。
不要为这些 SDK 方法使用 `async main` 或 `await`。

较大的成功 child 回复变成 `agent_tool_program_child_output_page/v1`，包含
`program_invocation_ref`、`program_call_ref`、`terminal_receipt_ref`、`reader`、
`content`、`offset_chars`、`next_offset_chars`、`total_chars`、`truncated`。
`tools.read_result` 接受完整页或其中三个精确 ref，跟随 continuation 重建序列化
业务输出。它是独立只读 broker frame，不是 allowlisted 业务工具、任意 receipt
reader 或新 invocation。reply frame 外层开销计入预算。
预算无法容纳 child 定位页时，broker 返回明确的
`program_result_budget_too_small` 错误。成功 child 与完整输出仍然持久登记，
可通过已关闭父级 reader 回读；不会把显示预算不足升级为结果未知。

父级 `tool_program_result/v1` metadata 恰好含 `kind`、`program_invocation_ref`、
`output_resource_ref`、`status`、`call_count`、`reader`、`agent_action_ref`。
status 保留 `returned`、`failed`、`cancelled` 或 `outcome_unknown`；action 结算
代表观察，不证明成功。即使部分失败或取消，typed children 与结果 resource 仍持久
保留。无效 child 参数记录为 rejected，计入 call budget，但不执行 worker；已经
执行的 failed/unknown child 保留真实收据。

同一 loop 更早 turn 的已关闭父级可通过 `read_tool_program_output` 回读：传入精确
`agent_action_ref`（`agent_action/v2`）、`output_resource_ref`，可加
`offset_chars`（0）、`max_bytes`（10000，可见上限 10000）。
`tool_program_output_page/v1` 保留 locator、`reader`、`content`、offset/continuation/
total/truncation 和 `source_status`。全部已接受 child 终态后，四种已关闭 status
都可读；只有 started 或 child 未完成的程序不可读。`source_status` 保留真实
failed/cancelled/unknown。应只读已持久成功 child 来取回 ID，不为显示结果重跑失败
程序。unknown 保留既有 reconciliation block，拒绝新调用，不透明重试或恢复执行。

API 源码：[task spec](../cpn/rpnh/agent_tasks.py)、
[工具目录](../cpn/components/agent_loop/tool_catalog.py)、
[managed scheduler](../cpn/plugins/managed_scheduler.py)、
[program owner broker](../cpn/components/agent_loop/program_execution.py)、
[隔离 runtime](../cpn/plugins/controlled_script.py)。
