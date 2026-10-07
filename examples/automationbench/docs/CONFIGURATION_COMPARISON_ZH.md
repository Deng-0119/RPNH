# 显式选择的 API 契约可见性对照条件

[English](CONFIGURATION_COMPARISON.md) | [示例](../README_ZH.md)

`api-contract-visibility-v1` 是新的显式配置条件，**仅支持 native host**。
默认仍为 `baseline`。历史结果不变、不重评分；任务 prompt、公开任务契约、初始
world、clock、rubric、任务顺序、cohort、依赖 pin、预算和评分规则均不变。
保留不可变初始 world 捕获和已有 null/Trello 参数兼容映射。

## 变化范围

固定上游完成 `api_search` 排序后，统一、与任务无关的元数据层仅修正结果中已返回
的三个已知 endpoint：

- Salesforce Account PATCH 补充 `HealthStatus` / `health_status`、`Tier` / `tier`
  和 `Priority` / `priority`。底层均为 `Optional[str]`；描述惯例不是强制 enum。
  mutator 忽略 null 和空字符串，因此它们不能清空字段。Account create 不变
- Sheets `spreadsheets.get` 如实说明只返回元数据和工作表列表。固定实现接受但忽略
  `includeGridData` 和 `ranges`；两者均不会读取单元格或过滤工作表列表
- Sheets `values.get` 说明显式逐表 URL/range 读取路径，由 actor 选择需要的表。
  裸 A1 range 只指向第一张表；adapter 不会自动读取所有表

URL、endpoint ID、method、结果顺序和数量、未知 endpoint 均保留。搜索索引和
BM25 语料不变，因此补充 request 文档**不会**使新关键词可检索，也不保证发现
相关 endpoint。元数据不包含任务 ID、隐藏断言、精确任务答案或规范标签。

dispatch 前的窄 `api_fetch` 检查可对以下情况返回固定、脱敏的可操作反馈：method/
URL 缺失或类型错误、URL 格式错误、未知顶层参数、query JSON/object 形状错误、
不支持的参数容器类型，以及已核对 Account PATCH 路径的非 object body。
返回标明 `stage: pre_dispatch`、`upstream_dispatched: false`、
`effect_status: not_started`；不回显参数值或异常原文，不计为上游 dispatch。
修正后需使用新的调用身份；相同身份只重放已经保留的返回。

这不是全 endpoint 验证器。未知路由、字段值校验、scalar/array body 契约和文本查询
兼容仍由上游负责。为保留文本 body 特例，body JSON 解析错误也留给上游处理。
一旦开始 dispatch，异常**不会**改写成无副作用参数错误：原失败、可能已修改的
world checkpoint 和去重保护均保留，不新增自动重试。上游 JSON 错误返回也保持
原样，不能据此声称回滚或无副作用。

native harness 在该条件下将既有 `raw_result` envelope 字段如实解释为
**actor 可见**返回；baseline 保留原“exact upstream”说明。公开任务消息不变。
此版本在启动前拒绝 DSH；其条件路径须另行实现和验证。

## 选择并冻结新条件

使用[运行手册](RUNBOOK_ZH.md)中的已授权 profile 和未修改的固定上游，选择新的
私有工作目录。例如：

```bash
rpnh-ab prepare --upstream "$AB_UPSTREAM" --work "$AB_NEW_WORK" \
  --profile "$RPNH_PROFILE" --host native --split simple \
  --configuration-condition api-contract-visibility-v1
rpnh-ab doctor --upstream "$AB_UPSTREAM" --work "$AB_NEW_WORK" \
  --profile "$RPNH_PROFILE" --split simple \
  --configuration-condition api-contract-visibility-v1
```

同一选项也支持既有 `--cohort`，不创建新 cohort、不改 ID 或顺序。真实 provider
实验仍需单独授权。`accept-host` 和 `run` 继承冻结条件，不能在 launch 时覆盖。
仍须对该条件单独生成 installed-host acceptance；组件测试不能替代该验收。

条件 ID/hash 进入冻结 plan、conditions、benchmark spec、attempt、prepared
launch、score 证据、独立 summary 和 acceptance identity。summary 排除条件身份
不匹配的 attempt；scorer 本体及输入/结果不变。hash 覆盖 pin、endpoint catalog、overlay 内容
和相关实现源码；benchmark hash 因此区分 baseline 与 comparison 验收。
`run` 核对 doctor 条件、冻结 plan 和当前条件 hash；acceptance 还核对 attempt
身份。catalog/overlay 变化需要新的工作目录和匹配验收。

## 证据与限制

每个 comparison `api_search` 调用的 `api_search_metadata_events.jsonl` 分别
保留原始上游和 actor 可见字符串、JSON canonical 字符串 hash、改动的 endpoint
ID、request 序号/hash 和条件身份。文件进入白名单导出并列入导出字节清单。
原始参数仍在私有 tool events 中。plugin/Registry witness 对应 actor 可见字符串，
而非原始上游字符串；登记成功不证明后续模型实际消费。

`pre_dispatch_rejected` 与 `dispatch_started` / `dispatch_finished` 分开记录。
证据核对分别统计返回的 preflight 拒绝与上游返回，不将前者当作业务成功；不放宽
scorer、历史评分或结果分类。

本次仅做合成离线验证。新增测试覆盖元数据保真、条件身份、脱敏 preflight、
dispatch 后部分修改异常、导出证据和 baseline 行为。另用标准库提取已校验 blob
的固定上游函数，在合成对象上核对非 enum Account 写入/读回、只含元数据的多表
列表、显式逐表单元格读取及裸 A1 只读首表。它不等于完整上游包、Pydantic、
provider、socket、Registry 或 installed-host 集成验收，也不能证明历史评分提升。
