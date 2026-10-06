# AutomationBench 公开结果 — 2026-10-06

[English](PUBLIC_RESULTS_20261006.md) | [中文](PUBLIC_RESULTS_20261006_ZH.md)

首轮 first18 使用 **freeze04**：计划18、启动16、评分14，5 PASS / 9 FAIL /
4 BLOCKED。独立 **repair** 条件执行四题，1 PASS / 3业务FAIL，原生与独立评分等值。
旧14道已评分题未重跑。必须理解为 first18 加 repair4，不能混成新源码18题已完成或
6/18通过率。[2026-10-02 pilot](RESULTS_ZH.md) 是更早的独立实验。

## 条件与身份

AutomationBench与未改动的严格native scorer固定于
[`4a8e1061254004d9dac807054eed33fad7d1ff14`](https://github.com/zapier/AutomationBench/tree/4a8e1061254004d9dac807054eed33fad7d1ff14)。
seed为`20261006`，排除38个既往/辅助任务，在调用前按六业务域×三集成宽度各冻结一题，
未使用rubric难度或结果选题。C3是在停止后建立的执行分区，仅承载原先已冻结的三道
marketing题，不是预注册条件，也没有换题。

实际请求模型为`gpt-5.6-terra`、medium、native local-process bridge，无fallback。
这只是实际执行条件，不是产品默认模型、推荐供应商或账号。响应模型ID与供应商请求ID
仍为null；业务SaaS world是本地模拟。`support-0044`等是adapter别名，不是官方native ID。
官方loader没有专用native ID列，因此保持null；上游名称、0基行索引、原始example ID及
固定源码链接在任务表中独立保留。

## 首轮18题与修复4题

| 别名 | 首轮状态 / partial | 修复执行性质 | 修复状态 / partial | 修复提交 / 响应 |
|---|---|---|---|---|
| finance-0009 | PASS / 1.0 | 未重跑 | — | — |
| finance-0002 | FAIL / 0.5 | 未重跑 | — | — |
| finance-0022 | FAIL / 0.6666666666666666 | 未重跑 | — | — |
| hr-0021 | FAIL / 0.16666666666666666 | 未重跑 | — | — |
| hr-0001 | FAIL / 0.0 | 未重跑 | — | — |
| hr-0055 | BLOCKED / null | fresh-world独立验证 | FAIL / 0.8 | 21 / 21 |
| marketing-0025 | FAIL / 0.5 | 未重跑 | — | — |
| marketing-0096 | FAIL / 0.6 | 未重跑 | — | — |
| marketing-0090 | PASS / 1.0 | 未重跑 | — | — |
| operations-0005 | PASS / 1.0 | 未重跑 | — | — |
| operations-0073 | PASS / 1.0 | 未重跑 | — | — |
| operations-0052 | FAIL / 0.0 | 未重跑 | — | — |
| sales-0091 | FAIL / 0.2 | 未重跑 | — | — |
| sales-0054 | PASS / 1.0 | 未重跑 | — | — |
| sales-0048 | FAIL / 0.25 | 未重跑 | — | — |
| support-0063 | BLOCKED / null | fresh-world独立验证 | PASS / 1.0 | 8 / 8 |
| support-0044 | BLOCKED / null | 首次执行 | FAIL / 0.47058823529411764 | 20 / 20 |
| support-0011 | BLOCKED / null | 首次执行 | FAIL / 0.0 | 17 / 17 |


首轮hr-0055因unsupported-item拒绝中止，具体类型/根因未知；support-0063因provider
capacity中止。两题均已启动、含unknown submission outcome、无分数。support-0044与
support-0011未启动。四题首轮分数保持null而非0，仍属于18题分母；完整通过率/均分未知。

独立smoke `simple-0177` PASS，9提交/9响应，不计主批。主批164/162；含smoke为173/171。
两次失败提交usage未知，因此完整usage、实际账单和wire请求数未知。CLI提交不是wire请求。

修复四题按support-0044、support-0011、hr-0055、support-0063顺序分别20/20、17/17、21/21、
8/8，共66/66。四题均host terminal且host/world-owner quiescent；原native revision1和独立
revision2评分等值。评估完成不把业务FAIL变为PASS。无额外live smoke、fallback、旧unknown
回放或额外容量重试。

66响应保留canonical input=1,985,969、output=25,260，相加total=2,011,229，是累计值而非单次
context。cached input=303,360、cache-write input=0、reasoning output=10,248单列，不重复相加。
上游usage来源未保留，这些值不是独立观察的provider计费。账单、wire数和response model ID
仍为null。全部66次Registry与CLI context均为本地声明272000，成功stderr预算摘要66份已核验。
实际初始materialized world与clock未捕获，均null。

## 失败解释

旧9个业务FAIL中8个支持主要agent行为原因。sales-0048保留公开契约疑点：健康等级算对但写在
Description，评分要求health_status，而公开更新字段漏HealthStatus。不替换原生分数。
finance-0002金额格式评分问题和marketing-0025日期/路由歧义不取消各自独立成立的业务遗漏。

- support-0044：遗漏可发现的Report Config中`Exempt_Org_ID`和`Batch_Reference`，纳入内部
  q07/q08，报告漏标识且受影响统计错误；读取、标签、append和发信均工作。
  `data-quality-issue`与`quality_issue`精确命名仍有公开契约疑点，不能把全部子项唯一归给agent。
- support-0011：只读默认Customers页，漏Win-back Config、Spend History、Outreach Log。
  全22次业务调用仅search/read（POST为contact search），没有发信、标签、append或Slack写入。
  孤立不支持的GET 404不是必要写操作被阻断的证据。
- hr-0055：漏读要求检查的部门调动邮件，沿用旧tracker，遗漏Ravi的HIPAA通知；发信成功。
  BambooHR路由疑点不是该扣分项已建立的原因。

补充支持这三次尝试的第1类资料发现/选择/执行不足，不证明模型一般无能力或反事实必然成功。
三题必要操作的实际因果harness阻断未建立。旧unsupported-item根因仍未知，不再出现不等于
根因修复；容量证据未证明context溢出，也不证明服务容量全面恢复。

## 范围与公开文件

SpreadsheetBench V1与SWE-bench Verified仍因既有adapter/环境缺口BLOCKED；Office条件未触发，
NOT_RUN；GAIA/tau2仅候选。人工审阅NOT_RUN，预训练污染UNKNOWN，强worker OS隔离UNPROVEN。
不是全榜单、模型排名、无污染或全部机制验收。

既有修复writer测试52项、独立50个不同用例，存在重叠，不能相加为102 unique；两次独立的
installed native-host离线准备各7项通过。这些是先前证据，不是本次发布新测。
另见[有限验收](../../docs/guides/release-validation_ZH.md)与
[bridge配置](../../docs/guides/configuration_ZH.md)。

- [任务身份与上游链接](results/public-tasks-20261006.json)
- [按条件分离的分数](results/public-scores-20261006.json)
- [计数、条件和provenance](results/public-provenance-20261006.json)

本页是冻结cohort、native/独立评分、失败分析与终审记录的精简字段投影，不是人工业务审阅。
原始证据留本地，不含gold答案、输入dataset、world、transcript、私有RESULTS复制或ZIP。
保留[既有声明](THIRD_PARTY_NOTICES.md)及
[上游许可证](https://github.com/zapier/AutomationBench/blob/4a8e1061254004d9dac807054eed33fad7d1ff14/LICENSE)，
第三方API结构仍有其自身权利。
