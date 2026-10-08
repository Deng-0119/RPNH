# 原始失败证据候选包

本目录供父任务审阅后发布，以分析环境、配置、输入交付、模型/工具行为及原始评分的多因素关系。
日志、条件记录、完整 reward/rule_results/spend/optimality/checks 与工具命令尽量保留原字节；确认的实际凭据和私有端点仅作最小替换。
MANIFEST.json 逐文件记录原始/分发摘要、原始路径或精确 Registry 引用、字节变化和脱敏区间。区间均为原始 UTF-8 字节的零基半开范围；账本不含密钥值。

注册请求是 logical_provider_request_recipe/v1 原件；响应区分 adapter-return 与规范化版本。两份响应版本不等于两次模型调用。
AgentAction 保留原始记录字段的序列化表示，包括参数、结果及资源引用；不将它称为原始物理 JSON 消息。events.json 是持久事件的明确 metadata-serialization，并非供应商网络包。
部分历史工具资源的 provenance 缺少当前 fresh-reader 所需的 agent_loop_ref；保留该校验失败，另以精确 canonical object 身份、envelope 和大小读取原件。此回收不代表原 provenance 校验通过。摘要是本次对保留字节的实测值。
未保留的 vendor wire/完整 CLI event stream 明确为 NOT_FOUND，没有用重构消息冒充原始 wire。

历史 WSL、任务、源码及 AF_UNIX 路径可用于还原错误上下文，不是访问授权或当前可运行地址。
未复制完整 profile/config/auth、Registry DB/WAL/SHM、数据库 dump、tar、缓存。Odoo API key 仅从原 tar 中读入内存用于匹配，从未解包或另存。
存在不确定凭据的个别文件留待审阅，详见 exclusions；不能把缺失内容解释为没有发生动作。

S01/S02 启动失败、S03 原始零分及 H01 原始评分按各自源码身份保留，不因后续集成而重标。S04 仅含公开条件对照。
原始 manifest 的摘要仍指向原件；如对应文件被脱敏，应使用本包 MANIFEST.json 的分发摘要核验。
这是候选材料，不代表新的运行、业务成功、模型优势或父任务已批准发布。
