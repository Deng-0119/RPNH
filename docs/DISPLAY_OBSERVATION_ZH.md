---
name: rpnh-display-observation
description: "定义只读展示观察边界和证据限制。"
metadata:
  document-kind: reference
  audience: operator-and-developer
  language: zh-CN
  counterpart: DISPLAY_OBSERVATION.md
  revision: "2026-09-25.1"
  status: source-reviewed-pre-release
---

# 只读展示观察（仅当前态）

`cpn.rpnh.inspection.project_registry_observation(run_dir, *, catalog)` 是可信
看板宿主按需调用的 Python 接口。它不改变原有 `project_registry_net()`、
`rpnh/net_view/v1`，也不新增 CLI 命令或浏览器路由。

```python
from pathlib import Path
from cpn.rpnh.inspection import project_registry_observation
from cpn.rpnh.registry.schema_catalog import SchemaCatalog

snapshot = project_registry_observation(Path("/path/to/existing/run"), catalog=SchemaCatalog())
```

调用方须传入该 run 实际使用的 catalog；上述默认 catalog 仅适用于确实使用它的 run。
返回 `rpnh/net_observation/v1`，包括：

- `source`：单个已有 run、task ID、精确 net 引用、观察到的 head ordinal 和 writer epoch；
- `net`：不变的 v1 当前态 PetriNet 投影，marking 数量来自已验证 checkpoint；
- `boundaries`：声明的入口／出口 port、place 与终止规则；声明**不是**终态证据；
- `transition_bindings`：每个 transition 既有的精确 node、operation binding 和
  executable binding 引用，以及编译后 operation、输入／输出 port 的作用域内 ID 与
  place；不生成新的步骤身份；
- `coverage`：明确注明 firing 历史与 token 轨迹 `not_provided`，as-of 历史
  `unsupported`。

读取以 `create=False, read_only=True` 打开 Registry，复用现有已验证的当前态
hydration；组装结果前后核对 head ordinal 与 writer epoch。发生变化时拒绝拼接
快照，调用方可以稍后重读。run 不存在时不会创建；不获取 writer 权限，不运行
executor、不恢复 run、不发布新事实，也不写 Registry。

既有 v1 投影包含 operation config。宿主在发送给浏览器或缓存前，**必须先**按自身规则
完成披露限制；切换视图不会提升权限。新边界响应不复制 terminal tool config，不把
`node_synopsis` 当作 UI 文字（它也参与模型提示词）。v1 edge ID 的作用域是所投影的
声明，不是跨版本的全局身份。

这是当前观察，不是历史回放。`hydrate_module_runtime()` 读取的是**最新** adopted net
和 checkpoint；不能给它贴旧 ordinal，不可把完整的 `ordered_firing_record()` 误当
as-of 记录，也不能把 provisional observation 提升为 canonical authority。有界规范
checkpoint 索引可以作为后续单独验证的只读增量；在途细节、完整事务边界、生效时间、
并发结算前驱和结构迁移必须分别证明后才能宣称时间轴可用。UI 分组、卡片文案与动画
仍不属于 main。

可见的 provisional firing、live workspace 文件或进程退出都不是终态结果。看板必须把
此类文件标为未 settlement；只有 Registry 同时存在规范的
`run_terminal_evidence/v1` 与 `final_result_index/v1` 对象时才能报告完成。Viewer 始终
只读；它既不结算 firing，也不会把 provisional workspace view 变成已登记 revision。

Display-observation API 本身仍隔离在 `cpn/rpnh/inspection.py`，不改变身份、Registry、
Petri 准入、结算、provider、resume 或 schema。针对性测试覆盖旧 v1 兼容、多个声明
边界、缺失 run 不创建、混合 head 拒绝及观察期间真实 SQLite writer 提交。产品、可读性
和浏览器验收与本观察契约分开，参阅 [viewer 指南](guides/viewer_ZH.md)。
