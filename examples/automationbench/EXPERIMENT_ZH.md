# 已发表实验条件

[English](EXPERIMENT.md) | [实验结果](RESULTS_ZH.md) | [当前扩展适配](EXTENSIONS_ZH.md)

本页只描述 2026-10-02 已保留实验。后来增加的 runtime、宿主、前端、acceptance、cohort、评分或
导出能力不会改变该实验条件，也不能反向改写已发表结果的来源。

## 当时实际使用的条件

| 边界 | 已发表 18 题 pilot |
|---|---|
| 操作环境 | WSL Linux |
| 执行引擎 | RPNH native worker 与 Registry |
| 工具传输 | 本地 AF_UNIX socket，连接一个串行 AutomationBench world owner |
| executor 标签 | `deepseek-v4-pro` |
| provider 标签／传输 | `volcano`，HTTPS |
| 业务系统 | AutomationBench 本地模拟 world，不是生产 SaaS 账号 |
| AutomationBench | `1.0.6`，commit `4a8e1061254004d9dac807054eed33fad7d1ff14` |
| RPNH | 前 8 题：`3492ba2`；后 10 题与 remediation：`1da3648` |
| 评分 | 固定上游 `partial_credit` 与严格 `task_completed_correctly`；无 LLM judge |

Codex、OpenCode、Basic 与 DSH 都不是本次已发表 pilot 的执行证据。它们的当前扩展支持单独记录，
不会把历史结果追溯性地改写成跨宿主实验。

## 冻结选题与结果

score-blind 计划从每个“public 业务域 × 集成宽度”单元选一题，共 18 题。严格首轮保留所有计划
槽位：

- 尝试 18 题；
- 17 次基础设施闭环并形成可评分 final world；
- 8 题严格完成；
- 17 个可评分首轮的平均 partial credit 为 0.8877005348；
- 437 次 executor 模型调用；
- 1,081 次成功业务工具分派。

`operations-0009` 的失败首轮继续保留在 first-attempt 记录中。后续 1.0 remediation 是独立工程
结果，不改变已发表的 8/18。

## 不可改写的公开记录

| 文件 | SHA-256 |
|---|---|
| `results/conditions-20261002.json` | `cf640896fa90712242831f91129e97771418dce9403e512881ecef3ac9b30bb1` |
| `results/stratified-pilot-plan-20261002.json` | `7f99846bd6247987dd9030342beaa9c0cd70ac675da35dcb30e9abd056f68f11` |
| `results/stratified-pilot-first-attempt-20261002.json` | `31d87b37c5475257a8c7d3d918c11363c180d912d09aaef0b5ddeae11f66767b` |
| `results/operations-0009-remediation-20261002.json` | `89b7023928ffee28c9a63a1aba4acbb138f5f8ae446fa25dfdda17c9131ebfe8` |

扩展回归测试锁定这些字节。新的 acceptance、用户 cohort、Registry、profile 与 export 只能写入
调用者在仓库外提供的 work 目录，绝不更新 `results/`。

## 证据边界

公开记录支持核对计划、逐题汇总、累计数值和已披露执行条件。原始 Registry 数据库、provider
transcript、私有 execution profile、凭据和本机绝对路径有意不公开；这会限制第三方重放历史
provider 请求，但不表示内部一致的公开成绩有错。

只有在任务身份、scorer、算术、冻结状态资格或所述实验条件被证明错误时，才应考虑重做历史
pilot。后来完善可复用 example 本身不是重做实验的理由。
