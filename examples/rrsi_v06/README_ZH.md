# RRSI v0.6 application 示例

[English](README.md) | 中文

本示例在现有 RPNH Registry 与 PetriNet 运行时之上实现一个有界、本地的 RRSI v0.6
application。它不增加第二套 harness，不修改 `cpn/`，也不绑定特定 provider 或模型。
Analyst、Digester、Proposer、Critic 与 Policy occurrence 都是独立的 RPNH child run，
各自保留 Registry 证据。

本项目的目标是验证框架级可行性：以公开 RRSI 方法代表当前先进的 RSI 框架，证明它能够
作为 application 在当前 RPNH harness 上实现并正常运行。“先进/SOTA”修饰的是所实现的
RSI 框架，不表示这个本地 fixture 已复现论文 benchmark 分数。

仓库内的 timeout fixture 用于演示 application 与选择机制；它不是 Google RRSI 的官方
benchmark domain，也不复现论文报告结果。官方源码与 domain adapter 已公开在
[`google-research/rrsi`](https://github.com/google-research/rrsi)，其外部 benchmark 依赖
未复制到本仓库。边界与第三方声明分别位于同目录的 `DESIGN_ZH.md` 和
`THIRD_PARTY_NOTICES.md`。

## 结构

```text
固定协议 + 用户自有 execution profile
                 |
                 v
校准 -> H0 evolve -> 两轮 protocol round -> H0/final heldout -> export
                          |
                          +-> Analyst child Registry
                          +-> Digester child Registries
                          +-> Proposer child Registry
                          +-> Critic child Registry

每个评分 slot -----------------------------> Policy child Registry
```

campaign 只负责 application 协调。每个面向模型的 occurrence 都复用现有 registered-host
binding、`start_run`、`Harness.exact_execute` 与 PetriNet 转移校验。CandidateManifest 使用
显式 run/resource 引用和源文本，不引入 hash、checksum、fingerprint、第二个 Registry
或新的 workflow 服务。

## 安装与测试

在 RPNH 源码根目录执行：

```bash
python -m pip install -e .
python -m pip install -e './examples/rrsi_v06[test]'
python -m pytest examples/rrsi_v06/tests -q
```

测试使用脚本化 input port，不调用真实 provider。RPNH 根项目的默认测试发现不会自动
包含这个独立示例，因此应显式运行上述命令。

## 使用自己的 profile 运行

以下命令会调用用户自有 RPNH execution profile 所配置的模型；只有在明确授权本次实验后
才应执行。run 目录和 profile 都应位于仓库之外。

```bash
: "${RPNH_EXECUTION_PROFILE:?Set an authorized RPNH execution profile}"
export RRSI_WORK="$(mktemp -d)"
python examples/rrsi_v06/example.py \
  --run-root "$RRSI_WORK/run" \
  --execution "$RPNH_EXECUTION_PROFILE" \
  --protocol examples/rrsi_v06/protocol.example.json
```

目标目录必须尚不存在。报告写入 `$RRSI_WORK/run/formal-report.json`；每个 child Registry
保留在 `roles/` 或 `policy/` 下。例如可查看一个 Policy run：

```bash
rpnh net --run "$RRSI_WORK/run/policy/evolve-h0/H0/evolve-missing-0" \
  --view --no-open
```

本示例不提交 execution profile、Registry 数据库、run 目录、provider transcript 或历史
实验结果。

## 完成语义

`formal_rrsi_v06_local_complete=true` 表示这个冻结的本地 fixture 已用真实
Registry/PetriNet/provider 证据完成全部计划轮次与 Policy slot。注入的测试 runner 只能证明
结构行为，不能产生该完成声明。这个声明不覆盖官方 coding、workspace 或 engineering
benchmark payload，也不声称 strict AgentLoop conformance 或 B1 突然丢失自动对账。
