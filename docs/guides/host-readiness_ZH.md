---
name: rpnh-host-readiness
description: 比较惰性 HOST 声明，不产生执行或权限声明。
metadata:
  document-kind: guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: host-readiness.md
  revision: "2026-10-06.1"
  status: offline-validated-initial-slice
---

[English](host-readiness.md) | [中文](host-readiness_ZH.md)

# 仅声明数据的 HOST readiness 诊断

本首切片只回答一个问题：**所需声明的精确内容是否匹配提供的惰性 HOST 声明快照？**
它不准备、不授权、不执行候选，不新增 Registry schema、图发布、采用、预约或执行入口。
已有持久 v1/v2 readiness 记录的原有含义保持不变。

API 位于 `cpn.rpnh.collaboration.host_readiness`。本切片没有新 CLI 命令；诊断不能经过
会预先发现或加载插件的 CLI 路径。

## 对已准备的 HOST 取声明快照

准备 HOST 代码是另一个显式步骤。可信应用已准备好具体的 `Registration` 后，
`snapshot_host_declarations(registration)` 只复制 `registration.declarations()`。
它不会调用 `resolve`、组件 lowerer、executor、tool、analyzer、插件 factory 或 `load_catalog`。

下面是完整的离线示例，其中哨兵 callable 必须始终不执行：

```python
from cpn.rpnh.registration import Registration
from cpn.rpnh.collaboration.host_readiness import (
    diagnose_host_requirements,
    snapshot_host_declarations,
)

# 显式 HOST 准备，发生在诊断 API 之外。
def sentinel_executor(**kwargs):
    raise AssertionError("diagnostics must not execute this callable")

registration = Registration()
registration.register_executor(
    "example/echo/v1",
    sentinel_executor,
    identity={"implementation_id": "example.echo", "revision": "v1"},
    contracts={"transport": "deterministic"},
)

snapshot = snapshot_host_declarations(registration)
required = {
    "executor": {
        "example/echo/v1": registration.declaration("executor", "example/echo/v1")
    }
}
report = diagnose_host_requirements(required, snapshot).to_dict()
assert report["declarations_status"] == "matched"
assert report["execution_ready"] is False
assert report["permission"] == report["capacity"] == "not_checked"
assert report["reservation"] == "not_reserved"
```

`required` 是作者 HOST requirements 或 compiled wire 中已有的嵌套 `registrations`
对象，不是整个 host-requirements envelope。支持 `schema`、`component`、`executor`、
`tool`、`analyzer` 五种声明。需求可只选子集；快照中额外的声明不会变成额外需求。
输入容器必须是标准 JSON dict/list，数字有限、键为字符串，声明字段闭合且 kind/key
与外层地址一致。可执行声明要求非空 identity、对象 contracts，并拒绝可执行 locator
字段；schema 声明要求 `$id` 匹配及既有 Draft7 dialect。Python adapter、循环数据和
畸形字段抛出 `TypeError` 或 `ValueError`。

## 如何读报告

`declaration_checks` 的每项都有 kind/key、status、reason，以及所需和已有声明的精确摘要：

- `matched` / `exact_declaration_match`：完整声明内容一致
- `missing` / `missing_host_declaration`：提供的快照缺少该项
- `mismatch` / `declaration_digest_mismatch`：同一 kind/key 的内容不同
- `not_checked` / `no_host_snapshot`：未提供 HOST 快照

总体 `declarations_status` 在没有快照时为 `not_checked`；否则只要有不匹配项就是
`mismatch`，没有不匹配但有缺项则为 `missing`，全部匹配才为 `matched`。
同时存在缺项与不匹配时，各项原因仍分别保留。空需求对于显式快照为 `matched`，未提供
快照则仍为 `not_checked`。这些状态都不代表可执行。

`required_declarations_digest`、`host_declarations_digest` 分别覆盖完整的需求和快照
嵌套 registrations 对象，使用 SHA-256；每项摘要覆盖完整声明，包括 identity、contracts
或 schema。具体编码是先验证标准 JSON，再对
`json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)`
的 UTF-8 字节求摘要。对象键序规范化，数组顺序和数字表示保留，所以 `true`、`1`、`1.0`
互不相同。摘要不证明原始空白、源码、callable 身份、插件 archive 或实现 revision 的来源。
库存包含额外声明时，全部需求可以匹配，而两个完整库存摘要仍不同。

`HostDeclarationSnapshot`、`HostReadinessDiagnostic` 是冻结 DTO。`to_dict()` 和
`snapshot.registrations` 返回脱离内部状态的数据；修改原输入或返回的嵌套对象不会改变
已有观察。取快照不是对 Registration 并发修改的原子 cut；后续新增声明不在旧快照内。
`Registration.declarations()` 没返回的隐式机械 schema 不会被悄悄加载；只有提供的
惰性快照包含该声明时才可匹配，否则报告缺项。

## 惰性 JSON 与历史计划

已保存的声明对象可在不准备代码的情况下复制：

```python
from cpn.rpnh.collaboration.host_readiness import HostDeclarationSnapshot

saved = snapshot.to_dict()
restored = HostDeclarationSnapshot(saved["registrations"])
assert restored.declarations_digest == saved["declarations_digest"]
assert diagnose_host_requirements(required, restored).execution_ready is False
```

这只验证内容一致性。复制或调用方自行构造的 JSON 快照不是经认证的 HOST 证据，也不会
变成执行 capability。第二个参数接收快照 DTO，不接收任意报告 JSON。没有已准备快照时
就省略参数，保留 `not_checked`；不要为了得到好看的匹配报告而临时加载插件。

对于已有的已提交候选，传入其**精确** `VersionRef`，以及已经用对应 schema catalog
配置好的现有 Core：

```python
from cpn.rpnh.collaboration.host_readiness import diagnose_candidate_plan

# core 和 exact_plan_ref 来自已有 Registry 读取路径。
report = diagnose_candidate_plan(core, exact_plan_ref, snapshot).to_dict()
assert report["plan_ref"]["entity_type"] in {
    "collaboration_candidate_plan/v1", "collaboration_candidate_plan/v2"
}
assert report["execution_ready"] is False
```

包装函数将 v1 交给已有 `read_candidate_plan`，v2 交给已有
`read_preserved_candidate_plan`。这些 canonical 历史 reader 继续执行原有的精确字节、
依赖、离线验证与固定 cut 检查；包装函数不新增原始数据库 reader，不升级版本。报告带
`plan_ref`、`plan_sha256`，后者覆盖 reader 已验证的完整 canonical plan 字节。
材料缺失、畸形或不支持时保留 reader 的异常，不产出积极诊断。未知计划版本和 `latest`
一类别名会被拒绝。历史可读性不证明当前权限、首次准入、真实 HOST lower 或可采用性。

## 仍未验证的边界

每份报告固定 `execution_ready=false`、`permission=not_checked`、
`capacity=not_checked`、`reservation=not_reserved`。`callable_identity`、`lowering`、
`runtime_schema_authority` 也始终为 `not_checked`。

直接声明比较不验证 schema 运行兼容性，不读取 `$ref` 目标。即使带外部 schema 引用的
声明完全匹配，也不代表现有 Registry 运行合同支持它。诊断不检查凭据、不配置 provider、
不探测远端服务、不预约资源。另一个已有的 `cpn.rpnh.diagnostics.diagnose` 会执行已注册
analyzer，本切片明确不复用它。

本地接续应先使用真实观察到的需求及已准备的 HOST 数据。下一执行切片需要真实可信重编译
和完整 wire 比较、精确输入与权限核验，以及设计中的原子 registration/dispatch guard
和生命周期 pin。本声明快照不含 callable 或冻结执行选择，不能消除 rebind/check-use
裂缝，也不能开启多个 owner 的共享库存执行。图桥接、采用、恢复及真实 provider 验收
均未在本切片实现或证明。

## 离线验证

在装好测试依赖的 checkout 中执行：

```sh
python -m pytest -q tests/test_host_readiness.py
python -m pytest -q tests/test_candidate_plan_read_context.py tests/test_candidate_plan_offline.py tests/test_preserved_candidate_plan_reads.py
```

聚焦测试覆盖匹配、缺项、不匹配、未检查、畸形输入、不可变及深度脱离、哨兵 factory/callable
零调用、插件/网络/子进程禁用路径，以及不新增 Registry 对象或事件的真实 canonical v1/v2
读取。这些是离线机制证据，不是 benchmark、provider 或 execution-readiness 验收。
