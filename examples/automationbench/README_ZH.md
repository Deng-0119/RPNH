# AutomationBench 公开任务 × 原生 RPNH

[English](README.md) | 中文

本 example 保留了一次真实、score-blind 的 AutomationBench 18 题分层 pilot，以及生成结果所用
的 RPNH native 适配器。试验覆盖六个 public 业务域和三种集成宽度，使用真实外部模型路线；
AutomationBench 的业务 SaaS 仍是本地模拟世界。

严格首轮结果为 8/18 题满分、17/18 基础设施闭环、437 次模型调用和 1,081 次成功业务工具
分派；17 个可评分首轮的平均 partial credit 为 0.8877005348。唯一基础设施失败被原样保留，
随后在独立修复复验中通过；复验不覆盖首轮记录。

- [结果与逐题得分](RESULTS_ZH.md)
- [与公开 600 题报道的比较](COMPARISON_ZH.md)
- [设计与执行边界](DESIGN_ZH.md)
- [本地运行手册](docs/RUNBOOK_ZH.md)
- [实验协议](docs/PROTOCOL_ZH.md)
- [证据规则](docs/EVIDENCE_ZH.md)
- [离线验证](docs/OFFLINE_VERIFICATION_ZH.md)

## 查看已保留结果

以下命令不需要安装 package，不调用模型，也不写文件：

```bash
python -I examples/automationbench/example.py results
python -I examples/automationbench/example.py results --json
```

仓库内机器可读记录为：

- `results/stratified-pilot-plan-20261002.json`
- `results/stratified-pilot-first-attempt-20261002.json`
- `results/operations-0009-remediation-20261002.json`
- `results/conditions-20261002.json`

原始 Registry 数据库、provider transcript、execution profile、凭据和本机绝对路径不会提交。

## 结构

```text
public task prompt -> RPNH native actor -> 受管 AutomationBench 工具
                                           |
                                           v
                                  串行化本地 world owner
                                           |
                                           v
                              冻结 final world -> 上游严格 rubric
```

RPNH 负责模型执行、受管工具准入、生命周期和 Registry 证据；独立安装并固定版本的
AutomationBench 负责题目加载、模拟业务世界、endpoint 实现和程序化终态评分。适配器不增加
第二套 agent loop，也不增加 LLM judge。

正式适配器包含两项窄范围兼容规则：JSON 字符串 `"null"` 转为缺省可选参数；仅对 Trello
`POST /1/cards/{id}/idLabels` 将 JSON 标量标签包装为上游要求的 `{"value": ...}`。模型原始参数
仍保留在工具事件证据中。

## 安装与测试

在 RPNH 源码根目录执行：

```bash
python -m pip install -e .
python -m pip install -e './examples/automationbench[test]'
python -m pytest examples/automationbench/tests -q -m 'not integration'
```

把 `RPNH_AB_UPSTREAM` 指向独立安装且位于
`4a8e1061254004d9dac807054eed33fad7d1ff14` 的 AutomationBench checkout，可运行固定上游和
脚本化 native-worker 集成检查；这些检查不调用真实 provider。

## 使用自己的 profile 运行

适配器包含 `doctor`、bridge `acceptance`、`prepare`、带门禁的 `run`、`score`、`reproject`、
`summarize` 和 `export`。当前公开 example 不提供部署专属的七项 acceptance-manifest producer，
因此 batch `run` 有意不是开箱即用入口。部署方若补充 producer，仍须另行授权 RPNH execution
profile 并保留绑定条件的证据。profile、work 目录和 Registry 都应放在仓库外；详见
[运行手册](docs/RUNBOOK_ZH.md)。

默认准备路径可以选择全部 600 个 public task，但本 example 保留的结果仅是冻结的 18 题 pilot。
它不是 AutomationBench 官方榜单提交，不是 public-600 代表性估计，也不是与其他模型或 harness
的同条件比较。

## 源码版本

- AutomationBench：`4a8e1061254004d9dac807054eed33fad7d1ff14`。
- RPNH 执行核心：前 8 题记录 `3492ba2`，后 10 题及复验记录 `1da3648`；两提交间 `cpn/`
  没有差异。

本目录不复制 AutomationBench 题库；详见[第三方说明](THIRD_PARTY_NOTICES.md)。
