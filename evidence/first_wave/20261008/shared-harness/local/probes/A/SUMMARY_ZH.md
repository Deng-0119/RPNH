# A 原生受管动作导出与双消费者只读验收

结论：**PASS_FINITE**，本职责内无 blocking finding。两项原生 pytest 各 1 PASS，真实 provider/model 调用合计 0；scripted 逻辑调用分别 4、4（合计 8）。未修改产品或冻结补丁，未提交、未运行历史 reproject。

## 核源与执行

- 精确基线 `c545621c30452650202ad0aca2bc023b6956929b`；冻结 A/B 17 文件运行前后 hash 一致，见 `source-verified.json`、`source-final.json`。
- 原样运行指定 `test_node_scoped_managed_calls_persist_v3_alongside_builtin_v2`，保留原有 pure / synthetic external_read 两节点、v3=2、v2=4 断言。结果 `native-start.xml`，用时 57.72s；原始命令、stdout/stderr、时间及 exit0 分别见同名前缀文件。
- 独立新增 `test_native_consumers.py`，通过真实 `run_agent_task` / OwnerEventLoop / AF_UNIX / plugin spawn worker，结果 `consumers.xml`，用时 52.55s。源码及独立新增 diff/hash 见 `local_additions.json`；不属于冻结 A/B 补丁。

## 原生事实与合法映射

本地 fixture 明确声明 first / finalize 两节点，roles_by_node 分别为 local_worker / local_finalizer，实际工具 shared 绑定 pure `localprobe/echo`。插件配置 schema 显式声明 run_id；每个独立 worker 实际处理一个本地请求并返回该 worker 的 request ordinal=1、PID 和 echo 值。它不是 HA Bank 后端序号、业务成功或评分。没有注入 action row、伪造 delivery 或新增产品 API。

实际产生 v3=2、v2=4。两个 worker PID [12, 13]，fixture PID 8。同次 HA collection 包含 2 个 tool_call、1 个当前 terminal→user communication；对应 admitted/settled Registry record 共 4 条。完整 exact refs、最小返回证据见 `native-observations.json`。

v3 两条均为 `provider_delivery_not_recorded`，model_visible_result_ref=null；HA `unproven_managed_results=2`、managed_result_model_input_complete=false。导出验证的是实际 registered managed return，不声称模型输入交付已证明；complete_within_declared_scope=true 仅对应导出器声明的有限范围。

## 双消费者只读结果

只保留 fixture 创建的原 owner；READ 阶段新 owner=0、新 writable core=0。HA 唯一新 core 使用 create=False/read_only=True；environment_host 借用初始 owner 的 core/kernel。两消费者各调用原共享 typed reader 一次，core 不同、cut 不同且各自绑定正确；fixture 仅记录和比较返回值，没有替代匹配权威。

HA 与 environment_host 的 authority / terminal evidence / terminal result exact refs 一致，generation=0、status=terminal、输出 finalize 一致。读取前、HA 后、environment_host 后及最终均为 head=893、writer epoch=2、对象数=268，所有对象字节指纹 `29faafcd0b9369a7d5fefbd1d890e4656a0ffe326d94ceb74209f65de7bef348` 不变。head 增量=0、epoch 增量=0、对象字节变化=0、writer/启动拦截命中=0。见 `read-only-observations.json`，逐对象完整指纹仅留 runtime。

## 边界与交接

current generation、reopened active、running/stopped、错 caller ref、foreign-run、跨 core/cut 及 head/epoch drift 拒绝已引用父任务实际执行 JUnit 中 16 项 PASS，详见 `boundary-offline-refs.json`（包含原 JUnit hash）；不把这些引用另算成本子任务新增测试。drift 是离线注入；真实 OS 并发 writer pressure 为 **NOT_RUN**。历史 reproject、A/B 总体离线结果、集成与 Git 属父任务。

完整原始 Registry、profile、request 等只在 `.h26/an/g` 与 `.h26/an/consumers` 留存。公开候选仅 probes/A 下经过检查的脚本、diff、日志/JUnit、refs/hash/观察结果；不得发布 runtime DB、私有 profile 或原始 provider/request transcript。两个 pytest 已 exit0，runtime 残留 owner socket=0。全部 per-case 命令、状态、证据路径、只读计数和范围限制汇总于 `RESULT.json`。
