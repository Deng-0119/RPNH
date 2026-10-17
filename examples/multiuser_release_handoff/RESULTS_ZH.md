# 原始 episode：结果与源码边界

[English](RESULTS.md) · [示例](README_ZH.md) · [机器可读检查摘要](result-summary.json)

2026-10-16T20:18:43Z 的最终只读审计为 PASS：必需检查 98/98 通过；全部 108 项中 106 PASS、2 项可选 UNKNOWN。审计没有模型调用、业务重跑或 Registry 写入。公开摘要只投影选定字段，不分发原始日志、私有世界、Registry 数据库或提供方转录。

## 精确业务产物

- 本地 Drive 规范文件 `sql-export-guard_release_note.txt` 达到版本 2。稳定可变 slot 为 `logical_slot:7196b636140b5ac497f6c8aeec492412`；当前不可变正文为 `resource:fd86078165c4518fb9b40fb38fc40375` / `resource_version:bf4afb66fee356dda64db05f85fbcca8`。slot 稳定不要求正文身份不变。
- 原始 SQL commit `961cbb286424a90892f8111f79ded17d0837c138` 保留于本地 `platform_admin/mini-seed-repo` 的 `coord/sql-export-guard` 分支，路径为 `sql/patches/sql_export_guard.sql`，完整内容见 result-summary.json。SELECT 对 SQL NULL payload/status 返回 `{}` / `pending`，保留非 NULL 值，不修改存储行。JSON null、空字符串和 SQL NULL 互不等同。
- QA 检查表 commit `4d68b6105fc7cbc3e2632ba75b5d590fbae0ae20` 的父提交是原 SQL commit，路径为 `qa/checklists/sql-export-guard_qa_checklist.md`。原始三条模板与真实验证发现分别保留。
- 只创建一个事件 `incident:c576433b180840b1bd1819f7e1c1c9ad:v1`；[原始事件字段](incident-summary.json) 未改写，实际验证证据另附。
- QA、支持重读 v2 并释放精确 claim 后确认。验证/接受确认时间分别为 QA `2026-10-16T18:41:10.188093Z` / `18:43:04.500471Z`，支持 `2026-10-16T20:02:58.502536Z` / `20:04:30.029975Z`，均绑定当前正文版本及原 SQL commit。最终交接已发布，之后是否消费不作断言。

V2 说明包含上线前提、有限的 NULL/非 NULL 与下游检查、受影响导出的隔离、失败产物/日志/源 `IS NULL` 证据保留、旧查询恢复及受控再生成。回退查询不修复已导出/导入的产物；文字指引与静态检查不代表已通过数据库测试或执行回滚。

## 历史运行时、恢复与计数

Run02 实测运行时为 `7e27f27c3474ceb5a1ce4b82e4df624c28529f30`，包含公开基线 `798b4260e33cfb7f6476eee17e9f48de9ba998f2` 之外的保留依赖，不冒称对该公开基线或后续发行物的干净运行。显式检查点重入、HOST JSON 修复已公开；新示例还需要累计调用上限、上下文/head 查询支持及保留的 Petri 输入修正。参见匹配的[运行说明](RUNNING_ZH.md)。

旧授权失效、重入时孤立 Source 根、支持派发前嵌套 JSON 序列化失败均保留。恢复读取永久原生状态，在同一世界核对副作用；只续做运维/支持未完成工作，开发与 QA 保留终态。新世界运行不替代本恢复证据。

| 窗口 | 适配器提交 | 返回 |
|---|---:|---:|
| 原始基线 | 67 | 64 |
| Run01 | 72 | 71 |
| Run02 | 13 | 13 |
| 续跑合计 | 85 | 84 |
| 累计 | 152 | 148 |

Run02 运维 6/6、支持 7/7，设计/开发/QA 增量零。可用 CLI 完成记录累计 149 条：input 4,821,361、cached input 338,304、output 65,509、reasoning 5,417。一次停止中的运维 CLI 完成但适配器未返回，因此 149 与 148 不同。三次基线尝试缺失 CLI 用量，仍为 UNKNOWN。缓存属于 input 子集，reasoning 不重复加到 output；账单及提供方内部 HTTP 次数未知。计数不包含软件工作会话。

业务窗口退出 0，峰值 1,288,105,984 字节，OOM0；只读审计和捕获窗口同样退出 0、OOM0。这些是测量窗口，不是通用最低内存要求。

## 证据边界

原始评测器 NOT_RUN，官方分数 NOT_COMPARABLE_ADAPTED；无匹配对照、优越性、生产部署、外部身份联邦或全产品认证结论。开发/QA 可选历史切点完整性仍 UNKNOWN，保留终态图片是真实捕获。公开整理没有新增实跑声明。

## 公开入口验证

[发布验证](release-validation.json)单列源码与包装检查：指南固定原任务获取及配置可操作，零provider原生预检通过，80项运行时／示例定向检查无skip通过；复现并修复两项收尾缺陷后三个driver控制流测试通过。隔离安装的核心也通过schema／cap传输／CLI检查。这些检查没有向历史核算增加真实模型／provider调用，也不重评原业务episode。
