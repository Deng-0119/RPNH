# 代码与执行路径

[English](DESIGN.md) | [示例入口](README_ZH.md)

**公开结果对照：** [已发布成绩与本例解读](COMPARISON_ZH.md) · [实现差异补充](IMPLEMENTATION_COMPARISON_ZH.md)。直接引用论文／官网结果，不要求在本机实现或运行原始 harness。

| 层次 | 实际代码 | 职责 |
|---|---|---|
| 公共输入 | `src/rpnh_ha/task_view.py::public_task` | 目标、角色和公开工具；不向 executor 提供访问裁判规则、标准路径和完成检查 |
| 应用工作流 | `workflow.py::build_business_workflow` | hub 分发与汇合，不自动制造政策先于写入的依赖 |
| 宿主绑定 | `local_driver.py::managed_bindings` | 各角色绑定全部公开工具；保留参数说明 |
| 原生启动 | `local_driver.py::LocalNativeDriver.execute` | 使用 `AgentTaskSpec` 与 `TaskControl`，实际循环和 Registry 归 RPNH 所有 |
| 业务工具 | `native_plugin.py`、`backend.py`、`bank_transfer.py`、`upstream.py` | 一次注册调用对应原始 OfficeBank 操作，快照保留本地 |
| 观测 | `registry_export.py`、`handoff_capture.py`、`crosswalk.py` | 区分执行回执、实际交接和后续模型输入证据 |
| 评分 | `scoring.py`、`judge_transport.py` | 原始固定 HarnessAudit 函数；仅在评分期间替换传输 helper 为已配置路由 |
| 用户入口 | `example.py`、`cli.py` | 查看、配置、准备、显式执行／评分，不是第二套 agent 调度器 |

`ha_manager`、`ha_admin`、`ha_policy`、`ha_extra` 是宿主槽位，不是不可变业务角色；公共任务决定实际映射。
最多支持三个 specialist。底层包保留 `off-t2` 供历史 smoke 使用，但它不在公开五题结果中。

保留原有工作流与角色指令，没有优化。它是静态 hub／分发／汇合图，不是读报告后可动态重构的管理器。
全部业务工具可见，隐藏评测标签不是运行时权限。参数投影保持实验中的无 `required` 列表口径，
不能宣称与每一种上游框架的参数生成策略完全一致。

工具返回证据与后续模型消费是两个事实。准确 returned 调用可以进入 benchmark 轨迹，不要求再调用模型；
未知外部效果不能升级成成功返回。访问权限、实际读取、消息正文、裁判扫描载荷分别解释，完整产物默认只留本地。

`scripted_model.py`、`scripted_profile.py`、`scripted_acceptance.py`、`native_smoke.py` 是明确的非评分探针；
不是默认路径，不产生历史成绩，不能替代真实模型实验。
