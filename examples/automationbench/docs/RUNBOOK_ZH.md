# 本地运行手册

[English](RUNBOOK.md) | [示例](../README_ZH.md)

## 1. 查看已保留结果

在 RPNH 源码根目录执行：

```bash
python -I examples/automationbench/example.py results
python -I examples/automationbench/example.py results --json
```

该步骤只读，不调用模型或业务 API。

## 2. 独立安装固定上游

使用位于 `4a8e1061254004d9dac807054eed33fad7d1ff14` 的独立 AutomationBench checkout，
不要把其题库复制进本仓库。在隔离环境中安装 RPNH、example 和固定上游：

```bash
python -m pip install -e .
python -m pip install -e './examples/automationbench[test]'
python -m pip install -e "$AB_UPSTREAM"
```

所有生成 work 都放在仓库外，并使用短路径满足 native Unix owner socket 限制：

```bash
AB_WORK="$(mktemp -d)/ab"
```

## 3. 运行确定性检查

```bash
python -m pytest examples/automationbench/tests -q -m 'not integration'
RPNH_AB_UPSTREAM="$AB_UPSTREAM" \
  python -m pytest examples/automationbench/tests/test_offline_runtime.py -q
```

第二条命令使用脚本化 local-process adapter、真实 native worker 和固定 AutomationBench world，
会经过 Unix socket 与 Registry，但不调用真实 provider。
如果系统临时目录对 Unix socket 来说过长，把 `RPNH_AB_TMPDIR` 设为仓库外的短可写目录。

## 4. 准备新条件

`PROFILE` 必须是已经另行授权的 RPNH execution-selection JSON，不是 API key 文件，并且必须留在
仓库外。

```bash
rpnh-ab doctor --upstream "$AB_UPSTREAM" --profile "$PROFILE" --work "$AB_WORK"
rpnh-ab prepare --upstream "$AB_UPSTREAM" --profile "$PROFILE" --work "$AB_WORK"
```

`doctor` 和 `prepare` 不调用模型。默认 public plan 包含全部 600 题；继续前先检查 `plan.json` 和
`conditions.json`。

批次门禁还要求与条件完全一致的 `rpnh-ab/acceptance-manifest/v1`，覆盖 installed runtime、managed
schema/effect、真实脚本化 host 执行、输出边界、超过 48 次调用的无累计上限 fixture、静止停止和
world/rubric 对照。不得用单元测试或 startup probe 伪造 passed manifest。当前公开 example 不包含
权威 manifest producer；该部署绑定记录由 deployment owner 负责生成。上面的 integration test
提供核心 native 证据，但本身不是 passed manifest。在部署方实现并审查 producer 之前，package 的
batch `run` 必须保持阻断。

## 5. 实时执行与离线维护

只有部署方实现自己的受审 manifest producer 后，仍需明确授权和 matching manifest，才能用带
`--acceptance ... --launch-output ...` 的 `prepare` 生成 launch request，再调用
`rpnh-ab run ... --config ...`。这些只是接口名称，不是本仓库提供的开箱即用 invocation。实时 run
可能产生付费外部模型调用。除非修改固定上游（适配器会拒绝），它不会连接生产业务 SaaS 账号。

`score`、`reproject` 和 `summarize` 只能读取已保留冻结证据。`export` 排除私有 profile、原始
Registry 数据库和 provider transcript。不要为了让汇总完整而重跑失败任务；修复复验应使用新条件，
并保留首轮。

保留 pilot 使用任务专属 native driver，完整证据留在私有实验归档中；本 example 没有把它冒充成
通用 batch manifest。仓库内 18 题结果是历史证据；这些命令用于准备新条件，不会自动复现完全
相同的 provider 结果。
