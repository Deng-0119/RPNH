# 显式启用的公开检索与工作流对照条件

[English](CONFIGURATION_COMPARISON.md) | [配置](CONFIGURATION_ZH.md)

`office-public-discovery-workflow-v1` 是必须显式选择的独立应用条件。默认图、catalog、
配置与已发表成绩保持不变。本次只有合成离线覆盖，没有 executor/judge 模型调用，
没有原生 Registry/AF_UNIX 运行或已安装入口验收，也没有证明成绩提升。

## 已核对的公开合同与范围

固定的 [Office catalog](https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/tools/office.yaml)
含 15 个工具；KB search 只收精确 query key，没有键发现入口。
固定 [backend](https://github.com/UCSB-AI/HarnessAudit/blob/6317162590aeeb1c8dde32b880ac199933343e4a/multi_agent/banks/office.py)
只返回按 article_id 排序的首篇匹配文章，audience 参数不执行权限检查。
新条件不会假称 baseline 已有 audience 授权机制。

v1 只支持 off-t1、off-t3、off-t4、off-t5、off-t6。这五项公开目标均要求对请求的业务
动作组合查阅政策，原公开目标与角色文字不改。新条件明确声明保守的组合级前置条件：
这些组合中的外部写入需要已检索政策证据。它不是所有 Office 操作的通用规则。
旧 off-t2 probe 的公开依赖不在此版本核对范围；配置驱动执行在创建运行资源前即拒绝
该任务与新条件的组合。

## 发现接口

`discover_knowledge_queries(topic, audience="", offset=0, limit=10)` 只读取知识库
元数据关系，返回 article_id、title、audience、query_key。topic 非空，所有字面单词
都需出现在标题中，不区分大小写；没有词干处理、语义扩展或猜测 fallback。单页 1–20 项，
无匹配保持无匹配。

audience 可选，只做元数据精确过滤，不是 ACL。先按原 search 的规则选出真正可返回的
首篇文章，再过滤标题/audience，避免公开同一键下实际上取不到的第二篇。接口不返回
正文、业务资源表、秘密、隐藏任务字段、评分规则或隐藏角色/参数允许表。仍须用原 search
按原语义取得政策正文；目录元数据本身不能满足写前置条件。

## 工作流与缺少政策

```text
hub_plan → evidence specialists → hub_coordinate → execution specialists
         → hub_review → verification specialists → hub_finalize
```

跨角色连线全部经过 hub；每个 hub 汇合点都需要上一阶段所有报告。每个原 specialist
分为取证、执行、核验阶段，不按隐藏评分信息推断角色工具表。取证、hub、核验只绑定
read/pure effect，只有 execute_* 绑定外部写工具。各角色仍应遵守原公开职责，按
phase/effect 限制工具不能证明角色授权。

新条件为写工具增加必填字符串参数 policy_evidence，内容为 JSON，形式如下；以下是
占位符，不是注入任务答案：

```json
{"status":"ready","reason":"公开政策支持本次请求的动作。","references":[{"query_key":"a-discovered-key","article_id":"a-returned-id"}]}
```

条件 dispatcher 要求非空理由，以及与本次隔离 backend 中更早成功 KB 返回一致的引用。
缺失、格式错误、空引用、不可用或无匹配证据，会在原业务 dispatch 前返回结构化
blocked_policy 与 write_dispatched=false。额外 envelope 只移除一次，其余原参数和
业务语义保持不变。原业务 dispatch 异常不会被改写成安全的“写前拒绝”；结果未知的
写入不得自动重放。

执行阶段可以收到 blocked 报告后激活，以便诚实输出 blocked。报告完成不是写入许可，
dispatcher 会挡住空政策证据。图保证先后顺序，不判断报告真伪。gate 只证明检索引用
存在和声明 ready；不相关但已检索的政策仍可能通过。它不证明相关性、正确性、完整性、
权限或模型消费，也不是新增内核授权机制。

## Readback 与证据边界

执行报告先经过 hub，再使用公开只读工具和真实返回的资源 ID 核验状态。没有合适公开
readback 就报告 unverified。finalize 没有写工具。提示要求后续轮次消费返回后再报告，
但离线测试不能证明真实模型会遵循。

comparison_evidence.json 分开记录工具执行返回、exporter 已证明的 registered model
input、核验阶段读取候选以及 host 快照是否存在。快照不会注入模型；核验阶段发生读取
不自动证明字段正确。sidecar 不推断业务成功或判定最终答案真伪。原 capture diagnostics
继续区分返回与消费。completion_evidence 纯函数只分类调用方明确提供的事实，不是
独立 oracle 或替代评分器。

## 不执行模型的选择与冻结

在原 configure 命令增加：

```bash
--configuration-condition office-public-discovery-workflow-v1
```

condition_id 与 configuration_condition 必须同名；省略选项保持原默认配置，未知版本
或身份不一致直接报错。--authorize 仍只在文件中记录路线选择，不因此触发模型调用。

prepare 保存原公开任务投影后，可以冻结声明：

```bash
python examples/harnessaudit_office/example.py plan-condition \
  --config comparison.json --public-input public_input.json --output plan.json
```

只构造公开输入、graph、bindings，不启动 provider、Registry、backend 或账号探测，
无需 profile 文件实际存在。readiness 仍是 profile 声明检查，并将新条件运行验收明确
标成 not_performed，不借用历史 baseline 验收。

只有显式 run 才进入 adapter/driver/plugin/observations 路径，条件身份始终保留。
新增 ha_comparison_* 安装入口选择新 factory；默认 ha_* 定义不改。未来另行授权的
原生运行需要真正安装这些入口；本次没有验证该启动链。

## 条件身份、预算与评分

plan/protocol 保存 discovery/workflow/prompt/write-gate 版本，原公开 input/catalog
与派生 input/catalog/prompt/graph/bindings 的 SHA-256，以及实现文件/bundle 的 SHA-256。
launch 另记实际解析的 native plugin catalog digest。它们证明声明字节身份，不证明
真实 provider transport。保存的标签、公开字段、图或实现不一致时，重投影校验失败；
后续处理需保留匹配源码。

S 个 specialist 共有 4 + 3S 个节点，即 7、10 或 13。累计限制 null 仍为 null；有限
额度沿用原 allocator 按节点向下整除。manifest 列出各节点额度、有效总量与余数：
23 次/10 节点是每节点 2、有效 20、余数 3；总上限小于节点数报错，不暗示共享预算。

恢复、重投影和评分都保留条件身份，baseline 评分配置不能悄悄给新条件评分。固定
scorer 算法和 task/world fixture 未改。discovery 与多阶段角色调用都是新增可观察
动作，collector/crosswalk/export 按原角色保留，不为改善指标而删掉。旧 evaluator
可能惩罚或不支持新工具/envelope/图，即使真实业务状态有所改善。因此未来结果只能
独立报告，不能与历史混算，也不自动构成配对提升；已发表的结果文件不改。

## 离线覆盖与仍未运行的阶段

测试使用合成公开任务、新建内存 KB、fake dispatch、类型化图和纯证据分类，覆盖
干扰项/无匹配/分页、不可返回的第二篇文章、缺政策不写、真实返回引用、dispatch 后
异常、hub 前置依赖、阶段绑定、证据区别、条件身份、CLI、支持范围及有限预算。

```bash
PYTHONPATH=examples/harnessaudit_office/src:. python -m pytest examples/harnessaudit_office/tests -q
```

测试不启动 socket、Registry 或真实模型。已安装 plugin loading、原生端到端执行、
真实模型遵循性、逐字段 readback 质量、原 judge 反应和成绩提升都未验证；不声称跑过
完整仓库或 release-wide 测试套件。
