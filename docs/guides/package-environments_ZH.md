---
name: rpnh-package-environments
description: "惰性读取正式版本化的包环境要求，不安装或执行包内容。"
metadata:
  document-kind: reference-guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: package-environments.md
  revision: "2026-10-07.1"
  status: source-reviewed-pre-release
---

[English](package-environments.md) | [中文](package-environments_ZH.md)

# 包 v2 环境声明与精确目标

`rpnh/share_package/v2` ZIP 声明 closed Module 所需环境，并保留
[v1 ZIP 安全边界和精确依赖规则](portable-packages_ZH.md)。preview 和包解析仍为
惰性操作：不提取、不探测解释器、不加载插件、不联网获取、不安装、不写 Registry、
不运行业务。报告和锁始终包含 `execution_permitted=false`。

环境声明回答作者需要什么。本机选择、具体依赖解析、准备、HOST 装配与业务执行是
独立步骤。后续步骤及明确授权边界见[本机环境准备](../environment-preparation_ZH.md)。
声明检查通过不表示环境已准备或正在运行。

## 版本与兼容边界

v2 entry 新增必填 `environment_requirements_path`，必须指向 artifacts 中
`role=document`、`media_type=application/json` 的工件，具备字节数、原始字节
SHA-256、license、disclosure 和 provenance。环境文档的 `entry_id` 必须等于
manifest entry。不能使用未绑定的旁置文件、可执行定位器或包提供的脚本。

v2 流程使用 `rpnh/package_preview/v2`、`rpnh/package_preview_parser/v2`、
`rpnh/package_resolution_lock/v2` 与 `rpnh/package_resolver/v2`。v2 锁的每个
节点记录 `manifest_schema` 和 `environment_requirements`，后者含 entry、工件
路径、原始摘要和 schema 版本。选中的 v1 节点明确标记 `not_declared`。

v1 输入保留原有报告/锁版本及严格未知字段拒绝。v1 根不能选择 v2 依赖。v2 根可以
锁定 v1 材料，但自动环境准备对混合闭包返回 `ENVIRONMENT_REQUIREMENTS_UNDECLARED`。
未声明不等于空列表，也不根据某台机器碰巧能运行来补写作者要求。准备前须提供更新后
的正式包。显式旧版 reader `preview_package_v1` 拒绝 v2。

## 封闭的要求文档

文档 schema 为 `rpnh/environment_requirements/v1`，八个字段全部必填：
`schema_version`、`entry_id`、`python`、`distributions`、`system_requirements`、
`tools`、`plugins`、`services`。未知嵌套字段、重复 JSON 键、非有限数字、超限结构
均拒绝。每行都是必要要求；某类别为空仅表示作者未在该类声明额外要求，不证明远程
服务可用。

最小示例：

```json
{
  "schema_version": "rpnh/environment_requirements/v1",
  "entry_id": "main",
  "python": {
    "requirement_id": "python",
    "implementation": "cpython",
    "version_specifier": ">=3.11,<4"
  },
  "distributions": [{
    "requirement_id": "harness",
    "name": "rpnh-harness",
    "version_specifier": ">=0.1.0rc1,<1",
    "satisfies_host_requirement_ids": []
  }],
  "system_requirements": [],
  "tools": [],
  "plugins": [],
  "services": []
}
```

- Python 支持 `cpython` 和明确 PEP 440 约束。distribution 名称为规范化发行包名，
  不是 import 名称；必须包含 `rpnh-harness`。禁止 URL/VCS/path/editable 目标和
  安装参数。版本范围是要求，不是具体安装选择
- 系统行是封闭的 `platform`、`capability`、`os_package` union。操作系统/架构声明
  不证明 RPNH 支持该平台；当前 runtime 支持仍为 Linux/WSL2。能力约束为惰性标量
  或 `{operator,value}`，operator 为 `present`、`eq`、`gte`、`lte`；仅已安装可信
  adapter 可以解释。未知合同或版本协议为 unsupported
- 工具声明 `tool_contract_id`、`version_constraint`、`required_capabilities` 和
  HOST 要求映射，不包含本机 executable 路径
- 插件声明 `plugin_id`、`api_contract`、`version_specifier` 与本文档内的
  `distribution_requirement_id`，不接受 entry-point/import 定位器。安装、metadata
  可见、可信装配和 operation 成功是不同事实
- 服务声明 `service_contract_id`、capabilities、认证需要及可空的 model constraint。
  exact model condition 为有界字符串；浮动 `latest`、`default`、`auto`、`:latest`
  不能作为精确选择。capabilities 约束列出 `contract_ids`。本机 profile、账号和
  credential 引用不属于共享文档

同一 entry 的所有类别共用唯一 requirement ID。完整身份为
`(manifest_digest, entry_id, requirement_id)`；不同包的相同短 ID 保持独立。
`satisfies_host_requirement_ids` 只引用同一 manifest 的平面 HOST 要求，不授权
加载或执行。集合按 requirement ID 遍历，作为集合的合同/ID 列表排序；这些语义
排序不改变原始工件 bytes 的摘要域。

## 读取精确选中的闭包

安装后入口仍为 `rpnh package preview` / `rpnh package resolve`：

```sh
rpnh package preview root.zip
rpnh package resolve root.zip --local-package dependency.zip --entry main > package-lock.json
```

resolve 将规范锁 bytes 输出到 stdout，不增加换行。不要重排已保存的锁并认为摘要
未变。下面 Python API 会重新验证精确 archive、manifest、artifact 和 lock，
不会信任保存的“preview passed”报告：

```python
from pathlib import Path
from cpn.rpnh.collaboration.share_packages import PackageResolutionLock
from cpn.rpnh.collaboration.environment_requirements import read_environment_requirements

lock = PackageResolutionLock(Path("package-lock.json").read_bytes())
requirements = read_environment_requirements(
    lock, [Path("root.zip"), Path("dependency.zip")], entry_id="main"
)
target = requirements.target.to_dict()
```

只有锁选中的 archive 参与要求与 schema 闭包。未使用的 catalog 包不增加 Python
约束、schema 或隐式依赖。即使重新压缩后 manifest bytes 不变，替换选中的 archive
也会被拒绝。共享包依赖仍按精确 manifest 摘要锁定；环境版本范围不引入共享包
SemVer 求解器。

`PackageTarget` 包含 `package_lock_digest`、`root_manifest_digest`、
`root_archive_digest`、`entry_id`、有序 `requirement_artifacts` 和
`requirements_digest`。每个工件行含 `manifest_digest`、`entry_id`、
`artifact_path`、`artifact_digest`。`requirements_digest` 对规范有序清单取摘要，
不对猜测合并后的要求文件取摘要。每个原始工件摘要仍独立验证。

manifest/archive/artifact 摘要分别对应自己的原始 bytes；lock 和本机 DTO 摘要
对应规范 bytes，并保存在文档外，避免自引用。它们都不是 Registry exact ref。
只有经过合法 owner 出版，要求文档或去敏准备报告才拥有 Registry 引用。本机路径和
binding digest 不是 Viewer 公共身份。

## HOST 诊断与内部端口

`diagnose_package_host_requirements(requirements, snapshot)` 接收已验证要求和
已装配 HOST 的惰性 `HostDeclarationSnapshot`，检查所需 kind/key 是否存在及必要
schema 规范内容。`terminal` 映射到 Registration 的 `tool`；`effect` 仍是独立
runtime 权限检查。它不创建 Registration、不加载插件、不 compile、不授予权限。

没有完整作者 expected declarations 时，报告保留
`author_implementation_identity=not_supplied`；本机快照与自身相符不能证明作者
实现身份。即使 `declarations_status=matched`，仍保留
`contract_compatibility=requires_compilation`、permission/capacity 未检查、
`execution_ready=false`。实际运行前必须由选定可信 HOST 的 Module compile
验证 operation 合同。

v2 允许 operation input 由可信 component 的内部端口提供，例如 native plugin
capability。preview 不能证明端口存在，明确将内部端口绑定保留为未检查。不能把
这些端口当成未声明公共边界；公共端口方向错误、operation output 缺失或 terminal
端点错误仍会拒绝。实际可信 lowering/compiler 检查不可省略。v1 原有较严格 parser
行为保持不变。

## 验证范围

确定性包测试覆盖 v1 兼容、v2 原始字节身份、所选闭包、命名空间隔离、未声明 v1
节点、畸形字段、不安全引用、不可变 DTO、native 内部输入边界，以及零探测/加载/
网络/Registry 行为。它们不证明发行安装验收、所选解释器业务成功、native worker
完成或远程 provider 可用。这些都需要针对同一精确 package/lock/entry 的独立准备
与实际运行证据。
