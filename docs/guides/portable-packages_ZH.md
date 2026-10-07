---
name: rpnh-portable-packages
description: "在不执行包内容的前提下，预览有界纯数据包并生成精确本地依赖锁。"
metadata:
  document-kind: reference-guide
  audience: operator-and-developer
  language: zh-CN
  counterpart: portable-packages.md
  revision: "2026-10-06.1"
  status: source-reviewed-pre-release
---

[English](portable-packages.md) | [中文](portable-packages_ZH.md)

# 可移植包预览与精确本地锁

本页说明保留的 v1 合同。正式绑定的环境要求和版本化 v2 路径见[包环境声明](package-environments_ZH.md)。

首实现读取只含一个 closed Module 的纯数据 ZIP，并从明确提供的本地 ZIP 中解析精确依赖。
它不将包导入 Registry，不准备 HOST、不安装插件、不 lower/compile Module、不采用图、不执行
operation，也不联网获取材料或运行包内代码。所有结果明确包含 `execution_permitted=false`。

完整可移植包设计尚未成为安装协议。包 requirements 是惰性清单，不能直接作为 HOST readiness
检查需要的嵌套声明快照；本切片没有自动的 package→readiness 或 package→candidate 桥接。

## 完整离线样例

可分发夹具位于 `cpn/examples/portable_packages/minimal/`，包含完整 manifest、真实的
`rpnh/module_declaration/v1` 文档、两个应用 schema、清单内的 MIT 许可证，以及正确长度和摘要。
其中 `example/identity-*` 仅为符号 HOST 合同，未安装实现。预期：材料完整性、schema 闭包、自包含
Draft7 兼容性 satisfied；HOST readiness、许可证使用权限、来源权威、内容审查和撤销状态
not_checked。它不是可执行样例，也不证明 terminal 已成功。

先按[安装指南](installation_ZH.md)从包含本功能的已批准源码或 wheel 安装 RPNH。
在该 Python 环境中确认 `rpnh package --help` 可用，再进入一个专门保存输出的可写目录。
下列命令在当前目录生成 `portable-identity.zip` 和 `package-lock.json`，再次执行会覆盖同名文件。
使用标准库和 manifest 的显式允许清单生成确定性 ZIP：

```sh
python - <<'PY'
import json
from importlib.resources import files
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
root = files("cpn").joinpath("examples", "portable_packages", "minimal")
manifest = json.loads(root.joinpath("manifest.json").read_bytes())
paths = ["manifest.json", *[row["path"] for row in manifest["artifacts"]]]
with ZipFile("portable-identity.zip", "w") as archive:
    for path in sorted(paths):
        info = ZipInfo(path, (1980, 1, 1, 0, 0, 0))
        info.compress_type = ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, root.joinpath(*path.split("/")).read_bytes())
PY
rpnh package preview portable-identity.zip
rpnh package resolve portable-identity.zip --entry main > package-lock.json
```

有依赖时重复提供本地输入：

```sh
rpnh package resolve root.zip --local-package dependency-a.zip --local-package dependency-b.zip --entry main > package-lock.json
```

结构合法的 `preview` 输出 JSON 并返回 0，即使某项检查 missing/incompatible；必须读各项状态。
`resolve` 只有在包/schema 闭包完整且可解释时才返回 0，但运行兼容性仍可 incompatible。
包读取或解析失败以 JSON 写入 stderr，退出码 2；缺少参数、未知选项等 CLI 用法错误
由参数解析器输出普通文本，退出码同样为 2。命令不会写入包内容、解压文件或自行保存锁；上面的 shell
重定向才明确保存 stdout。

## 已实现 manifest 合同

ZIP 根必须存在 `manifest.json`，schema ID 为 `rpnh/share_package/v1`。
`cpn.rpnh.collaboration.share_packages.manifest_schema()` 返回本切片完整、独立的 JSON Schema
副本。下列顶层字段全部必填。manifest 及其合同记录拒绝未知字段，Module 机械字段同样拒绝未知项；
声明中 config 对象内的应用数据仍只是数据。

| 字段 | 实际含义 |
|---|---|
| `schema_version`, `package_id`, `version` | 固定 v1；带命名空间的小写包 ID；仅 `major.minor.patch`，不支持 prerelease/build |
| `entries` | 恰好一个 `closed_module`，含 entry ID、声明路径、固定声明 schema、输入/输出 schema ID、completion 合同 |
| `artifacts` | 除 manifest 外所有文件恰好一行：`path`, `media_type`, 整数 `bytes`, 原始字节 `sha256`, `role`, `license_id`, `disclosure` |
| `origin` | `repository_url`, `commit`, `publisher_claim`, `source_refs`；保留声明，不等于鉴真 |
| `provenance` | 每工件恰好一行：`artifact_path`, `relation`, 可空 `origin_ref`, 可空 `origin_digest` |
| `dependencies` | `dependency_id`, `package_id`, 可空 `manifest_digest`, 可空 `version_range`, `required=true`, 可空 `acquisition_hint` |
| `compatibility` | `declaration_schemas`, `runtime_contracts`, `host_contracts`；不产生执行权限 |
| `requirements` | 唯一 `requirement_id`，以及 `kind`, `contract_id`, `required`, `effects`, `data_classes` |
| `policy_surface` | 本切片必须为空，不修改 policy |
| `licenses` | 唯一 license ID、expression、非空 `text_paths`、`notice_paths`；引用非空 license 工件 |
| `disclosure` | `classification`, `intended_audience`, `excluded_categories`；作者声明，不是敏感数据扫描 |

支持 artifact role：`declaration`, `schema`, `knowledge`, `fixture`, `document`, `license`。
媒体类型为 `application/json`, `application/schema+json`, `text/plain`, `text/markdown`，全部
必须是 UTF-8 文本。每份 JSON 严格拒绝 BOM、重复键、NaN/Infinity、溢出为 infinity、尾随内容和
孤立 Unicode surrogate。

Completion 必须声明真实 success exit、全部 failure exits、必需 acceptor requirement ID，以及
空 `open_obligations`。静态检查与 Module 端口、operation/outcome products、terminal source
核对；不证明可达性、终止、无死锁或业务验收。Module required schemas 必须覆盖组件/端口 schema。
声明的 component、executor、tool、analyzer、terminal、effect key 必须列入必需 HOST requirements
与 compatibility 清单。本切片直接使用 key 作为 `contract_id`，不解析实现或 grant。

`origin_ref` 和 `source_refs` 保留 source-qualified version-reference 形状，包括 `source_id`
以及 `ref:{entity_type,logical_id,version_id}`。它们仍是未经验证的声明，不改写为本地 ancestry
或 Registry ref。`copied` 要求当前原始字节摘要等于 origin digest；`derived` 保留原摘要而当前
工件有新摘要。二者都不证明许可。仓库与获取提示若存在，只允许不带凭据、query、fragment 的 HTTPS
URL，绝不自动读取。

## 精确解析与摘要字节域

- `manifest_digest`：原始 `manifest.json` 字节的 SHA-256，包括空白
- `archive_digest`：完整 ZIP 字节的 SHA-256，包括压缩和容器元数据
- artifact `sha256`：原始解压文件字节的 SHA-256
- `package_lock_digest`：`PackageResolutionLock.to_bytes()` 的 SHA-256；排序键、ASCII escape、紧凑分隔符、无尾随换行的规范 JSON

`resolve` stdout 恰好为 `to_bytes()`，重定向保存后可直接计算 lock digest。摘要保存在锁外，不做
自引用；锁不含输入文件系统路径和时钟时间。相同精确输入产生相同锁字节。同 manifest 不同压缩容器
具有相同 manifest digest、不同 archive digest。root 保留调用者给定的容器；等价依赖 manifest
选择按字典序最小的 archive digest，不受输入顺序影响。

锁的 schema 为 `rpnh/package_resolution_lock/v1`，resolver 合同为 `rpnh/package_resolver/v1`。
它固定 root entry、含材料清单和原 provenance 的节点、全部启用依赖边、schema owner 与直接引用、
分开的兼容检查；selected features 为空。同一 package ID 只能选择一个 manifest digest。
本地缺依赖、身份不符、循环、选择冲突均失败。同 ID/version 不同内容报告 `VERSION_CONTENT_REPLACED`；
不同版本报告 `DEPENDENCY_VERSION_CONFLICT`。仅提供未使用候选不会替换已选择内容。

Range-only 依赖可预览，但 resolve 返回 `DEPENDENCY_RANGE_UNRESOLVED`。本切片也拒绝 digest+range
组合，因为没有 SemVer range solver，不假设范围已满足。不支持 optional dependency/feature selection，
不追随远端获取提示。

## 两个独立 schema 检查轴

`package_schema_closure_resolved` 表示必需材料与支持的引用能够离线解释。
`runtime_schema_authority_supported` 表示满足本切片对当前自包含 Draft7 reader 的保守静态兼容
范围；不创建真实 registered schema authority 或 HOST readiness attestation。

- 指向已识别 Draft7 schema 位置的本地 `#/...` 引用，包括 boolean schema 与本地递归结构，可满足两轴
- 跨文档/跨包引用可闭合 package graph，同时运行轴为 `incompatible/unsupported_schema_reference_contract`
- 缺目标材料或 pointer 为 `SCHEMA_CLOSURE_MISSING`，不调用网络 resolver
- 指向 `#/default`、`#/examples/0` 等 annotation 数据的引用，即使目标为对象，也不支持，不能生成精确锁
- 未知 dialect/keyword 和嵌套 `$id` scope 在此保守切片中 incompatible；不支持动态/递归 reference vocabulary
- 包 schema 不得覆盖权威 `PROTECTED_SCHEMA_REFS` 集合（包括两个 `runtime/llm_*_envelope/v1` 合同），也不得覆盖 `rpnh/`、`registry_v1/` 命名空间

Content schema 必须有规范显式 ID 和 Draft7 声明。不同包的同一 schema ID 必须对应完全相同字节。
不会自动内联、改写 ID 或放宽旧 reader。运行兼容轴还保守扫描 annotation 内引用，与现有 resource
reader 的递归扫描一致。闭合锁仍不代表允许运行。

## ZIP 与资源边界

不解压到文件系统。仅支持普通文件 ZIP member，stored 或 deflated 压缩。路径为最多 240 字符的
可移植 ASCII 相对名，拒绝路径穿越、绝对/drive 路径、空组件、反斜杠、Windows 保留名、尾随点和
大小写碰撞。拒绝 symlink、目录 member、加密、ZIP64、多卷、streaming data descriptor、前置
可执行内容、隐藏 local record、额外尾随字节。local metadata 必须与中央清单一致。

默认：32 MiB archive、每 expanded member 4 MiB（含 manifest）、每包展开总量 32 MiB、128 member、
最大压缩比 200:1、JSON 深度 64、100,000 JSON node。创建 `ZipInfo` 前检查中央目录，大小上限为每个
允许 member 1,024 字节；每 member extra/comment 元数据不超过 512 字节。

解析最多接收 64 份本地输入（包括 root）、256 依赖边、深度 16。输入 ZIP 总字节和展开材料总字节各有
独立 64 MiB 上限，未使用候选也计入；解压前应用剩余展开预算。`PackageLimits` 可施加更严格本地
上限，固定 manifest 合同仍保留自己的最大值。

这些是有界解析检查，不是恶意软件/秘密扫描、许可证裁定、来源鉴真或任意 PN 验证器。报告和锁包含
可能私密的作者元数据，分享前仍需审查。

## Python API 与验证范围

```python
from cpn.rpnh.collaboration.package_preview import preview_package
from cpn.rpnh.collaboration.package_resolution import resolve_package

preview = preview_package("portable-identity.zip")  # 也支持 ZIP bytes
report = preview.to_dict()                        # 独立可修改副本
lock = resolve_package(preview, [], root_entry_id="main")
lock_bytes = lock.to_bytes()
lock_digest = lock.package_lock_digest
```

结果记录 frozen，字段为不可变 bytes/tuple。resolve 对传入 preview 从原始 archive bytes 重新校验；
伪造报告不产生权威。`PackageError.code` 为稳定错误类别。

定向离线测试为 `tests/test_share_packages.py` 与 `tests/test_share_packages_resolution.py`，
覆盖完整夹具、资源上限、坏输入、零执行/网络/写入、原 provenance 保留、精确依赖冲突与 schema/运行
分轴。这不构成 package import、runtime adoption、远端获取或真实 provider 调用的验证证据。
