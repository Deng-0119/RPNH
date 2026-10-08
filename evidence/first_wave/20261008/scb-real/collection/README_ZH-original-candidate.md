# SCB 原始运行证据候选包

仅供父任务隐私审阅后发布。prefix3 不等于完整五 checkpoint。
保留原请求 recipe、adapter 返回/规范化响应、工具命令与输出、owner/launcher/grade 失败及选定提交源码。
AgentAction 保留原始记录字段的序列化表示；metadata 文件不是原始物理 JSON 消息或 vendor wire。
actual_model_call_counts 原 tuple 顺序为 settled_total_calls、post_limit_excess_calls；第二项不是 fake 次数。real/fake 另按实际 provider/selection 与 fixture 边界判断，excess=0 不代表 fake=0。
未保存的 wire、溢出输出及未知 usage 不重构、不补零、不重跑。
只做确认凭据的最小替换；MANIFEST 记录原始/分发摘要与零基半开字节区间。
原错误顺序、case 预期数据和历史绝对路径保留；路径不代表访问授权。
不含完整 profile、执行配置、DB/WAL、缓存或上游测试/oracle/reference solution 文件。
snapshot 是 agent 提交源码，不是上游参考解。未通过 provenance 的资源回收单独标记，不能冒称校验通过。
